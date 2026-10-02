#!/usr/bin/env python3
"""Resumable repair across ALL HF archive years, including summary-only snapshots."""
import argparse
import json
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from article_store import ROOT, BODY_FORMAT_VERSION, MEDIA_FORMAT_VERSION, fetch_page_content, take_media_source, utc_now
from huggingface_content import CONTENT_VERSION, extract, is_huggingface_blog, remap_translation
from news_store import atomic_write_json


def repair_one(path, document, root=ROOT):
    previous = json.loads(path.read_text(encoding='utf-8'))
    content = extract(document, previous['url'])
    translated = None
    target = root / 'data/translations/zh-CN' / path.relative_to(root / 'data/articles')
    if target.exists():
        old = json.loads(target.read_text(encoding='utf-8'))
        translated = remap_translation(old, previous.get('body', ''), content['body'])
    updated = {**previous, **content, 'contentKind': 'page', 'articleKind': 'page',
               'bodyFormatVersion': BODY_FORMAT_VERSION, 'mediaFormatVersion': MEDIA_FORMAT_VERSION,
               'hfContentCheckedAt': utc_now(), 'mediaLayoutCheckedAt': utc_now()}
    updated.pop('note', None)
    atomic_write_json(path, updated)
    if translated is not None:
        atomic_write_json(target, translated)
    # Refresh only this record's lightweight index, without regenerating unrelated archives.
    relative = path.relative_to(root / 'data/articles')
    day = root / 'data/news' / relative.parts[0] / relative.parts[1] / (relative.parts[2] + '.json')
    if day.exists():
        shard = json.loads(day.read_text(encoding='utf-8'))
        for item in shard.get('items', []):
            if item.get('id') == updated['id']: item['articleKind'] = 'page'
        atomic_write_json(day, shard)
    return {'images': len(content['images']), 'restoredBody': previous.get('contentKind') == 'summary',
            'translatedBlocksKept': len(translated['blocks']) if translated else 0}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--cache-dir', type=Path)
    p.add_argument('--offline', action='store_true')
    p.add_argument('--limit', type=int, default=80)
    p.add_argument('--retry', action='store_true')
    args = p.parse_args()
    directory = ROOT / 'data/articles'; state_path = ROOT / 'data/huggingface-repair-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'version': CONTENT_VERSION, 'failures': {}}
    failures = state.setdefault('failures', {})
    rows = []
    for path in directory.rglob('*.json'):
        snapshot = json.loads(path.read_text(encoding='utf-8'))
        if is_huggingface_blog(snapshot.get('url')): rows.append((path, snapshot))
    rows.sort(key=lambda row: row[1].get('publishedAt', ''), reverse=True)
    attempted = repaired = restored = 0; now = utc_now()
    for path, snapshot in rows:
        if snapshot.get('hfContentVersion') == CONTENT_VERSION: continue
        key = path.relative_to(directory).as_posix()
        cache = args.cache_dir / (snapshot['id'] + '.html') if args.cache_dir else None
        if not args.retry and failures.get(key, {}).get('nextAttemptAt', '') > now: continue
        if args.offline and (not cache or not cache.exists()): continue
        if not args.offline and attempted >= args.limit: break
        attempted += 1
        try:
            if cache and cache.exists(): document = cache.read_text(encoding='utf-8')
            else:
                if attempted > 1: time.sleep(3.3)
                _, resolved, _ = fetch_page_content(snapshot['url'])
                source = take_media_source(resolved)
                if not source or source[1] != 'html': raise ValueError('source HTML unavailable')
                document = source[0]
            result = repair_one(path, document)
            repaired += 1; restored += int(result['restoredBody']); failures.pop(key, None)
            if repaired % 25 == 0: print(f'[hf] repaired {repaired} this batch', flush=True)
        except Exception as error:
            limited = isinstance(error, urllib.error.HTTPError) and error.code == 429
            delay = timedelta(minutes=10) if limited else timedelta(days=1)
            failures[key] = {'attemptedAt': utc_now(), 'error': str(error)[:240],
                             'nextAttemptAt': (datetime.now(timezone.utc)+delay).isoformat().replace('+00:00', 'Z')}
            print(f'[hf] {snapshot["id"]}: {type(error).__name__}: {str(error)[:160]}', flush=True)
            if limited: break
    complete = sum(json.loads(path.read_text()).get('hfContentVersion') == CONTENT_VERSION for path, _ in rows)
    state.update({'version': CONTENT_VERSION, 'updatedAt': utc_now(), 'total': len(rows), 'repaired': complete,
                  'remaining': len(rows)-complete, 'lastBatch': {'attempted': attempted, 'repaired': repaired, 'restoredBodies': restored}})
    atomic_write_json(state_path, state)
    if repaired:
        from translate_articles import build_translation_index
        from build_translation_progress import build_progress
        atomic_write_json(ROOT / 'data/translations/index.json', build_translation_index())
        atomic_write_json(ROOT / 'data/translations/progress.json', build_progress(ROOT))
    print(f'[hf] total={len(rows)} repaired={complete} remaining={len(rows)-complete}; restored summaries={restored}')
    return 0


if __name__ == '__main__': raise SystemExit(main())
