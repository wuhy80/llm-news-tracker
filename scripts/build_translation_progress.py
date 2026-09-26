"""Build site-wide translation coverage without making model/network requests."""
from pathlib import Path
from article_store import ROOT, utc_now
from news_store import load_news, atomic_write_json
from translate_articles import (read_json, article_blocks, translatable_blocks, mostly_english,
                                body_hash, TRANSLATION_VERSION, READABLE_KINDS)


def build_progress(root: Path) -> dict:
    items = {str(item['id']): item for item in load_news(root / 'data/news.json').get('items', [])}
    snapshots = {}
    for path in sorted((root / 'data/articles').rglob('*.json')):
        snapshot = read_json(path)
        if snapshot and snapshot.get('id'):
            ident = str(snapshot['id'])
            snapshots[ident] = (snapshot, path.relative_to(root / 'data/articles'))
            items.setdefault(ident, snapshot)
    counts = dict(total=len(items), complete=0, partial=0, pending=0, notRequired=0, awaitingBody=0)
    blocks_total = blocks_done = eligible = automatic = stale = errors = 0
    for ident, item in items.items():
        snapshot, relative = snapshots.get(ident, ({}, None))
        body = str(snapshot.get('body') or '')
        if snapshot.get('contentKind') not in READABLE_KINDS or not body.strip():
            counts['awaitingBody'] += 1
            continue
        blocks = translatable_blocks(article_blocks(body))
        if not mostly_english(body) or not blocks:
            counts['notRequired'] += 1
            continue
        eligible += 1
        if (item.get('aiReview') or {}).get('importanceLevel') in {4, 5}:
            automatic += 1
        expected = {block['id']: block['sourceHash'] for block in blocks}
        record = read_json(root / 'data/translations/zh-CN' / relative) or {}
        valid = (record.get('articleId') == ident and record.get('targetLanguage') == 'zh-CN'
                 and record.get('translationVersion') == TRANSLATION_VERSION
                 and record.get('sourceBodyHash') == body_hash(body))
        done = set()
        if valid:
            for block in record.get('blocks', []):
                if isinstance(block, dict) and block.get('id') in expected and str(block.get('translationZh') or '').strip():
                    if block.get('sourceHash') == expected[block['id']]:
                        done.add(block['id'])
            if record.get('lastError') and len(done) < len(expected):
                errors += 1
        elif record:
            stale += 1
        blocks_total += len(expected)
        blocks_done += len(done)
        counts['complete' if len(done) == len(expected) else 'partial' if done else 'pending'] += 1
    return {'schemaVersion': 1, 'generatedAt': utc_now(), 'scope': 'all-documents', 'documents': counts,
            'eligibleDocuments': eligible, 'automaticEligibleDocuments': automatic,
            'translatedBlocks': blocks_done, 'totalBlocks': blocks_total,
            'documentPercent': round(counts['complete'] * 100 / eligible, 2) if eligible else 0,
            'blockPercent': round(blocks_done * 100 / blocks_total, 2) if blocks_total else 0,
            'staleDocuments': stale, 'errorDocuments': errors}


if __name__ == '__main__':
    result = build_progress(ROOT)
    atomic_write_json(ROOT / 'data/translations/progress.json', result)
    print(result)
