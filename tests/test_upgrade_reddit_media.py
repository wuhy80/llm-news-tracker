import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import upgrade_reddit_media as upgrade

PREVIEW_SMALL = 'https://preview.redd.it/abc.png?width=140&height=139&auto=webp&s=SIG'
PREVIEW_BIG = 'https://preview.redd.it/big.png?width=640&crop=smart&s=SIG2'


class UpgradeRedditMediaTests(unittest.TestCase):
    def write_snapshot(self, path, src, original=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'images': [{'src': src, 'originalUrl': original or src}]}),
                        encoding='utf-8')

    def make_tree(self, root):
        data = root / 'data'
        (data / 'article-media').mkdir(parents=True)
        self.write_snapshot(data / 'articles/2026/10/02/aaaa.json', PREVIEW_SMALL)
        self.write_snapshot(data / 'articles/2026/10/02/bbbb.json', PREVIEW_SMALL)
        self.write_snapshot(data / 'articles/2026/10/02/cccc.json', PREVIEW_BIG)
        return data, data / 'article-media'

    def test_only_small_preview_srcs_become_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            candidates, documents = upgrade.collect_candidates(data, media)
            self.assertEqual(list(candidates), [PREVIEW_SMALL])
            self.assertEqual(candidates[PREVIEW_SMALL], 'https://i.redd.it/abc.png')
            self.assertEqual(len(documents), 3)

    def test_only_verified_originals_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            plan = upgrade.build_plan(data, media, checker=lambda url: {'ok': False, 'status': 404})
            self.assertEqual(plan['accepted'], 0)
            self.assertEqual(plan['rejected'][0]['status'], 404)
            plan = upgrade.build_plan(data, media, checker=lambda url: {'ok': True, 'status': 200})
            self.assertEqual(plan['accepted'], 1)

    def test_rewrite_touches_src_and_leaves_original_url_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            plan = upgrade.build_plan(data, media, checker=lambda url: {'ok': True, 'status': 200})
            changed = upgrade.rewrite(plan['documents'], plan['mapping'])
            self.assertEqual(changed, 2)
            text = (data / 'articles/2026/10/02/aaaa.json').read_text(encoding='utf-8')
            self.assertIn('"src": "https://i.redd.it/abc.png"', text)
            self.assertIn('"originalUrl": "' + PREVIEW_SMALL + '"', text)

    def test_a_rejected_candidate_is_never_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            plan = upgrade.build_plan(data, media, checker=lambda url: {'ok': False, 'status': 404})
            changed = upgrade.rewrite(plan['documents'], plan['mapping'])
            self.assertEqual(changed, 0)
            text = (data / 'articles/2026/10/02/aaaa.json').read_text(encoding='utf-8')
            self.assertIn(PREVIEW_SMALL, text)

    def test_main_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            before = {p: p.read_bytes() for p in (data / 'articles').rglob('*.json')}
            with mock.patch.object(upgrade, 'DATA_DIR', data), \
                 mock.patch.object(upgrade, 'MEDIA_DIR', media), \
                 mock.patch.object(upgrade, 'verify', lambda url: {'ok': True, 'status': 200}):
                self.assertEqual(upgrade.main([]), 0)
            after = {p: p.read_bytes() for p in (data / 'articles').rglob('*.json')}
            self.assertEqual(before, after)

    def test_main_apply_rewrites_the_verified_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            with mock.patch.object(upgrade, 'DATA_DIR', data), \
                 mock.patch.object(upgrade, 'MEDIA_DIR', media), \
                 mock.patch.object(upgrade, 'verify', lambda url: {'ok': True, 'status': 200}):
                self.assertEqual(upgrade.main(['--apply']), 0)
            text = (data / 'articles/2026/10/02/bbbb.json').read_text(encoding='utf-8')
            self.assertIn('https://i.redd.it/abc.png', text)
            big = (data / 'articles/2026/10/02/cccc.json').read_text(encoding='utf-8')
            self.assertIn(PREVIEW_BIG, big)


if __name__ == '__main__':
    unittest.main()
