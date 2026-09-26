"""Discover zero-price text models and persist bounded health-based failover."""
import json
import urllib.request
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path
from news_store import atomic_write_json

CATALOG_URL = 'https://openrouter.ai/api/v1/models'
PREFERRED = ['qwen/qwen3.8-27b:free', 'google/gemma-4-31b-it:free',
             'google/gemma-4-26b-a4b-it:free', 'nvidia/nemotron-3-super-120b-a12b:free']


def free_text_model(model):
    ident = model.get('id', '')
    # A free listing is not quality approval. Config overrides cannot bypass this.
    if ident not in PREFERRED:
        return False
    pricing = model.get('pricing', {})
    try:
        if any(Decimal(str(pricing.get(k, '-1'))) != 0 for k in ('prompt', 'completion')):
            return False
        if any(Decimal(str(v)) != 0 for v in pricing.values()):
            return False
    except (InvalidOperation, ValueError):
        return False
    arch = model.get('architecture', {})
    if 'text' not in arch.get('input_modalities', []) or 'text' not in arch.get('output_modalities', []):
        return False
    return (model.get('context_length', 0) >= 16000
            and (model.get('top_provider', {}).get('max_completion_tokens') or 8000) >= 8000
            and not any(x in ident for x in ('safety', 'guard', 'code', 'fin:', 'sante:')))


def fetch_catalog():
    request = urllib.request.Request(CATALOG_URL, headers={'User-Agent': 'LLM-Pulse/1.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)['data']


class ModelPool:
    def __init__(self, path, preferred='', catalog=None, now=None):
        self.path = Path(path)
        self.now = now or datetime.now(timezone.utc)
        try:
            self.health = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.health = {}
        self.health.setdefault('models', {})
        # Refresh each run, stronger than daily. Fail closed on catalog/network errors.
        available = {m['id'] for m in (fetch_catalog() if catalog is None else catalog) if free_text_model(m)}
        order = [preferred] + PREFERRED
        self.candidates = list(dict.fromkeys(m for m in order if m in available))
        self.attempted = set()
        self.health.update({'schemaVersion': 1, 'checkedAt': self.now.isoformat(), 'available': self.candidates})
        self.save()

    def save(self):
        atomic_write_json(self.path, self.health)

    def select(self):
        if len(self.attempted) >= 3:
            raise RuntimeError('Three free models failed in this run; stopping until next scheduled check')
        for model in self.candidates:
            blocked = self.health['models'].get(model, {}).get('disabledUntil', '')
            if model not in self.attempted and (not blocked or datetime.fromisoformat(blocked) <= self.now):
                self.health['selectedModel'] = model
                self.save()
                return model
        raise RuntimeError('No healthy approved free translation model available; waiting instead of lowering quality')

    def failed(self, model, reason, permanent=False):
        self.attempted.add(model)
        self.health['models'][model] = {'lastError': reason[:600], 'failedAt': self.now.isoformat(),
            'disabledUntil': (self.now + timedelta(hours=24 if permanent else 1)).isoformat()}
        self.save()

    def succeeded(self, model):
        self.health['models'][model] = {'lastSuccessAt': datetime.now(timezone.utc).isoformat()}
        self.save()
