"""Bounded follow-up dispatch after committed translation progress.

This supplements delayed cron delivery; it is not an independent scheduler.
Never continue an empty, failed-only, cooling-down, or stale run.
"""
import json
import os
import re
import urllib.request
from pathlib import Path


def continuation_inputs(runtime, inputs, run_url):
    try:
        depth = int(inputs.get('continuation_depth', '0'))
    except (ValueError, TypeError):
        return None
    if (not 0 <= depth < 3 or runtime.get('runUrl') != run_url
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


def main():
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or not run_id.isdigit():
        raise ValueError('An Actions repository and run ID are required')
    path = Path(__file__).resolve().parents[1] / 'data/translations/runtime.json'
    if not path.exists():
        print('[translate:continue] no current runtime; skipped')
        return
    inputs = continuation_inputs(json.loads(path.read_text()),
        json.loads(os.environ.get('CONTINUATION_INPUTS', '{}')),
        f'https://github.com/{repo}/actions/runs/{run_id}')
    if inputs is None:
        print('[translate:continue] no eligible budget-limited progress, or chain cap reached')
        return
    request = urllib.request.Request(
        f'https://api.github.com/repos/{repo}/actions/workflows/translate-articles.yml/dispatches',
        data=json.dumps({'ref': 'main', 'inputs': inputs}).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
                 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json',
                 'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 204:
            raise RuntimeError('Continuation dispatch was not accepted')
    print('[translate:continue] requested follow-up ' + inputs['continuation_depth'] + '/3')


if __name__ == '__main__':
    main()
