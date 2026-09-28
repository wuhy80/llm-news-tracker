"""Token-aware admission for Groq/Gemini; persisted alongside translation progress.

Groq uses conservative rolling 24h budgets (not an assumed reset timezone).
Gemini uses America/Los_Angeles calendar days. Defaults for Gemini are local
pilot caps, NOT claims about the account's entitlement. Configure console limits
with GEMINI_RPM/TPM/RPD; observed lower limits always win.
"""
import math
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from api_rate_control import RateControl, RateLimited, timestamp


def duration(value):
    value = str(value or '')
    if not re.fullmatch(r'(?:\d+(?:\.\d+)?(?:ms|[hms]))+', value):
        return 0
    return sum(float(n) * {'h': 3600, 'm': 60, 's': 1, 'ms': .001}[u]
               for n, u in re.findall(r'(\d+(?:\.\d+)?)(ms|[hms])', value))


def next_pacific_day(now):
    local = now.astimezone(ZoneInfo('America/Los_Angeles'))
    return (datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), local.tzinfo)
            + timedelta(minutes=5)).astimezone(timezone.utc)


class RequestTooLarge(ValueError):
    pass


def is_content_error(provider, error):
    return (provider == 'groq' and getattr(error, 'code', None) == 400
            and getattr(error, 'translation_provider_code', '') == 'json_validate_failed')


class UsageHeaders(dict):
    """Transport usage beside headers without changing the response tuple API."""
    def __init__(self, headers, usage=None):
        super().__init__(headers)
        self.usage = usage


def repair_content_pause(provider, state):
    """Migrate only the two known serializations of the Groq content error."""
    if provider != 'groq':
        return False
    message = state.get('lastError', '')
    raw_error = False
    if message.startswith('HTTP 400: '):
        try:
            error = json.loads(message[len('HTTP 400: '):]).get('error', {})
            raw_error = isinstance(error, dict) and error.get('code') == 'json_validate_failed'
        except (ValueError, AttributeError):
            pass
    normalized = re.fullmatch(
        r'account: HTTP 400 / json_validate_failed; next attempt after [0-9T:.+Z-]+', message)
    if not (raw_error or normalized):
        return False
    state.pop('nextAttemptAt', None)
    state.update(lastStatus='content_error', lastError='Groq JSON output failure; stale pause repaired')
    return True


