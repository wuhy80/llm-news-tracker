import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import translate_articles as tr
from translation_models import ModelPool, PREFERRED, BigModelPool


def catalog():
    return [{'id': m, 'pricing': {'prompt': '0', 'completion': '0'},
             'architecture': {'input_modalities': ['text'], 'output_modalities': ['text']},
             'context_length': 32000} for m in PREFERRED]


class ResilienceTests(unittest.TestCase):
    def test_partial_output_preserves_good_blocks_and_rejects_duplicate_missing(self):
        blocks = tr.article_blocks('First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n\nFourth paragraph.')
        payload = {'translations': [
            {'id': 'b0001', 'translationZh': '第一段。'},
            {'id': 'b0002', 'translationZh': 'Second paragraph.'},
            {'id': 'b0003', 'translationZh': '第三段。'},
            {'id': 'b0003', 'translationZh': '重复。'},
            {'id': 'b9999', 'translationZh': '额外内容。'}]}
        good, _, failed = tr.normalize_partial_response(payload, blocks)
        self.assertEqual([b['id'] for b in good], ['b0001'])
        self.assertEqual(set(failed), {'b0002', 'b0003', 'b0004'})
        self.assertEqual(failed['b0002']['outputHasChinese'], False)

    def test_reference_is_exact_and_does_not_relax_prose_validation(self):
        self.assertEqual(tr.reference_translation('Code: github.com/dreamfast/abliterlitics'),
                         '代码：github.com/dreamfast/abliterlitics')
        self.assertEqual(tr.reference_translation('HuggingFace: DreamFast/Qwen-3.8-27b-abliterlitics'),
                         'Hugging Face 模型：DreamFast/Qwen-3.8-27b-abliterlitics')
        for text in ['Code: github.com/a/b is fast', 'This accuracy is 85%.', 'HuggingFace: a/b is fast']:
            self.assertIsNone(tr.reference_translation(text))

    def test_deferred_block_not_complete_other_blocks_continue_and_retry_is_single(self):
        blocks = tr.article_blocks('Bad block.\n\nGood block.')
        record = {'status': 'partial', 'totalBlocks': 2, 'blocks': []}
        tr.defer_blocks(record, {'b0001': {'reason': 'no Chinese'}}, 'test')
        self.assertEqual([b['id'] for b in tr.pending_chunk(blocks, record, 6000, 8)], ['b0002'])
        good, words, _ = tr.normalize_partial_response({'translations': [{'id': 'b0002', 'translationZh': '合格段落。'}]}, blocks[1:])
        tr.apply_chunk(record, good, words, 'test')
        self.assertEqual(record['status'], 'partial')
        self.assertEqual(tr.pending_chunk(blocks, record, 6000, 8), [])
        record['blockFailures']['b0001']['nextAttemptAt'] = '2020-01-01T00:00:00Z'
        self.assertEqual([b['id'] for b in tr.pending_chunk(blocks, record, 6000, 8)], ['b0001'])
        good, words, _ = tr.normalize_partial_response({'translations': [{'id': 'b0001', 'translationZh': '恢复的段落。'}]}, blocks[:1])
        tr.apply_chunk(record, good, words, 'test')
        self.assertEqual(record['status'], 'complete')
        self.assertEqual(record['blockFailures'], {})

    def test_bigmodel_payload_and_credentials_never_use_openrouter_fields(self):
        class Response(io.BytesIO):
            headers = {}
        result = {'choices': [{'message': {'content': '{"translations": []}'}}]}
        with patch.object(tr.urllib.request, 'urlopen', return_value=Response(json.dumps(result).encode())) as request:
            tr.request_translation('fake-bigmodel-key', tr.BIGMODEL_MODEL, [], tr.BIGMODEL_ENDPOINT)
        req = request.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, tr.BIGMODEL_ENDPOINT)
        self.assertEqual(req.headers['Authorization'], 'Bearer fake-bigmodel-key')
        self.assertEqual(payload['model'], 'glm-4.7-flash')
        self.assertNotIn('provider', payload)
        self.assertNotIn('tools', payload)
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        with self.assertRaises(ValueError):
            tr.request_translation('test', 'glm-4.7-flashx', [], tr.BIGMODEL_ENDPOINT)

    def test_old_content_cooldown_migrates_but_service_cooldown_remains(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'health.json'
            future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            path.write_text(json.dumps({'models': {
                PREFERRED[0]: {'lastError': 'HTTP 429', 'disabledUntil': future},
                PREFERRED[1]: {'lastError': 'block b0027 has no Chinese translation', 'disabledUntil': future}}}))
            pool = ModelPool(path, catalog=catalog())
            self.assertEqual(pool.select(), PREFERRED[1])
            self.assertIn('disabledUntil', pool.health['models'][PREFERRED[0]])

    def test_one_provider_failure_does_not_stop_other_provider(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'OPENROUTER_API_KEY': 'test-o', 'BIGMODEL_API_KEY': 'test-b'}, clear=True), \
            patch.object(tr, 'prepare_routing', side_effect=lambda root, providers, worker: {'order': providers}), \
            patch.object(tr, 'RUNTIME_FILE', Path(directory) / 'runtime.json'), \
            patch.object(tr, 'STATE_FILE', Path(directory) / 'state.json'), \
            patch.object(tr, 'run_provider', side_effect=[RuntimeError('service unavailable'), 0]) as run:
            self.assertEqual(tr.main(), 1)
            self.assertEqual([x.args[0] for x in run.call_args_list], ['bigmodel', 'openrouter'])
            state = json.loads((Path(directory) / 'runtime.json').read_text())
            self.assertEqual(state['status'], 'finished')
            self.assertNotIn('test-b', json.dumps(state))

    def test_missing_bigmodel_key_keeps_openrouter_and_reports_not_configured(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test'}, clear=True), \
            patch.object(tr, 'prepare_routing', side_effect=lambda root, providers, worker: {'order': providers}), \
            patch.object(tr, 'RUNTIME_FILE', Path(directory) / 'runtime.json'), \
            patch.object(tr, 'STATE_FILE', Path(directory) / 'state.json'), \
            patch.object(tr, 'run_provider', return_value=0) as run:
            self.assertEqual(tr.main(), 0)
            run.assert_called_once_with('openrouter')
            self.assertFalse(json.loads((Path(directory) / 'runtime.json').read_text())['bigmodelConfigured'])

    def test_real_worker_keeps_partial_results_and_processes_next_article(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            a = tr.article_blocks('Bad paragraph.\n\nGood paragraph.')
            b = tr.article_blocks('Another article.')
            one = {'status': 'partial', 'totalBlocks': 2, 'translatedBlocks': 0, 'blocks': []}
            two = {'status': 'partial', 'totalBlocks': 1, 'translatedBlocks': 0, 'blocks': []}
            records = [({'id': 'one'}, {}, a, root/'one.json', one), ({'id': 'two'}, {}, b, root/'two.json', two)]
            def candidates(*args):
                return [r for r in records if r[-1]['status'] != 'complete']
            calls = []
            def request(token, model, chunk, endpoint):
                calls.append(chunk)
                entries = [{'id': x['id'], 'translationZh': x['source'] if x['source'].startswith('Bad') else '合格的译文。'} for x in chunk]
                return {'translations': entries}, model, {}
            stack.enter_context(patch.object(tr, 'RateControl'))
            mocks = {'remove_stale_records': 0, 'load_news': {'items': []}, 'sync_requests': {},
                     'resolve_items': [], 'publish_queue': None, 'build_translation_index': {}}
            for name, value in mocks.items():
                stack.enter_context(patch.object(tr, name, return_value=value))
            for name, filename in [('STATE_FILE', 'state.json'), ('MODEL_HEALTH_FILE', 'health.json'), ('INDEX_FILE', 'index.json')]:
                stack.enter_context(patch.object(tr, name, root/filename))
            stack.enter_context(patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test'}, clear=True))
            stack.enter_context(patch.object(sys, 'argv', ['translate', '--request-limit', '5', '--interval', '0']))
            stack.enter_context(patch.object(tr, 'select_candidates', side_effect=candidates))
            stack.enter_context(patch.object(tr, 'request_translation', side_effect=request))
            stack.enter_context(patch('translation_models.fetch_catalog', return_value=catalog()))
            self.assertEqual(tr.run_provider(), 0)
            self.assertEqual(len(calls), 2)
            self.assertEqual(one['translatedBlocks'], 1)
            self.assertEqual(one['status'], 'partial')
            self.assertEqual(two['status'], 'complete')
            health = json.loads((root/'health.json').read_text())
            self.assertFalse(any('disabledUntil' in x for x in health['models'].values()))
            state = json.loads((root/'state.json').read_text())
            self.assertEqual(state['runTranslatedBlocks'], 2)
            self.assertEqual(state['runModelBlocks'], 2)
            self.assertEqual(state['runLocalBlocks'], 0)
            self.assertEqual(state['runCompletedArticles'], 1)

    def test_bigmodel_overload_code_is_preserved_for_service_classification(self):
        error = urllib.error.HTTPError(tr.BIGMODEL_ENDPOINT, 429, 'busy', {},
                                      io.BytesIO(b'{"error":{"code":"1305","message":"busy"}}'))
        tr.error_message(error)
        self.assertEqual(error.translation_provider_code, '1305')
        self.assertFalse(tr.shared_pool_limit(error))

    def test_bigmodel_health_is_independent_of_catalog(self):
        with tempfile.TemporaryDirectory() as directory, patch('translation_models.fetch_catalog', side_effect=RuntimeError('offline')):
            pool = BigModelPool(Path(directory)/'health.json')
            self.assertEqual(pool.select(), tr.BIGMODEL_MODEL)
            pool.failed(tr.BIGMODEL_MODEL, 'temporary error')
            with self.assertRaises(RuntimeError):
                pool.select()


if __name__ == '__main__':
    unittest.main()

