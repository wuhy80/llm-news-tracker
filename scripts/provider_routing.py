"""Daily small-sample evaluation and auditable provider/model ordering.

Scores are automatic fidelity/format proxies, not human semantic-quality grades.
No external judge, arbitrary model discovery, or additional account keys.
"""
import json
import math
import os
import re
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from api_rate_control import RateControl, RateLimited, timestamp
from news_store import atomic_write_json
from provider_limits import TokenRateControl, RequestTooLarge
from translation_models import BigModelPool, ModelPool

PROVIDERS = {
    'bigmodel': {'key': 'BIGMODEL_API_KEY', 'models': ['glm-4.7-flash'],
                 'endpoint': 'https://open.bigmodel.cn/api/paas/v4/chat/completions'},
    'gemini': {'key': 'GEMINI_API_KEY', 'models': ['gemini-3.5-flash-lite', 'gemini-3.8-flash'],
               'endpoint': 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'},
    'groq': {'key': 'GROQ_API_KEY', 'models': ['qwen/qwen3.8-27b', 'openai/gpt-oss-120b'],
             'endpoint': 'https://api.groq.com/openai/v1/chat/completions'},
    'openrouter': {'key': 'OPENROUTER_API_KEY', 'models': [],
                   'endpoint': 'https://openrouter.ai/api/v1/chat/completions'},
}
# Same short, trusted fixture for every model. Never follow article instructions.
FIXTURE = [
    ('The retry budget is 3 requests, not 30. HTTP 429 does not mean the API key is invalid.',
     ['3', '30', '429', 'API'], [r'不|并非|而非|非']),
    ('Keep /data/models unchanged. Run kubectl get pods before restarting Kubernetes. Documentation: https://example.com/guide',
     ['/data/models', 'kubectl get pods', 'Kubernetes', 'https://example.com/guide'], [r'之前|以前|前', r'不变|保持|保留']),
    ('In this experiment, batching reduced latency by 25%, but increased memory use. This result does not prove that every workload is faster.',
     ['25%'], [r'延迟|时延', r'内存', r'增加|增大|提高|更多|上升', r'不|并非|未']),
]


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def output_limit(provider):
    return 2048 if provider in ('gemini', 'groq') else 8000


def reservation(prompt, chunk, provider):
    # UTF-8 bytes are a deliberately conservative token upper estimate. Include
    # serialization/roles and the full output cap (including reasoning tokens).
    source = json.dumps({'blocks': [{k: b[k] for k in ('id', 'kind', 'source')} for b in chunk]}, ensure_ascii=False)
    input_bound = len((prompt + source).encode('utf-8')) + 256
    return input_bound + (output_limit(provider) if provider == 'groq' else 0)


class DirectModelPool(BigModelPool):
    def __init__(self, path, model):
        super().__init__(path)
        self.model = model
        if self.health.get('model') != model:
            self.health = {}


def record_attempt(root, provider, model, accepted, expected, elapsed, outcome, now=None):
    now = now or datetime.now(timezone.utc)
    path = root / 'provider-metrics.json'
    data = read(path)
    day = now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    cutoff = (now.astimezone(ZoneInfo('Asia/Shanghai')).date() - timedelta(days=6)).isoformat()
    data = {k: v for k, v in data.items() if k >= cutoff}
    bucket = data.setdefault(day, {}).setdefault(provider + '/' + model,
        {'requests': 0, 'accepted': 0, 'expected': 0, 'successes': 0, 'limited': 0, 'seconds': 0})
    bucket['requests'] += 1
    bucket['accepted'] += accepted
    bucket['expected'] += expected
    bucket['successes'] += int(accepted == expected and expected > 0)
    bucket['limited'] += int(outcome == 'rate_limited')
    bucket['seconds'] = round(bucket['seconds'] + max(0, elapsed), 3)
    atomic_write_json(path, data)


def grade(payload, chunk, worker):
    good, _, failed = worker.normalize_partial_response(payload, chunk)
    by_id = {b['id']: b['translationZh'] for b in good}
    checks, passed = 0, 0
    for i, block in enumerate(chunk):
        text = by_id.get(block['id'], '').replace('％', '%')
        anchors, patterns = FIXTURE[i][1:]
        tests = [bool(text), len(text) >= 20]
        tests += [a in text for a in anchors]
        tests += [bool(re.search(p, text)) for p in patterns]
        checks += len(tests)
        passed += sum(tests)
    return round(passed / checks, 4), {b['id']: b['translationZh'] for b in good}


def score(result, history):
    if result.get('status') != 'passed':
        return 0
    requests = sum(h.get('requests', 0) for h in history)
    successes = sum(h.get('successes', 0) for h in history)
    expected = sum(h.get('expected', 0) for h in history)
    accepted = sum(h.get('accepted', 0) for h in history)
    limited = sum(h.get('limited', 0) for h in history)
    # Prior regularization prevents one fast call from overwhelming a week of data.
    reliability = (successes + 2) / (requests + 2)
    validity = (accepted + 3) / (expected + 3)
    availability = 1 - limited / max(1, requests)
    speed = min(1, 10 / max(1, result.get('seconds', 180)))
    return round(60 * result['fidelityProxy'] * validity + 25 * reliability + 10 * availability + 5 * speed, 2)


def prepare_routing(root, providers, worker, now=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    path = root / 'provider-ranking.json'
    report = read(path)
    if report.get('evaluationDate') == today:
        return report
    previous = report
    report = {'schemaVersion': 1, 'evaluationDate': today, 'startedAt': now.isoformat(),
              'status': 'running', 'results': [], 'order': list(providers), 'selectedModels': {},
              'method': 'fixed-fixture-v1; automatic fidelity/format proxy 60%, reliability 25%, non-429 availability 10%, latency 5%; 7-day production history',
              'limitations': 'Small automatic sample, not a human semantic-quality review. Quotas/cooldowns override rank at every call.'}
    # Mark before any inference, so interruption/re-run never repeats today's probes.
    atomic_write_json(path, report)
    chunk = worker.article_blocks('\n\n'.join(row[0] for row in FIXTURE))
    cutoff = (now.astimezone(ZoneInfo('Asia/Shanghai')).date() - timedelta(days=6)).isoformat()
    metrics = {d: v for d, v in read(root / 'provider-metrics.json').items() if d >= cutoff}
    for provider in providers:
        spec = PROVIDERS[provider]
        token = os.getenv(spec['key'], '').strip()
        control = (TokenRateControl if provider in ('groq', 'gemini') else RateControl)(root / f'rate-{provider}.json', provider)
        models = spec['models']
        if provider == 'openrouter':
            try:
                models = [ModelPool(root / 'model-health.json').select()]
            except Exception:
                report['results'].append({'provider': provider, 'status': 'unavailable', 'reason': 'No healthy approved zero-price model'})
                atomic_write_json(path, report)
                continue
        for model in models:
            result = {'provider': provider, 'model': model, 'status': 'pending'}
            report['results'].append(result)
            admitted = False
            started = time.monotonic()
            try:
                if provider in ('groq', 'gemini'):
                    control.acquire(model, token, tokens=reservation(worker.SYSTEM_PROMPT, chunk, provider))
                else:
                    control.acquire(model, token)
                admitted = True
                payload, actual, headers = worker.request_translation(token, model, chunk, spec['endpoint'])
                if hasattr(control, 'observe'):
                    control.observe(model, headers)
                quality, translations = grade(payload, chunk, worker)
                result.update(status='passed' if quality == 1 else 'quality_failed', fidelityProxy=quality,
                              actualModel=actual, sampleTranslations=translations)
                control.finished(model, success=True)
            except (RateLimited, RequestTooLarge) as error:
                result.update(status='deferred', reason=str(error))
            except Exception as error:
                worker.error_message(error)
                result.update(status='error', reason=type(error).__name__, httpStatus=getattr(error, 'code', None))
                if isinstance(error, urllib.error.HTTPError):
                    if hasattr(control, 'penalize_error'):
                        limited = control.penalize_error(model, error)
                    else:
                        limited = control.penalize(model, error.code, error.headers,
                            getattr(error, 'translation_limit_source', ''), getattr(error, 'translation_provider_code', ''))
                    result['nextAttemptAt'] = limited.until.isoformat()
                    if limited.scope == 'account':
                        result['stopProvider'] = True
                elif admitted and not isinstance(error, (ValueError, KeyError, TypeError)):
                    limited = control.penalize(model, 503)
                    result['nextAttemptAt'] = limited.until.isoformat()
            finally:
                if admitted:
                    control.finished(model)
                result['seconds'] = round(time.monotonic() - started, 3)
                key = provider + '/' + model
                history = [day[key] for day in metrics.values() if key in day]
                result['productionRequests7d'] = sum(h.get('requests', 0) for h in history)
                result['score'] = score(result, history)
                atomic_write_json(path, report)
                print(f"[translate:evaluation] {provider}/{model}: {result['status']}; score={result['score']}")
            if result.get('stopProvider'):
                break
    ranked = sorted([r for r in report['results'] if r['status'] == 'passed'], key=lambda r: -r['score'])
    order, selected = [], {}
    for result in ranked:
        if result['provider'] not in order:
            order.append(result['provider'])
            selected[result['provider']] = result['model']
    # Existing known providers remain fallbacks. New providers require a recent
    # passing fixture; a cooldown alone may reuse yesterday's passing evaluation.
    for provider in providers:
        if provider in order:
            continue
        old = previous.get('selectedModels', {}).get(provider)
        old_date = previous.get('evaluationDate', '')
        today_results = [r for r in report['results'] if r['provider'] == provider]
        if (old and old in PROVIDERS[provider]['models'] and
            old_date >= (now - timedelta(days=2)).date().isoformat() and
            today_results and all(r['status'] == 'deferred' for r in today_results)):
            selected[provider] = old
        if provider in ('openrouter', 'bigmodel') or provider in selected:
            order.append(provider)
    report.update(order=order, selectedModels=selected, status='finished', finishedAt=datetime.now(timezone.utc).isoformat())
    atomic_write_json(path, report)
    return report
