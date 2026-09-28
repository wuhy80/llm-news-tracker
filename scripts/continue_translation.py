"""Bounded batches inside the existing Actions job; no workflow dispatch.

This supplements delayed cron delivery; it is not an independent scheduler.
Never continue an empty, failed-only, cooling-down, or stale run.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def continuation_inputs(runtime, inputs, run_url):
    try:
        depth = int(inputs.get('continuation_depth', '0'))
    except (ValueError, TypeError):
        return None
    if (not 0 <= depth < 2 or runtime.get('runUrl') != run_url
            or runtime.get('status') != 'finished'):
        return None
    eligible = any(
        state.get('runStatus') == 'progress'
        and state.get('runStopReason') in ('request_limit', 'time_limit')
        and (state.get('runModelBlocks') or 0) > 0
        for state in runtime.get('providers', {}).values())
    if not eligible:
        return None
    result = {key: str(inputs[key]) for key in
              ('request_limit', 'daily_limit', 'interval_seconds', 'model')
              if inputs.get(key) not in (None, '')}
    result['continuation_depth'] = str(depth + 1)
    return result


def checkpoint(root):
    """Use the existing contents permission to persist quota/progress each batch."""
    def git(*args, check=True):
        return subprocess.run(['git', *args], cwd=root, check=check)
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('add', 'data/translations')
    changed = git('diff', '--cached', '--quiet', check=False).returncode
    if changed == 1:
        git('commit', '-m', 'chore: checkpoint translation batch and quota')
    elif changed:
        raise RuntimeError('Cannot inspect translation checkpoint')
    for _ in range(3):
        git('pull', '--rebase', 'origin', 'main')
        if git('push', 'origin', 'HEAD:main', check=False).returncode == 0:
            return
    raise RuntimeError('Cannot persist translation quota; stopping batches')


def main():
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or not run_id.isdigit():
        raise ValueError('An Actions repository and run ID are required')
    root = Path(__file__).resolve().parents[1]
    path = root / 'data/translations/runtime.json'
    run_url = f'https://github.com/{repo}/actions/runs/{run_id}'
    result = 0
    for batch in range(3):
        print(f'[translate:batch] starting {batch + 1}/3', flush=True)
        code = subprocess.run([sys.executable, str(root / 'scripts/translate_articles.py')], cwd=root).returncode
        result = max(result, int(code != 0))
        # Save even a failed batch's accepted translations and consumed quota.
        # A checkpoint failure raises before any further API calls.
        validation = subprocess.run([sys.executable, str(root / 'scripts/validate_translations.py')], cwd=root).returncode
        checkpoint(root)
        if validation:
            return 1
        runtime = json.loads(path.read_text()) if path.exists() else {}
        if continuation_inputs(runtime, {'continuation_depth': str(batch)}, run_url) is None:
            print('[translate:batch] no productive budget-limited work, or batch cap reached')
            break
    return result


if __name__ == '__main__':
    raise SystemExit(main())
