import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import translate_articles as tr
import provider_routing as routing


class ParallelTests(unittest.TestCase):
    def test_real_workers_overlap_without_duplicate_articles_and_keep_metrics(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            records = []
            for ident in ('one', 'two'):
                records.append(({'id': ident}, {}, tr.article_blocks(ident + ' English paragraph.'),
                    root / (ident + '.json'), {'status': 'partial', 'totalBlocks': 1,
                    'translatedBlocks': 0, 'blocks': []}))
            coordinator = tr.ArticleCoordinator()
            barrier = threading.Barrier(2)
            active = set()
            seen = []
            guard = threading.Lock()
            def candidates(items, *args):
                ids = {item['id'] for item in items}
                return [r for r in records if r[0]['id'] in ids and r[-1]['status'] != 'complete']
            def request(token, model, chunk, endpoint):
                source = chunk[0]['source']
                with guard:
                    self.assertNotIn(source, active)
                    active.add(source)
                    seen.append((token, source))
                # This fails if requests are serialized behind a global lock.
                barrier.wait(timeout=3)
                with guard:
                    active.remove(source)
                return {'translations': [{'id': chunk[0]['id'], 'translationZh': '合格的中文译文。'}]}, model, {}
            for name, value in {'remove_stale_records': 0, 'load_news': {'items': [r[0] for r in records]},
                                'sync_requests': {}, 'publish_queue': None, 'build_translation_index': {}}.items():
                stack.enter_context(patch.object(tr, name, return_value=value))
            stack.enter_context(patch.object(tr, 'resolve_items', side_effect=lambda items, _: items))
            stack.enter_context(patch.object(tr, 'select_candidates', side_effect=candidates))
            stack.enter_context(patch.object(tr, 'request_translation', side_effect=request))
            stack.enter_context(patch.object(tr, 'RateControl'))
            stack.enter_context(patch.object(tr, 'ModelPool'))
            tr.ModelPool.return_value.select.return_value = 'approved-test-model'
            for name, filename in [('STATE_FILE', 'state.json'), ('MODEL_HEALTH_FILE', 'health.json'), ('INDEX_FILE', 'index.json')]:
                stack.enter_context(patch.object(tr, name, root / filename))
            stack.enter_context(patch.dict(os.environ, {'BIGMODEL_API_KEY': 'b', 'OPENROUTER_API_KEY': 'o'}, clear=True))
            stack.enter_context(patch.object(sys, 'argv', ['translate', '--request-limit', '1', '--interval', '0']))
            with ThreadPoolExecutor(2) as executor:
                futures = [executor.submit(tr.run_provider, p, coordinator) for p in ('bigmodel', 'openrouter')]
                self.assertEqual([f.result(timeout=5) for f in futures], [0, 0])
            coordinator.finish()
            self.assertEqual(len({s for _, s in seen}), 2)
            self.assertEqual({p for p, _ in seen}, {'b', 'o'})
            self.assertTrue(all(r[-1]['status'] == 'complete' for r in records))
            self.assertEqual(coordinator.owners, {})
            tr.remove_stale_records.assert_called_once()
            tr.build_translation_index.assert_called_once()
            metrics = json.loads((root/'provider-metrics.json').read_text())
            self.assertEqual(sum(b['requests'] for day in metrics.values() for b in day.values()), 2)
            for filename in ('state.json', 'bigmodel-state.json'):
                self.assertEqual(json.loads((root/filename).read_text())['requestsToday'], 1)

    def test_waiting_worker_takes_released_partial_article_with_fresh_state(self):
        coordinator = tr.ArticleCoordinator()
        coordinator.data = {'items': [{'id': 'a'}]}
        coordinator.requests = {}
        record = {'blocks': []}
        selected = threading.Event()
        def candidates(items, *args):
            if threading.current_thread().name.startswith('ThreadPoolExecutor'):
                selected.set()
            return [(items[0], {}, [], Path('a'), dict(record))] if items else []
        with patch.object(tr, 'select_candidates', side_effect=candidates):
            coordinator.claim('bigmodel', 'm', set(), time.monotonic()+5)
            with ThreadPoolExecutor(1) as executor:
                future = executor.submit(coordinator.claim, 'groq', 'm2', set(), time.monotonic()+5)
                self.assertTrue(selected.wait(2))
                self.assertFalse(future.done())
                record['blocks'] = ['saved translation']
                coordinator.release('bigmodel')
                self.assertEqual(future.result(timeout=2)[-1]['blocks'], ['saved translation'])
            self.assertEqual(coordinator.owners, {'a': 'groq'})

    def test_main_starts_all_providers_before_waiting_for_results(self):
        barrier = threading.Barrier(2)
        def worker(provider, coordinator):
            barrier.wait(timeout=2)
            return 0
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'BIGMODEL_API_KEY': 'test', 'OPENROUTER_API_KEY': 'test'}, clear=True), \
            patch.object(tr, 'prepare_routing', return_value={'order': ['bigmodel', 'openrouter']}), \
            patch.object(tr, 'STATE_FILE', Path(directory)/'state.json'), \
            patch.object(tr, 'RUNTIME_FILE', Path(directory)/'runtime.json'), \
            patch.object(tr, 'run_provider', side_effect=worker):
            self.assertEqual(tr.main(), 0)
            state = json.loads((Path(directory)/'runtime.json').read_text())
            self.assertEqual(state['executionMode'], 'parallel')
            self.assertEqual(state['maxConcurrentProviders'], 2)

    def test_exception_releases_ownership(self):
        coordinator = tr.ArticleCoordinator()
        coordinator.owners['a'] = 'groq'
        with patch.object(tr, '_run_provider', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                tr.run_provider('groq', coordinator)
        self.assertEqual(coordinator.owners, {})

    def test_parallel_metric_updates_are_not_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ThreadPoolExecutor(4) as executor:
                futures = [executor.submit(routing.record_attempt, root, p, 'm', 1, 1, .1, 'success')
                           for _ in range(25) for p in ('groq', 'gemini', 'bigmodel', 'openrouter')]
                for future in futures:
                    future.result()
            metrics = json.loads((root/'provider-metrics.json').read_text())
            self.assertEqual(sum(b['requests'] for d in metrics.values() for b in d.values()), 100)
            self.assertTrue(all(b['requests'] == 25 for d in metrics.values() for b in d.values()))


if __name__ == '__main__':
    unittest.main()
