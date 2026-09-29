"""Durable API token accounting, separate from admission/quota reservations."""
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from news_store import atomic_write_json

LEDGER = Path(__file__).resolve().parents[1] / 'data/translations/token-usage.json'
PURPOSE = ContextVar('token_usage_purpose', default='translation')
LOCK = threading.RLock()


def normalize_usage(value):
    if not isinstance(value, dict):
        return None
    prompt = value.get('prompt_tokens', value.get('promptTokenCount'))
    completion = value.get('completion_tokens', value.get('candidatesTokenCount'))
    total = value.get('total_tokens', value.get('totalTokenCount'))
    if any(type(n) is not int or n < 0 for n in (prompt, completion, total)):
        return None
    if total < prompt + completion:
        return None
    return prompt, completion, total


@contextmanager
def usage_purpose(name):
    token = PURPOSE.set(name)
    try:
        yield
    finally:
        PURPOSE.reset(token)


def update(day, provider, model, purpose, *, started=False, usage=None):
    with LOCK:
        data = json.loads(LEDGER.read_text()) if LEDGER.exists() else {
            'schemaVersion': 1, 'timezone': 'Asia/Shanghai', 'days': {}}
        row = data['days'].setdefault(day, {}).setdefault(provider, {}).setdefault(model, {}).setdefault(purpose, {
            'requests': 0, 'knownUsageRequests': 0, 'unknownUsageRequests': 0,
            'inputTokens': 0, 'outputTokens': 0, 'totalTokens': 0})
        if started:
            row['requests'] += 1
            row['unknownUsageRequests'] += 1
        elif usage is not None:
            row['knownUsageRequests'] += 1
            row['unknownUsageRequests'] -= 1
            row['inputTokens'] += usage[0]
            row['outputTokens'] += usage[1]
            row['totalTokens'] += usage[2]
        data['updatedAt'] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        atomic_write_json(LEDGER, data)


@contextmanager
def track_request(provider, model, now=None):
    # Persist an unknown request BEFORE sending: interrupted calls are not lost.
    day = (now or datetime.now(ZoneInfo('Asia/Shanghai'))).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    purpose = PURPOSE.get()
    update(day, provider, model, purpose, started=True)
    result = {}
    try:
        yield result
    finally:
        usage = normalize_usage(result.get('usage'))
        if usage is not None:
            update(day, provider, model, purpose, usage=usage)


def report(data, day):
    lines = [f'### Token 用量：{day}（北京时间）', '',
             '仅汇总 API 返回的实际用量；未知用量不计作零。按请求开始日期、请求模型归属，包含未通过译文校验的响应。', '',
             '| 供应商 | 模型 | 用途 | 请求 | 已知 / 未知用量 | 输入 | 输出 | 总 token |',
             '|---|---|---|---:|---:|---:|---:|---:|']
    for provider, models in sorted(data.get('days', {}).get(day, {}).items()):
        totals = {k: 0 for k in ('requests', 'knownUsageRequests', 'unknownUsageRequests', 'inputTokens', 'outputTokens', 'totalTokens')}
        for model, purposes in sorted(models.items()):
            for purpose, r in sorted(purposes.items()):
                for key in totals:
                    totals[key] += r[key]
                lines.append(f"| {provider} | {model} | {purpose} | {r['requests']} | {r['knownUsageRequests']} / {r['unknownUsageRequests']} | {r['inputTokens']:,} | {r['outputTokens']:,} | {r['totalTokens']:,} |")
        r = totals
        lines.append(f"| **{provider} 合计** | — | 全部 | {r['requests']} | {r['knownUsageRequests']} / {r['unknownUsageRequests']} | {r['inputTokens']:,} | {r['outputTokens']:,} | **{r['totalTokens']:,}** |")
    if len(lines) == 6:
        lines.append('当天尚无用量记录。历史缺失用量不会自动估算或补零。')
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat())
    args = parser.parse_args()
    print(report(json.loads(LEDGER.read_text()) if LEDGER.exists() else {}, args.date))