class TokenRateControl(RateControl):
    def __init__(self, path, provider, interval=0):
        # One completion per rolling minute across this provider, including
        # evaluation/model changes and restarts. 750 <= observed OTPM 1000.
        if provider == 'groq':
            interval = max(interval, 61)
        super().__init__(path, provider, interval)
        self._pending = {}
        # Repair only the exact historical content-error misclassification.
        # Keep token reservations, server budgets, and genuine cooldowns intact.
        account = self.data['cooldowns'].get('account', {})
        if provider == 'groq' and account.get('reason') == 'HTTP 400 / json_validate_failed':
            del self.data['cooldowns']['account']
            self.save()

    def limits(self, model):
        defaults = {'rpm': 30, 'tpm': 8000, 'rpd': 1000, 'tpd': 200000} if self.provider == 'groq' else {'rpm': 1, 'tpm': 8000, 'rpd': 8}
        result = {}
        observed = self.data.get('observed', {}).get(model, {})
        for key, default in defaults.items():
            configured = max(0, int(os.getenv(f'{self.provider.upper()}_{key.upper()}', str(default))))
            value = min(configured, observed.get(key, configured))
            # An explicitly configured zero disables that model/quota dimension.
            result[key] = max(1, math.floor(value * .75)) if value else 0
        return result

    def events(self, model, now):
        all_events = self.data.setdefault('reservations', {})
        events = [e for e in all_events.get(model, []) if e['at'] > now.timestamp() - 86400]
        all_events[model] = events
        return events

    def acquire(self, model, token='', now=None, tokens=0):
        now = now or datetime.now(timezone.utc)
        self.blocked(model, now)
        limits = self.limits(model)
        if tokens <= 0:
            raise ValueError('A positive token reservation is required')
        if tokens > limits['tpm'] and limits['tpm']:
            raise RequestTooLarge('Chunk exceeds conservative token-per-minute budget')
        events = self.events(model, now)
        minute = [e for e in events if e['at'] > now.timestamp() - 60]
        if self.provider == 'gemini':
            today = now.astimezone(ZoneInfo('America/Los_Angeles')).date()
            daily = [e for e in events if datetime.fromtimestamp(e['at'], timezone.utc).astimezone(ZoneInfo('America/Los_Angeles')).date() == today]
            reset = next_pacific_day(now)
        else:
            daily = events
            reset = datetime.fromtimestamp(min((e['at'] for e in daily), default=now.timestamp()) + 86400, timezone.utc) + timedelta(seconds=5)
        scope = 'model:' + model
        if (not all(limits.values()) or len(daily) >= limits['rpd'] or
                ('tpd' in limits and sum(e['tokens'] for e in daily) + tokens > limits['tpd'])):
            self.save()
            raise RateLimited(reset, scope, 'local daily budget exhausted')
        # Do not block a worker for minutes: resume on the next scheduled run.
        if len(minute) >= limits['rpm'] or sum(max(e['tokens'], e.get('reservedTokens', e['tokens'])) for e in minute) + tokens > limits['tpm']:
            reset = datetime.fromtimestamp(min(e['at'] for e in minute) + 61, timezone.utc)
            wait = (reset - now).total_seconds()
            if 0 < wait <= 60:
                time.sleep(wait)
                return self.acquire(model, token, tokens=tokens)
            raise RateLimited(reset, scope, 'local minute budget exhausted')
        remote = self.data.get('remote', {}).get(model, {})
        for kind, needed in [('requests', 1), ('tokens', tokens)]:
            until = timestamp(remote.get(kind + 'Until'))
            if until and until > now and remote.get(kind + 'Remaining', needed) < needed:
                raise RateLimited(until, scope, 'reported ' + kind + ' budget exhausted')
        super().acquire(model, token, now)
        # Keep the full conservative reservation even if the call errors or is cancelled.
        event = {'at': datetime.now(timezone.utc).timestamp(), 'tokens': tokens, 'reservedTokens': tokens}
        events.append(event)
        self._pending[model] = event
        for kind, needed in [('requests', 1), ('tokens', tokens)]:
            if kind + 'Remaining' in remote:
                remote[kind + 'Remaining'] = max(0, remote[kind + 'Remaining'] - needed)
        self.save()

    def settle(self, model, usage):
        # Only this process's current request can be reconciled. Historical
        # reservations and failed/unknown responses remain conservative.
        event = self._pending.pop(model, None)
        if event is None or self.provider != 'groq' or not isinstance(usage, dict):
            return
        total = usage.get('total_tokens')
        prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if (type(total) is not int or total <= 0 or type(prompt) is not int
                or type(completion) is not int or prompt < 0 or completion < 0
                or total < prompt + completion):
            return
        event.update(tokens=total, actualTokens=total, promptTokens=prompt,
                     completionTokens=completion, usageSource='api')
        self.save()

    def finished(self, model, success=False):
        self._pending.pop(model, None)
        super().finished(model, success=success)

    def observe(self, model, headers, now=None):
        self.settle(model, getattr(headers, 'usage', None))
        now = now or datetime.now(timezone.utc)
        headers = {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
        remote = self.data.setdefault('remote', {}).setdefault(model, {})
        observed = self.data.setdefault('observed', {}).setdefault(model, {})
        for kind, metric in [('requests', 'rpd'), ('tokens', 'tpm')]:
            prefix = 'x-ratelimit-'
            limit, remaining = headers.get(prefix + 'limit-' + kind), headers.get(prefix + 'remaining-' + kind)
            reset = duration(headers.get(prefix + 'reset-' + kind))
            if limit and limit.isdigit():
                observed[metric] = min(int(limit), observed.get(metric, int(limit)))
            if remaining and remaining.isdigit():
                remote[kind + 'Remaining'] = int(remaining)
                remote[kind + 'Until'] = (now + timedelta(seconds=reset or (86400 if kind == 'requests' else 60))).isoformat()
        self.save()

    def penalize_error(self, model, error):
        now = datetime.now(timezone.utc)
        self.observe(model, error.headers, now)
        headers = dict(error.headers or {})
        details = getattr(error, 'translation_error_details', [])
        daily = False
        observed = self.data.setdefault('observed', {}).setdefault(model, {})
        for entry in details if isinstance(details, list) else []:
            if not isinstance(entry, dict):
                continue
            if entry.get('@type', '').endswith('RetryInfo'):
                seconds = duration(entry.get('retryDelay'))
                headers['Retry-After'] = str(max(seconds, float(headers.get('Retry-After', 0))))
            for violation in entry.get('violations', []):
                ident = (violation.get('quotaId', '') + ' ' + violation.get('quotaMetric', '')).lower()
                daily |= 'perday' in ident or 'per_day' in ident
                metric = ('rpd' if 'request' in ident and ('perday' in ident or 'per_day' in ident)
                          else 'rpm' if 'request' in ident else 'tpm' if 'token' in ident and ('perminute' in ident or 'per_minute' in ident) else None)
                raw = violation.get('quotaValue')
                if metric and str(raw).isdigit():
                    observed[metric] = min(int(raw), observed.get(metric, int(raw)))
        # Unknown 429 remains account-scoped: never rotate models to bypass it.
        limited = super().penalize(model, error.code, headers, provider_code=getattr(error, 'translation_provider_code', ''))
        until = limited.until
        remote = self.data.get('remote', {}).get(model, {})
        for kind in ('requests', 'tokens'):
            if remote.get(kind + 'Remaining') == 0:
                until = max(until, timestamp(remote.get(kind + 'Until')) or now)
        if daily and self.provider == 'gemini':
            until = max(until, next_pacific_day(now))
        if error.code in (400, 401, 403, 404, 410):
            until = max(until, now + timedelta(hours=24))
        self.data['cooldowns'][limited.scope]['until'] = until.isoformat()
        self.save()
        return RateLimited(until, limited.scope, f'HTTP {error.code}')
