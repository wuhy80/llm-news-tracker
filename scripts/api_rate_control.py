"""Shared, persisted admission control for serial free-model API calls.

Both Actions writers use the archive-writes concurrency group. These files must
be committed with progress, so scheduling another workflow does not reset limits.
"""
import json
import math
import random
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from news_store import atomic_write_json

MIN_INTERVAL = {'bigmodel': 10.0, 'openrouter': 6.0}


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def server_retry_at(headers, now):
    headers = {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
    times = []
    retry = headers.get('retry-after', '')
    try:
        seconds = float(retry)
        if math.isfinite(seconds) and seconds >= 0:
            times.append(now + timedelta(seconds=seconds))
    except (ValueError, OverflowError):
        try:
            parsed = parsedate_to_datetime(retry)
            if parsed.tzinfo:
                times.append(parsed)
        except (ValueError, TypeError, OverflowError):
            pass
    reset = headers.get('x-ratelimit-reset', '')
    try:
        epoch = float(reset)
        if epoch > 1e12:
            epoch /= 1000
        if math.isfinite(epoch) and epoch > 1e9:
            times.append(datetime.fromtimestamp(epoch, timezone.utc))
    except (ValueError, OverflowError, OSError):
        parsed = timestamp(reset)
        if parsed:
            times.append(parsed)
    return max([now, *times])


class RateLimited(RuntimeError):
    def __init__(self, until, scope='account', reason='cooldown'):
        self.until, self.scope = until, scope
        super().__init__(f'{scope}: {reason}; next attempt after {until.isoformat()}')


class RateControl:
    def __init__(self, path, provider, interval=0):
        self.path, self.provider = Path(path), provider
        self.minimum = max(MIN_INTERVAL[provider], interval)
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault('cooldowns', {})
        self.data.setdefault('intervalSeconds', self.minimum)

    def save(self):
        atomic_write_json(self.path, self.data)

    def daily(self, now):
        today = now.date().isoformat()
        if self.data.get('utcDate') != today:
            for key in ('quota', 'quotaCheckedAt'):
                self.data.pop(key, None)
            self.data.update(utcDate=today, requestsToday=0)
            # A date change does NOT clear service/account cooldowns.

    def blocked(self, model, now=None):
        now = now or datetime.now(timezone.utc)
        for scope in ('account', 'model:' + model):
            state = self.data['cooldowns'].get(scope, {})
            until = timestamp(state.get('until'))
            if until and until > now:
                raise RateLimited(until, scope, state.get('reason', 'cooldown'))

    def refresh_quota(self, token, now=None):
        if self.provider != 'openrouter':
            return
        now = now or datetime.now(timezone.utc)
        self.daily(now)
        checked = timestamp(self.data.get('quotaCheckedAt'))
        if checked and (now - checked).total_seconds() < 300:
            return
        request = urllib.request.Request('https://openrouter.ai/api/v1/key',
                                        headers={'Authorization': f'Bearer {token}', 'User-Agent': 'LLM-Pulse/1.0'})
        # Do not copy the key label, balance, or token into logs or persisted state.
        with urllib.request.urlopen(request, timeout=30) as response:
            account = json.load(response)['data']
        quota = account.get('free_model_daily_requests')
        if not isinstance(quota, dict) or any(type(quota.get(k)) is not int or quota[k] < 0 for k in ('used', 'limit', 'remaining')):
            raise RuntimeError('OpenRouter did not report a usable daily free-request quota; no inference sent')
        previous = self.data.get('quota', {})
        remaining = min(quota['remaining'], max(0, quota['limit'] - quota['used']))
        # Keep reservations conservative when the remote counter is eventually consistent.
        if previous:
            remaining = min(remaining, previous['remaining'])
        self.data.update(quota={'used': quota['used'], 'limit': quota['limit'], 'remaining': remaining},
                         quotaCheckedAt=now.isoformat())
        self.save()

    def acquire(self, model, token='', now=None):
        now = now or datetime.now(timezone.utc)
        self.daily(now)
        self.blocked(model, now)
        self.refresh_quota(token, now)
        if self.provider == 'openrouter' and self.data['quota']['remaining'] <= 0:
            until = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), timezone.utc) + timedelta(minutes=5)
            self.data['cooldowns']['account'] = {'until': until.isoformat(), 'reason': 'daily free quota exhausted', 'failures': 0}
            self.save()
            raise RateLimited(until, reason='daily free quota exhausted')
        last = timestamp(self.data.get('lastFinishedAt') or self.data.get('lastRequestAt'))
        gap = max(self.minimum, self.data.get('intervalSeconds', self.minimum))
        wait = max(0, gap - (now - last).total_seconds()) if last else 0
        if wait:
            time.sleep(wait + random.uniform(0, 1))
        self.data['lastRequestAt'] = datetime.now(timezone.utc).isoformat()
        self.data['requestsToday'] += 1
        if self.provider == 'openrouter':
            self.data['quota']['remaining'] -= 1
        self.save()

    def finished(self, model, success=False):
        self.data['lastFinishedAt'] = datetime.now(timezone.utc).isoformat()
        if success:
            self.data['cooldowns'].pop('model:' + model, None)
            self.data['cooldowns'].pop('account', None)
            self.data['successes'] = self.data.get('successes', 0) + 1
            if self.data['successes'] >= 10:
                self.data['intervalSeconds'] = max(self.minimum, self.data['intervalSeconds'] / 2)
                self.data['successes'] = 0
        self.save()

    def penalize(self, model, status, headers=None, limit_source='', provider_code='', now=None):
        now = now or datetime.now(timezone.utc)
        model_scope = (limit_source == 'upstream_provider_shared_pool'
                       or (self.provider == 'bigmodel' and provider_code == '1305')
                       or status in (500, 502, 503, 504))
        scope = 'model:' + model if model_scope else 'account'
        prior = self.data['cooldowns'].get(scope, {})
        failures = prior.get('failures', 0) + 1
        base = 900 if self.provider == 'bigmodel' and provider_code == '1305' else 1800 if model_scope else 60 if provider_code == '1302' else 300
        delay = min(21600, base * (2 ** min(failures - 1, 7)))
        until = max(now + timedelta(seconds=delay * random.uniform(1.0, 1.2)), server_retry_at(headers, now))
        if status == 402 or limit_source in ('account_daily_quota', 'free_model_daily_requests'):
            midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), timezone.utc) + timedelta(minutes=5)
            until = max(until, midnight)
        reason = f'HTTP {status}' + (f' / {provider_code}' if provider_code else '')
        self.data['cooldowns'][scope] = {'until': until.isoformat(), 'failures': failures, 'reason': reason}
        self.data['intervalSeconds'] = min(60, max(self.minimum, self.data['intervalSeconds'] * 2))
        self.data['successes'] = 0
        self.save()
        return RateLimited(until, scope, reason)
