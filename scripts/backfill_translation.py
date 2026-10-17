"""Backfill translations oldest-first, independently of the importance queue.

Same prompts, chunking, validation and record format as the scheduled worker —
this driver only changes the ordering (publishedAt ascending) and keeps its own
rate-control file so it never contends with production's pacing state.
"""
import argparse
import os
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from news_store import load_news, atomic_write_json
from api_rate_control import RateControl
from translate_articles import (PROVIDERS, SYSTEM_PROMPT, article_blocks, translatable_blocks,
                                mostly_english, load_record, pending_chunk, completed_ids,
                                reference_translation, apply_chunk, defer_blocks, request_translation,
                                normalize_partial_response, error_message, snapshot_path,
                                read_json, utc_now, TRANSLATIONS_DIR)

BACKLOG_MODEL = 'gpt-6.1-sol'


def eligible_items(root: Path) -> list[tuple[dict, dict, list[dict[str, str]]]]:
    """Every translatable importance-4/5 article, oldest publication first."""
    data = load_news(root / 'data/news.json', root / 'data/articles', hydrate=True)
    items = []
    for item in data.get('items', []):
        if (item.get('aiReview') or {}).get('importanceLevel') not in (4, 5):
            continue
        try:
            snapshot = read_json(snapshot_path(item))
        except ValueError:
            continue
        if not snapshot or snapshot.get('contentKind') not in {'community', 'feed', 'page', 'reader'}:
            continue
        body = str(snapshot.get('body') or '')
        if not mostly_english(body):
            continue
        blocks = translatable_blocks(article_blocks(body))
        if blocks:
            items.append((item, snapshot, blocks))
    items.sort(key=lambda entry: (str(entry[0].get('publishedAt') or ''), entry[0]['id']))
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-requests', type=int, default=int(os.getenv('BACKLOG_MAX_REQUESTS', '400')))
    parser.add_argument('--chunk-chars', type=int, default=3500)
    parser.add_argument('--chunk-blocks', type=int, default=8)
    parser.add_argument('--interval', type=float, default=0.0)
    args = parser.parse_args()

    token = os.getenv('NOWCODING_API_KEY', '').strip()
    if not token:
        print('::error::NOWCODING_API_KEY is not configured')
        return 1
    root = Path(__file__).resolve().parents[1]
    endpoint = PROVIDERS['nowcoding']['endpoint']
    # A private rate file: production paces itself on rate-nowcoding.json and
    # must never see this worker's request timestamps.
    control = RateControl(root / 'data/translations/rate-nowcoding-backlog.json', 'nowcoding', args.interval)

    requested = 0
    translated_blocks = 0
    completed_articles = 0
    deferred_blocks = 0
    now = datetime.now(timezone.utc)
    for item, snapshot, blocks in eligible_items(root):
        if requested >= args.max_requests:
            # Stopped with work in sight: the workflow chains another run.
            print(f'[backfill] stopped at the request cap with remaining articles', flush=True)
            if os.getenv('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as handle:
                    handle.write('chain=true\n')
            break
        path, record = load_record(item, snapshot, blocks)
        if record.get('status') == 'complete':
            continue
        chunk = pending_chunk(blocks, record, max(500, args.chunk_chars), max(1, args.chunk_blocks))
        if not chunk:
            expected = {b['id'] for b in translatable_blocks(blocks)}
            if expected and expected.issubset(completed_ids(record)):
                record['status'] = 'complete'
                record['translatedBlocks'] = record.get('totalBlocks', 0)
                record['completedAt'] = utc_now()
                record['updatedAt'] = record['completedAt']
                atomic_write_json(path, record)
                completed_articles += 1
            continue

        references = [b for b in chunk if reference_translation(b['source'])]
        if references:
            localized = [{'id': b['id'], 'kind': b['kind'], 'sourceHash': b['sourceHash'],
                          'translationZh': reference_translation(b['source']), 'provider': 'local',
                          'model': 'reference-labels-v1'} for b in references]
            apply_chunk(record, localized, [], record.get('model') or 'reference-labels-v1')
            translated_blocks += len(localized)
            atomic_write_json(path, record)
            continue

        control.acquire(BACKLOG_MODEL, token)
        requested += 1
        try:
            payload, actual_model, _ = request_translation(token, BACKLOG_MODEL, chunk, endpoint)
            translated, word_wise, failures = normalize_partial_response(payload, chunk)
            for block in translated:
                block.update(provider='nowcoding', model=actual_model)
            if translated:
                apply_chunk(record, translated, word_wise, actual_model)
                record['provider'] = 'nowcoding'
                translated_blocks += len(translated)
            if failures:
                defer_blocks(record, failures, actual_model)
                deferred_blocks += len(failures)
            atomic_write_json(path, record)
            print(f"[backfill:{record['status']}] {item['id']} "
                  f"{record.get('translatedBlocks', 0)}/{record.get('totalBlocks', '?')} blocks via {actual_model}", flush=True)
        except urllib.error.HTTPError as error:
            message = error_message(error)
            if error.code in {401, 403}:
                print(f'::error::backfill authentication failure: {message[:200]}')
                return 1
            # Server-side trouble is transient: wait it out, the chunk retries
            # on the next pass because the record was not advanced.
            print(f'[backfill:warn] {item["id"]}: HTTP {error.code}; backing off', flush=True)
            time.sleep(60 if error.code in {429} else 20)
        except Exception as error:
            print(f'[backfill:warn] {item["id"]}: {error_message(error)[:160]}', flush=True)
            time.sleep(20)

    print(f'[backfill] requests {requested}/{args.max_requests}; translated {translated_blocks} blocks; '
          f'completed {completed_articles} articles; deferred {deferred_blocks}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
