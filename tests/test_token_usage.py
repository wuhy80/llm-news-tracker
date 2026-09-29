import io
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import token_usage as usage
import translate_articles as tr


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'token-usage.json'
        self.patch = patch.object(usage, 'LEDGER', self.path)
        self.patch.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.patch.stop)

    def data(self):
        return json.loads(self.path.read_text())

    def test_date_purpose_and_unknown_interruption(self):
        now = datetime(2026, 9, 28, 16, tzinfo=timezone.utc)
        with usage.usage_purpose('evaluation'):
            with usage.track_request('groq', 'model', now) as record:
                record['usage'] = {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 35}
        with self.assertRaises(RuntimeError):
            with usage.track_request('groq', 'model', now):
                raise RuntimeError('network failure')
        rows = self.data()['days']['2026-09-29']['groq']['model']
        self.assertEqual(rows['evaluation']['totalTokens'], 35)
        self.assertEqual(rows['translation']['unknownUsageRequests'], 1)
        self.assertEqual(rows['translation']['totalTokens'], 0)

    def test_parallel_updates_and_history(self):
        def request(i):
            with usage.track_request('groq' if i % 2 else 'gemini', 'model', datetime(2026, 9, 28, tzinfo=timezone.utc)) as r:
                r['usage'] = {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(request, range(40)))
        with usage.track_request('groq', 'model', datetime(2026, 9, 29, tzinfo=timezone.utc)):
            pass
        for rows in self.data()['days']['2026-09-28'].values():
            self.assertEqual(rows['model']['translation']['totalTokens'], 60)
            self.assertEqual(rows['model']['translation']['requests'], 20)

    def test_invalid_and_native_usage(self):
        for value in [None, {}, {'prompt_tokens': True, 'completion_tokens': 1, 'total_tokens': 2},
                      {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 2}]:
            self.assertIsNone(usage.normalize_usage(value))
        self.assertEqual(usage.normalize_usage({'promptTokenCount': 4, 'candidatesTokenCount': 2, 'totalTokenCount': 9}), (4, 2, 9))

    def test_all_provider_responses_count_before_content_validation(self):
        pairs = [('bigmodel', tr.BIGMODEL_MODEL, tr.BIGMODEL_ENDPOINT),
                 ('openrouter', 'approved-model', tr.OPENROUTER_ENDPOINT)]
        pairs += [(p, tr.PROVIDERS[p]['models'][0], tr.PROVIDERS[p]['endpoint']) for p in ('groq', 'gemini')]
        for provider, model, endpoint in pairs:
            response = io.BytesIO(json.dumps({'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
                'choices': [{'finish_reason': 'length', 'message': {'content': 'invalid'}}]}).encode())
            response.headers = {}
            with patch.object(tr.urllib.request, 'urlopen', return_value=response):
                with self.assertRaises(ValueError):
                    tr.request_translation('test', model, [], endpoint)
        day = next(iter(self.data()['days'].values()))
        for provider, model, _ in pairs:
            self.assertEqual(day[provider][model]['translation']['totalTokens'], 15)

    def test_missing_usage_report(self):
        with usage.track_request('gemini', 'model'):
            pass
        data = self.data()
        day = next(iter(data['days']))
        self.assertIn('| 0 / 1 |', usage.report(data, day))
