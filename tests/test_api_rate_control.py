import io
import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from api_rate_control import RateControl, RateLimited, server_retry_at
import ai_review


class Response(io.BytesIO):
    headers = {}


class RateControlTests(unittest.TestCase):
    def test_server_hints_seconds_date_and_epoch_milliseconds(self):
        now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        expected = now + timedelta(hours=2)
        self.assertEqual(server_retry_at({'Retry-After': '120'}, now), now + timedelta(seconds=120))
        self.assertEqual(server_retry_at({'Retry-After': 'Sun, 27 Sep 2026 14:00:00 GMT'}, now), expected)
        self.assertEqual(server_retry_at({'Retry-After': '120', 'X-RateLimit-Reset': str(expected.timestamp()*1000)}, now), expected)
        self.assertEqual(server_retry_at({'Retry-After': 'invalid', 'X-RateLimit-Reset': 'inf'}, now), now)

    def test_bigmodel_distinguishes_overload_from_account_and_escalates_across_runs(self):
        now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch('api_rate_control.random.uniform', return_value=1):
            path = Path(directory)/'state.json'
            gate = RateControl(path, 'bigmodel')
            error = gate.penalize('glm-4.7-flash', 429, provider_code='1305', now=now)
            self.assertEqual(error.scope, 'model:glm-4.7-flash')
            self.assertEqual(error.until, now + timedelta(minutes=15))
            gate = RateControl(path, 'bigmodel')
            error = gate.penalize('glm-4.7-flash', 429, provider_code='1305', now=now + timedelta(minutes=20))
            self.assertEqual(error.until, now + timedelta(minutes=50))
            account = gate.penalize('glm-4.7-flash', 429, provider_code='1302', now=now)
            self.assertEqual(account.scope, 'account')
            self.assertEqual(account.until, now + timedelta(minutes=1))

    def test_never_retry_before_server_hint_and_only_model_scope_can_switch(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch('api_rate_control.random.uniform', return_value=1):
            gate = RateControl(Path(directory)/'state.json', 'openrouter')
            limited = gate.penalize('model-a', 429, {'Retry-After': '7200'}, 'upstream_provider_shared_pool', now=now)
            self.assertEqual(limited.until, now + timedelta(hours=2))
            gate.blocked('model-b', now)
            with self.assertRaises(RateLimited):
                gate.blocked('model-a', now)
            gate.penalize('model-b', 429, now=now)
            with self.assertRaises(RateLimited):
                gate.blocked('model-c', now)

    def test_cooldown_survives_utc_rollover(self):
        now = datetime(2026, 9, 27, 23, 59, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            gate = RateControl(Path(directory)/'state.json', 'bigmodel')
            gate.daily(now)
            gate.penalize('glm', 429, provider_code='1305', now=now)
            gate.daily(now + timedelta(minutes=2))
            with self.assertRaises(RateLimited):
                gate.blocked('glm', now + timedelta(minutes=2))

    def test_pacing_is_after_completion_and_cannot_be_lowered_by_cli(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch('api_rate_control.time.sleep') as sleep, patch('api_rate_control.random.uniform', return_value=0):
            gate = RateControl(Path(directory)/'state.json', 'bigmodel', interval=0)
            gate.data['lastFinishedAt'] = (now - timedelta(seconds=1)).isoformat()
            gate.acquire('glm', now=now)
            self.assertEqual(sleep.call_args.args[0], 9)
            self.assertEqual(gate.data['requestsToday'], 1)
            gate.penalize('glm', 429, provider_code='1302', now=now)
            self.assertEqual(gate.data['intervalSeconds'], 20)

    def test_quota_read_precedes_inference_and_shared_reservations_stop_at_zero(self):
        now = datetime.now(timezone.utc)
        body = {'data': {'label': 'do not persist', 'free_model_daily_requests': {'used': 49, 'limit': 50, 'remaining': 1}}}
        with tempfile.TemporaryDirectory() as directory, patch('api_rate_control.urllib.request.urlopen', return_value=Response(json.dumps(body).encode())) as urlopen:
            path = Path(directory)/'state.json'
            gate = RateControl(path, 'openrouter')
            gate.acquire('model', 'fake-key', now)
            self.assertEqual(urlopen.call_count, 1)
            gate = RateControl(path, 'openrouter')
            with self.assertRaises(RateLimited):
                gate.acquire('another-model', 'fake-key', now)
            self.assertEqual(urlopen.call_count, 1)
            saved = path.read_text()
            self.assertNotIn('fake-key', saved)
            self.assertNotIn('do not persist', saved)
            self.assertEqual(gate.data['requestsToday'], 1)

    def test_absent_remote_quota_does_not_assume_1000_requests(self):
        with tempfile.TemporaryDirectory() as directory, patch('api_rate_control.urllib.request.urlopen', return_value=Response(b'{"data":{"is_free_tier":false}}')):
            gate = RateControl(Path(directory)/'state.json', 'openrouter')
            with self.assertRaises(RuntimeError):
                gate.acquire('model', 'fake-key')
            self.assertEqual(gate.data['requestsToday'], 0)

    def test_review_429_stops_without_immediate_retry_and_persists_shared_pause(self):
        quota = Response(b'{"data":{"free_model_daily_requests":{"used":0,"limit":1000,"remaining":1000}}}')
        error = urllib.error.HTTPError('https://openrouter.ai/api/v1/chat/completions', 429, 'limit', {'Retry-After':'600'},
                                     io.BytesIO(b'{"error":{"code":429,"metadata":{"limit_source":"account_daily_quota"}}}'))
        with tempfile.TemporaryDirectory() as directory, patch.object(ai_review, 'ROOT', Path(directory)), \
             patch('api_rate_control.urllib.request.urlopen', side_effect=[quota, error]) as urlopen:
            with self.assertRaises(RateLimited):
                ai_review.request_reviews('openrouter', ai_review.DEFAULT_OPENROUTER_ENDPOINT, 'fake', 'free-model', [])
            self.assertEqual(urlopen.call_count, 2)  # one quota read + one inference, no retry storm
            gate = RateControl(Path(directory)/'data/translations/rate-openrouter.json', 'openrouter')
            with self.assertRaises(RateLimited):
                gate.blocked('different-translation-model')


if __name__ == '__main__':
    unittest.main()
