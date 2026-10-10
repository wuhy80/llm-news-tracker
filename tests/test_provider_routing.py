import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import translate_articles as tr
import provider_routing as routing
from api_rate_control import RateLimited
from provider_limits import TokenRateControl, RequestTooLarge, duration, next_pacific_day

GOOD = [
    '重试预算是 3 次请求，而不是 30 次。HTTP 429 并不意味着 API 密钥无效。',
    '保持 /data/models 不变。在重启 Kubernetes 之前运行 kubectl get pods。文档：https://example.com/guide',
    '在这个实验中，批处理使延迟降低了 25%，但增加了内存使用量。这一结果并不能证明每一种工作负载都更快。',
]


def response(chunk, good=True):
    return {'translations': [{'id': b['id'], 'translationZh': GOOD[i] if good else '快速但是完全错误的中文译文。'} for i, b in enumerate(chunk)]}


class ProviderTests(unittest.TestCase):
    def test_every_provider_is_paced_and_openai_shaped(self):
        # RateControl indexes MIN_INTERVAL by provider name, so a provider without
        # an entry raises KeyError the first time it is paced.
        from api_rate_control import MIN_INTERVAL

        for provider, spec in routing.PROVIDERS.items():
            with self.subTest(provider=provider):
                self.assertIn(provider, MIN_INTERVAL)
                self.assertTrue(spec['endpoint'].endswith('/chat/completions'))
                self.assertTrue(spec['key'].endswith('_API_KEY'))
                self.assertTrue(spec['models'] or provider == 'openrouter')

    def test_nowcoding_points_at_the_relay_and_its_model(self):
        spec = routing.PROVIDERS['nowcoding']

        self.assertEqual(spec['key'], 'NOWCODING_API_KEY')
        self.assertEqual(spec['endpoint'], 'https://nowcoding.ai/v1/chat/completions')
        self.assertEqual(spec['models'], ['gpt-6.1-sol'])

    def test_groq_output_budget_defers_long_blocks_without_mutating_them(self):
        chunk = tr.article_blocks('A' * 501)
        original = json.dumps(chunk)
        with self.assertRaises(RequestTooLarge):
            routing.reservation(tr.SYSTEM_PROMPT, chunk, 'groq')
        self.assertEqual(json.dumps(chunk), original)
        self.assertGreater(routing.reservation(tr.SYSTEM_PROMPT, chunk, 'gemini'), 0)

    def test_groq_output_pacing_survives_restart_and_model_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rate.json'
            gate = TokenRateControl(path, 'groq')
            gate.finished('qwen/qwen3.8-27b', success=True)
            restored = TokenRateControl(path, 'groq')
            with patch('api_rate_control.time.sleep') as sleep:
                restored.acquire('openai/gpt-oss-120b', tokens=1000)
            self.assertGreaterEqual(restored.minimum, 61)
            self.assertGreater(sleep.call_args.args[0], 60)

    def test_valid_json_with_length_finish_is_never_accepted(self):
        class Response(io.BytesIO):
            headers = {}
        body = {'choices': [{'finish_reason': 'length', 'message': {'content': '{"translations":[]}'}}]}
        with patch.object(tr.urllib.request, 'urlopen', return_value=Response(json.dumps(body).encode())):
            with self.assertRaisesRegex(ValueError, 'truncated'):
                tr.request_translation('fake', 'qwen/qwen3.8-27b', [], routing.PROVIDERS['groq']['endpoint'])

    def test_direct_payloads_use_own_endpoint_key_no_paid_router_or_tools(self):
        class Response(io.BytesIO):
            headers = {}
        for provider in ('groq', 'gemini'):
            for model in routing.PROVIDERS[provider]['models']:
                with patch.object(tr.urllib.request, 'urlopen', return_value=Response(b'{"choices":[{"message":{"content":"{}"}}]}')) as call:
                    tr.request_translation('fake', model, [], routing.PROVIDERS[provider]['endpoint'])
                req = call.call_args.args[0]
                payload = json.loads(req.data)
                self.assertEqual(req.full_url, routing.PROVIDERS[provider]['endpoint'])
                self.assertEqual(req.headers['Authorization'], 'Bearer fake')
                self.assertNotIn('provider', payload)
                self.assertNotIn('tools', payload)
                self.assertEqual(payload['max_tokens'], 750 if provider == 'groq' else 2048)
                self.assertEqual(payload['response_format'], {'type': 'json_object'})
        with self.assertRaises(ValueError):
            tr.request_translation('fake', 'paid-other-model', [], routing.PROVIDERS['groq']['endpoint'])

    def test_fixture_rejects_wrong_numbers_identifiers_and_english(self):
        chunk = tr.article_blocks('\n\n'.join(row[0] for row in routing.FIXTURE))
        self.assertEqual(routing.grade(response(chunk), chunk, tr)[0], 1)
        for values in ([s.replace('25%', '50%').replace('429', '500').replace('/data/models', '/tmp/models') for s in GOOD],
                       [row[0] for row in routing.FIXTURE], ['错误的中文'] * 3):
            payload = {'translations': [{'id': b['id'], 'translationZh': values[i]} for i, b in enumerate(chunk)]}
            self.assertLess(routing.grade(payload, chunk, tr)[0], .95)

    def test_daily_evaluation_once_and_failed_quality_never_promoted(self):
        now = datetime(2026, 9, 27, 14, tzinfo=timezone.utc)
        def request(token, model, chunk, endpoint):
            return response(chunk, good=model.startswith('gemini')), model, {}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GEMINI_API_KEY':'fake', 'GROQ_API_KEY':'fake'}, clear=True), \
             patch.object(routing, 'TokenRateControl') as gate, patch.object(tr, 'request_translation', side_effect=request) as call:
            first = routing.prepare_routing(Path(directory), ['gemini', 'groq'], tr, now)
            self.assertEqual(call.call_count, 4)
            self.assertEqual(first['order'], ['gemini'])
            self.assertEqual(gate.return_value.acquire.call_count, 4)
            self.assertEqual(first['results'][0]['sampleTranslations']['b0001'], GOOD[0])
            second = routing.prepare_routing(Path(directory), ['gemini', 'groq'], tr, now + timedelta(hours=1))
            self.assertEqual(second, first)
            self.assertEqual(call.call_count, 4)
            routing.prepare_routing(Path(directory), ['gemini', 'groq'], tr, now + timedelta(days=1))
            self.assertEqual(call.call_count, 8)

    def test_evaluation_latency_excludes_our_own_limiter_wait(self):
        clock = [0.0]
        def acquire(*args, **kwargs):
            clock[0] += 30
        def request(token, model, chunk, endpoint):
            clock[0] += 2
            return response(chunk), model, {}
        with tempfile.TemporaryDirectory() as directory, patch.object(routing, 'TokenRateControl') as gate, \
             patch.object(routing.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(tr, 'request_translation', side_effect=request):
            gate.return_value.acquire.side_effect = acquire
            report = routing.prepare_routing(Path(directory), ['gemini'], tr)
            self.assertEqual([r['seconds'] for r in report['results']], [2, 2])

    def test_existing_cooldown_uses_no_probe_and_no_same_day_retries(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch.object(routing, 'TokenRateControl') as gate, \
             patch.object(tr, 'request_translation') as call:
            gate.return_value.acquire.side_effect = RateLimited(now + timedelta(hours=1))
            report = routing.prepare_routing(Path(directory), ['gemini'], tr, now)
            routing.prepare_routing(Path(directory), ['gemini'], tr, now)
            call.assert_not_called()
            self.assertEqual(report['order'], [])

    def test_unknown_429_does_not_try_another_model_or_provider_key(self):
        error = urllib.error.HTTPError('https://example.com', 429, 'busy', {}, io.BytesIO(b'{"error":{"code":429}}'))
        with tempfile.TemporaryDirectory() as directory, patch.object(routing, 'TokenRateControl') as gate, \
             patch.object(tr, 'request_translation', side_effect=error) as call:
            gate.return_value.penalize_error.return_value = RateLimited(datetime.now(timezone.utc) + timedelta(minutes=5))
            report = routing.prepare_routing(Path(directory), ['groq'], tr)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(report['order'], [])
            self.assertEqual(len(report['results']), 1)

    def test_json_generation_failure_is_quality_failure_not_account_limit(self):
        error = urllib.error.HTTPError('https://example.com', 400, 'bad JSON', {},
            io.BytesIO(b'{"error":{"code":"json_validate_failed"}}'))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GROQ_API_KEY':'fake'}, clear=True), \
             patch.object(routing, 'TokenRateControl') as gate, \
             patch.object(tr, 'request_translation', side_effect=[error, ({'translations': []}, 'm', {})]) as call:
            report = routing.prepare_routing(Path(directory), ['groq'], tr)
            self.assertEqual(call.call_count, 2)
            self.assertEqual(report['results'][0]['status'], 'quality_failed')
            self.assertEqual(report['results'][0]['score'], 0)
            gate.return_value.penalize_error.assert_not_called()
            gate.return_value.observe.assert_called()

    def test_migration_only_removes_exact_groq_json_error(self):
        for provider, reason, removed in [
            ('groq', 'HTTP 400 / json_validate_failed', True),
            ('groq', 'HTTP 429', False),
            ('groq', 'HTTP 401', False),
            ('gemini', 'HTTP 400 / json_validate_failed', False),
        ]:
            with self.subTest(provider=provider, reason=reason), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'rate.json'
                until = (datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()
                state = {'cooldowns': {'account': {'reason':reason, 'until':until},
                    'model:m': {'reason':'HTTP 429', 'until':until}},
                    'reservations': {'m':[{'at':1, 'tokens':2000}]},
                    'remote': {'m': {'tokensRemaining':0}}, 'requestsToday':41}
                path.write_text(json.dumps(state))
                gate = TokenRateControl(path, provider)
                self.assertEqual('account' not in gate.data['cooldowns'], removed)
                for key in ('reservations', 'remote', 'requestsToday'):
                    self.assertEqual(gate.data[key], state[key])
                self.assertEqual(gate.data['cooldowns']['model:m'], state['cooldowns']['model:m'])
                with self.assertRaises(RateLimited):
                    gate.blocked('m')

    def test_speed_cannot_overrule_quality_or_production_failure(self):
        good = {'status':'passed', 'fidelityProxy':1, 'seconds':20}
        fast_bad = {'status':'quality_failed', 'fidelityProxy':.7, 'seconds':.1}
        self.assertGreater(routing.score(good, []), routing.score(fast_bad, []))
        bad_history = [{'requests':20, 'successes':1, 'accepted':3, 'expected':60, 'limited':10}]
        self.assertGreater(routing.score(good, []), routing.score({**good, 'seconds':1}, bad_history))

    def test_main_uses_evaluated_order_and_keeps_going_after_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GEMINI_API_KEY':'fake-g', 'GROQ_API_KEY':'fake-q', 'BIGMODEL_API_KEY':'fake-b'}, clear=True), \
             patch.object(tr, 'STATE_FILE', Path(directory)/'state.json'), \
             patch.object(tr, 'RUNTIME_FILE', Path(directory)/'runtime.json'), \
             patch.object(tr, 'prepare_routing', return_value={'order':['groq','gemini','bigmodel'], 'evaluationDate':'2026-09-27'}), \
             patch.object(tr, 'run_provider', side_effect=[RuntimeError('failure'),0,0]) as run:
            self.assertEqual(tr.main(), 0)
            self.assertEqual([c.args[0] for c in run.call_args_list], ['groq','gemini','bigmodel'])

    def test_evaluate_only_never_runs_bulk_translation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GEMINI_API_KEY':'fake'}, clear=True), \
             patch.object(sys, 'argv', ['translate', '--evaluate-only']), \
             patch.object(tr, 'STATE_FILE', Path(directory)/'state.json'), \
             patch.object(tr, 'prepare_routing', return_value={'order':['gemini']}) as evaluate, \
             patch.object(tr, 'run_provider') as run:
            self.assertEqual(tr.main(), 0)
            evaluate.assert_called_once()
            run.assert_not_called()

    def test_groq_tokens_not_only_requests_and_reservations_persist(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory)/'rate.json'
            gate = TokenRateControl(path, 'groq')
            gate.acquire('model', now=now, tokens=5000)
            gate = TokenRateControl(path, 'groq')
            with self.assertRaises(RateLimited):
                gate.acquire('model', now=now, tokens=5000)
            self.assertEqual(len(gate.events('model', now)), 1)
            with self.assertRaises(RequestTooLarge):
                gate.acquire('model', now=now, tokens=7000)

    def test_groq_header_duration_and_remaining_not_treated_as_rpm(self):
        now = datetime.now(timezone.utc)
        self.assertAlmostEqual(duration('2m59.56s'), 179.56)
        with tempfile.TemporaryDirectory() as directory:
            gate = TokenRateControl(Path(directory)/'rate.json', 'groq')
            gate.observe('model', {'x-ratelimit-limit-requests':'100', 'x-ratelimit-remaining-tokens':'10',
                'x-ratelimit-reset-tokens':'2m59.56s'}, now)
            self.assertEqual(gate.limits('model')['rpd'], 75)
            self.assertEqual(gate.limits('model')['rpm'], 22)
            with self.assertRaises(RateLimited) as raised:
                gate.acquire('model', now=now, tokens=1000)
            self.assertAlmostEqual((raised.exception.until - now).total_seconds(), 179.56)

    def test_groq_rolling_day_budget_does_not_reset_at_utc_midnight(self):
        now = datetime(2026, 9, 27, 23, 59, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GROQ_RPD':'1'}, clear=True):
            gate = TokenRateControl(Path(directory)/'rate.json', 'groq')
            gate.data['reservations'] = {'m':[{'at':now.timestamp(), 'tokens':1000}]}
            with self.assertRaises(RateLimited) as raised:
                gate.acquire('m', now=now+timedelta(minutes=2), tokens=1000)
            self.assertGreaterEqual(raised.exception.until, now+timedelta(days=1))

    def test_pacific_reset_honors_dst_and_daily_quota(self):
        summer = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        winter = datetime(2026, 12, 27, 12, tzinfo=timezone.utc)
        self.assertEqual(next_pacific_day(summer).hour, 7)
        self.assertEqual(next_pacific_day(winter).hour, 8)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GEMINI_RPD':'1'}, clear=True):
            gate = TokenRateControl(Path(directory)/'rate.json', 'gemini')
            gate.data['reservations'] = {'m':[{'at':summer.timestamp(), 'tokens':1000}]}
            with self.assertRaises(RateLimited) as raised:
                gate.acquire('m', now=summer+timedelta(minutes=2), tokens=1000)
            self.assertEqual(raised.exception.until, next_pacific_day(summer))

    def test_gemini_429_daily_zero_and_retry_info_persist_without_other_model_bypass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'rate.json'
            gate = TokenRateControl(path, 'gemini')
            error = urllib.error.HTTPError('https://example.com',429,'limit',{},None)
            error.translation_error_details = [
                {'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':'7200s'},
                {'@type':'type.googleapis.com/google.rpc.QuotaFailure','violations':[
                    {'quotaId':'GenerateRequestsPerDayPerProjectPerModel-FreeTier','quotaValue':'0'}]}]
            limited = gate.penalize_error('m', error)
            self.assertGreaterEqual(limited.until, next_pacific_day(datetime.now(timezone.utc)))
            gate = TokenRateControl(path, 'gemini')
            self.assertEqual(gate.limits('m')['rpd'], 0)
            with self.assertRaises(RateLimited):
                gate.acquire('another-model', tokens=1000)

    def test_evaluation_reservation_covers_input_and_full_groq_output(self):
        chunk = tr.article_blocks('\n\n'.join(row[0] for row in routing.FIXTURE))
        groq = routing.reservation(tr.SYSTEM_PROMPT, chunk, 'groq')
        gemini = routing.reservation(tr.SYSTEM_PROMPT, chunk, 'gemini')
        self.assertEqual(groq-gemini, 750)
        self.assertLess(groq, 6000)
        self.assertGreater(gemini, len(tr.SYSTEM_PROMPT))

if __name__ == '__main__':
    unittest.main()
