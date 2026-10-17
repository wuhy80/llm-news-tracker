"""Use authenticated maintainer GitHub issues as a durable priority queue."""
import json
import os
import re
import urllib.request
import urllib.error
from pathlib import Path
from article_store import ROOT, utc_now
from news_store import atomic_write_json

QUEUE_FILE = ROOT / 'data/translations/queue.json'
REPOSITORY = 'wuhy80/llm-news-tracker'
TITLE = re.compile(r'^\[优先翻译\]\s+([0-9a-f]{12})$')


def github_get(path, token):
    request = urllib.request.Request('https://api.github.com' + path, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        'User-Agent': 'llm-news-translation-queue', 'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def authorized_requests(issues, permission):
    """Newest request first; duplicate requests cannot create duplicate work."""
    result = {}
    for issue in sorted(issues, key=lambda row: int(row.get('number', 0)), reverse=True):
        match = TITLE.fullmatch(str(issue.get('title', '')))
        if not match or issue.get('pull_request') or issue.get('state') != 'open':
            continue
        login = (issue.get('user') or {}).get('login', '')
        if not re.fullmatch(r'[A-Za-z0-9-]+', login) or permission(login) not in {'admin', 'maintain', 'write'}:
            continue
        ident = match.group(1)
        if ident not in result:
            result[ident] = {'articleId': ident, 'issueNumber': issue['number'],
                             'requestedAt': issue.get('created_at'), 'status': 'queued',
                             'issueUrl': f'https://github.com/{REPOSITORY}/issues/{issue["number"]}'}
    return result


def sync_requests():
    token = os.getenv('GITHUB_TOKEN', '').strip()
    if not token:
        # Do not read an unverified caller-provided priority list.
        return {}
    issues = []
    for page in range(1, 101):
        batch = github_get(f'/repos/{REPOSITORY}/issues?state=open&sort=created&direction=desc&per_page=100&page={page}', token)
        issues.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise RuntimeError('Priority issue pagination exceeded safe bound')
    permissions = {}
    def permission(login):
        if login not in permissions:
            try:
                data = github_get(f'/repos/{REPOSITORY}/collaborators/{login}/permission', token)
                permissions[login] = data.get('permission', '')
            except urllib.error.HTTPError as error:
                if error.code not in {403, 404}:
                    raise
                permissions[login] = ''
        return permissions[login]
    return authorized_requests(issues, permission)


def resolve_items(items, requests):
    by_id = {item['id']: item for item in items}
    missing = set(requests) - set(by_id)
    if missing:
        for path in sorted((ROOT / 'data/articles').rglob('*.json')):
            if path.stem not in missing:
                continue
            record = json.loads(path.read_text(encoding='utf-8'))
            if record.get('id') == path.stem:
                by_id[path.stem] = record
    for ident, request in requests.items():
        if ident not in by_id:
            request['status'] = 'not_found'
    return list(by_id.values())


def publish_queue(requests, items, paused_until=None):
    # Import lazily to avoid a module initialization cycle.
    from translate_articles import (read_json, snapshot_path, article_blocks, translatable_blocks,
                                    load_record, completed_ids, mostly_english, READABLE_KINDS)
    by_id = {item['id']: item for item in items}
    position = 0
    for ident, request in requests.items():
        item = by_id.get(ident)
        if not item:
            request['status'] = 'not_found'
            continue
        snapshot = read_json(snapshot_path(item))
        if not snapshot or snapshot.get('contentKind') not in READABLE_KINDS:
            request['status'] = 'awaiting_body'
            continue
        body = str(snapshot.get('body', ''))
        blocks = article_blocks(body)
        expected = {b['id'] for b in translatable_blocks(blocks)}
        if not mostly_english(body) or not expected:
            request['status'] = 'not_required'
            continue
        _, record = load_record(item, snapshot, blocks)
        count = len(expected & completed_ids(record))
        request.update({'translatedBlocks': count, 'totalBlocks': len(expected)})
        if count == len(expected):
            request['status'] = 'complete'
        else:
            position += 1
            request['position'] = position
            request['status'] = 'retry_wait' if record.get('nextAttemptAt') else 'partial' if count else 'queued'
            if record.get('nextAttemptAt'):
                request['nextAttemptAt'] = record['nextAttemptAt']
    atomic_write_json(QUEUE_FILE, {'schemaVersion': 1, 'generatedAt': utc_now(),
                                  'pausedUntil': paused_until, 'articles': requests})
