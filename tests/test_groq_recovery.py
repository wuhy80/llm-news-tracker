import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from api_rate_control import RateControl, RateLimited
from provider_limits import TokenRateControl, UsageHeaders, repair_content_pause
from continue_translation import continuation_inputs
import translate_articles as tr
import continue_translation as batches
from types import SimpleNamespace


class GroqRecoveryTests(unittest.TestCase):
    def test_both_historical_pause_formats_are_repaired_once(self):
        for message in ['HTTP 400: {"error":{"code":"json_validate_failed"}}',
                        'account: HTTP 400 / json_validate_failed; next attempt after 2026-09-28T17:13:56.067030+00:00']:
            state = {'lastError': message, 'nextAttemptAt': '2099-01-01T00:00:00Z', 'requestsToday': 41}
            self.assertTrue(repair_content_pause('groq', state))
            self.assertNotIn('nextAttemptAt', state)
            self.assertEqual(state['requestsToday'], 41)
            self.assertFalse(repair_content_pause('groq', state))

    def test_real_limits_auth_and_other_providers_are_not_repaired(self):
        for provider, message in [
            ('gemini', 'HTTP 400: {"error":{"code":"json_validate_failed"}}'),
            ('groq', 'account: HTTP 429; next attempt after 2099-01-01T00:00:00Z'),
            ('groq', 'HTTP 401: {"error":{"code":"json_validate_failed"}}'),
            ('groq', 'HTTP 400: {"error":{"code":"invalid_request","message":"json_validate_failed"}}'),
        ]:
            state = {'lastError': message, 'nextAttemptAt': '2099-01-01T00:00:00Z'}
            before = dict(state)
            self.assertFalse(repair_content_pause(provider, state))
            self.assertEqual(state, before)

    def test_usage_reclaims_daily_budget_but_retains_minute_reservation(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GROQ_TPD': '12000'}, clear=True), \
                patch.object(RateControl, 'acquire'):
            path = Path(directory)/'rate.json'
            gate = TokenRateControl(path, 'groq')
            gate.acquire('m', tokens=5000)
            gate.observe('m', UsageHeaders({}, {'total_tokens': 1000, 'prompt_tokens': 600, 'completion_tokens': 400}))
            event = gate.data['reservations']['m'][0]
            self.assertEqual(event['tokens'], 1000)
            self.assertEqual(event['reservedTokens'], 5000)
            self.assertEqual(event['usageSource'], 'api')
            # The minute gate must still reserve 5000, not only actual 1000.
            with self.assertRaises(RateLimited):
                gate.acquire('m', now=now-timedelta(seconds=1), tokens=2000)
            restored = TokenRateControl(path, 'groq')
            restored.acquire('m', now=now+timedelta(minutes=2), tokens=5000)
            self.assertEqual(sum(e['tokens'] for e in restored.data['reservations']['m']), 6000)

    def test_missing_invalid_usage_failure_and_restart_keep_reservations(self):
        for usage in [None, {}, {'total_tokens': True}, {'total_tokens': -1},
                      {'total_tokens': 1, 'prompt_tokens': 2, 'completion_tokens': 3}]:
            with tempfile.TemporaryDirectory() as directory, patch.object(RateControl, 'acquire'):
                gate = TokenRateControl(Path(directory)/'rate.json', 'groq')
                gate.acquire('m', tokens=5000)
                gate.observe('m', UsageHeaders({}, usage))
                self.assertEqual(gate.data['reservations']['m'][0]['tokens'], 5000)
        with tempfile.TemporaryDirectory() as directory, patch.object(RateControl, 'acquire'):
            path = Path(directory)/'rate.json'
            gate = TokenRateControl(path, 'groq')
            gate.acquire('m', tokens=5000)
            gate.finished('m')
            gate.settle('m', {'total_tokens': 100, 'prompt_tokens': 50, 'completion_tokens': 50})
            restored = TokenRateControl(path, 'groq')
            restored.settle('m', {'total_tokens': 100, 'prompt_tokens': 50, 'completion_tokens': 50})
            self.assertEqual(restored.data['reservations']['m'][0]['tokens'], 5000)

    def test_actual_usage_above_reservation_is_not_clamped(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(RateControl, 'acquire'):
            gate = TokenRateControl(Path(directory)/'rate.json', 'groq')
            gate.acquire('m', tokens=1000)
            gate.observe('m', UsageHeaders({}, {'total_tokens': 1300, 'prompt_tokens': 700, 'completion_tokens': 600}))
            self.assertEqual(gate.data['reservations']['m'][0]['tokens'], 1300)

    def test_usage_does_not_override_remote_limits_or_real_cooldowns(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch.object(RateControl, 'acquire'):
            gate = TokenRateControl(Path(directory)/'rate.json', 'groq')
            gate.acquire('m', tokens=5000)
            gate.observe('m', UsageHeaders({'x-ratelimit-remaining-tokens': '10', 'x-ratelimit-reset-tokens': '120s'},
                {'total_tokens': 100, 'prompt_tokens': 50, 'completion_tokens': 50}), now)
            with self.assertRaises(RateLimited):
                gate.acquire('m', now=now+timedelta(seconds=70), tokens=1000)
            gate.data['cooldowns']['account'] = {'reason': 'HTTP 429', 'until': (now+timedelta(days=1)).isoformat()}
            state = {'lastError': 'account: HTTP 400 / json_validate_failed; next attempt after 2099-01-01T00:00:00Z'}
            repair_content_pause('groq', state)
            with self.assertRaises(RateLimited):
                gate.acquire('m', now=now+timedelta(minutes=3), tokens=1000)

    def test_real_response_transports_usage_to_limiter(self):
        class Response(io.BytesIO):
            headers = {'x-ratelimit-limit-tokens': '8000'}
        usage = {'total_tokens': 800, 'prompt_tokens': 500, 'completion_tokens': 300}
        payload = {'choices': [{'message': {'content': '{"translations":[]}'}}], 'usage': usage}
        with tempfile.TemporaryDirectory() as directory, patch.object(RateControl, 'acquire'), \
             patch.object(tr.urllib.request, 'urlopen', return_value=Response(json.dumps(payload).encode())):
            gate = TokenRateControl(Path(directory)/'rate.json', 'groq')
            gate.acquire('openai/gpt-oss-120b', tokens=5000)
            _, _, headers = tr.request_translation('test', 'openai/gpt-oss-120b', [], tr.PROVIDERS['groq']['endpoint'])
            gate.observe('openai/gpt-oss-120b', headers)
            self.assertEqual(gate.data['reservations']['openai/gpt-oss-120b'][0]['tokens'], 800)
            self.assertNotIn('usage', dict(headers))

    def test_in_job_batches_checkpoint_before_next_calls_and_stop_at_three(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'data/translations').mkdir(parents=True)
            runtime = {'runUrl': 'https://github.com/a/b/actions/runs/123', 'status': 'finished',
                       'providers': {'groq': {'runStatus': 'progress', 'runStopReason': 'request_limit', 'runModelBlocks': 1}}}
            (root/'data/translations/runtime.json').write_text(json.dumps(runtime))
            actions = []
            def run(command, **kwargs):
                actions.append(Path(command[-1]).name)
                return SimpleNamespace(returncode=0)
            with patch.object(batches, '__file__', str(root/'scripts/continue_translation.py')), \
                 patch.dict(os.environ, {'GITHUB_REPOSITORY': 'a/b', 'GITHUB_RUN_ID': '123'}, clear=True), \
                 patch.object(batches.subprocess, 'run', side_effect=run), \
                 patch.object(batches, 'checkpoint', side_effect=lambda _: actions.append('checkpoint')):
                self.assertEqual(batches.main(), 0)
            self.assertEqual(actions, ['translate_articles.py', 'validate_translations.py', 'checkpoint'] * 3)

    def test_bounded_continuation_preserves_inputs_and_rejects_stale_or_idle_runs(self):
        runtime = {'runUrl': 'current', 'status': 'finished', 'providers': {'groq': {
            'runStatus': 'progress', 'runStopReason': 'time_limit', 'runModelBlocks': 10}}}
        self.assertEqual(continuation_inputs(runtime, {'request_limit': '4'}, 'current'),
                         {'request_limit': '4', 'continuation_depth': '1'})
        self.assertIsNone(continuation_inputs(runtime, {'continuation_depth': '3'}, 'current'))
        self.assertIsNone(continuation_inputs(runtime, {}, 'different'))
        for reason in ('rate_limited', 'queue_empty', 'error', 'daily_limit'):
            runtime['providers']['groq']['runStopReason'] = reason
            self.assertIsNone(continuation_inputs(runtime, {}, 'current'))
        runtime['providers']['groq'].update(runStopReason='request_limit', runModelBlocks=0)
        self.assertIsNone(continuation_inputs(runtime, {}, 'current'))


if __name__ == '__main__':
    unittest.main()
