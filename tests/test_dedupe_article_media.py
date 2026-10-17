import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dedupe_article_media as dedupe


class DedupeArticleMediaTests(unittest.TestCase):
    """Fixture paths use the real data/article-media/ prefix because that is the
    string the stored documents contain; only the part below the media
    directory is used when files on disk are compared."""

    def write_snapshot(self, path, src):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'images': [{'src': src, 'alt': ''}]}), encoding='utf-8')

    def make_tree(self, root):
        data = root / 'data'
        media = data / 'article-media'
        (media / '2026/09/01/aaaa').mkdir(parents=True)
        (media / '2026/09/02/bbbb').mkdir(parents=True)
        (media / '2026/09/01/aaaa/shared.gif').write_bytes(b'S' * 1000)
        (media / '2026/09/02/bbbb/shared.gif').write_bytes(b'S' * 1000)
        (media / '2026/09/01/aaaa/unique.png').write_bytes(b'U' * 500)
        self.write_snapshot(data / 'articles/2026/09/01/aaaa.json',
                            'data/article-media/2026/09/01/aaaa/shared.gif')
        self.write_snapshot(data / 'articles/2026/09/02/bbbb.json',
                            'data/article-media/2026/09/02/bbbb/shared.gif')
        self.write_snapshot(data / 'articles/2026/09/01/uniq.json',
                            'data/article-media/2026/09/01/aaaa/unique.png')
        return data, media

    def test_identical_contents_are_grouped_and_measured(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            plan = dedupe.build_plan(data, media)
            self.assertEqual(plan['mediaFiles'], 3)
            self.assertEqual(plan['referencedPaths'], 3)
            self.assertEqual(plan['distinctContents'], 2)
            self.assertEqual(plan['duplicateFiles'], 1)
            self.assertEqual(plan['savingsBytes'], 1000)
            self.assertEqual(plan['duplicates'],
                             {'2026/09/02/bbbb/shared.gif': '2026/09/01/aaaa/shared.gif'})

    def test_apply_repoints_every_holder_and_deletes_the_extra_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            plan = dedupe.build_plan(data, media)
            result = dedupe.apply_plan(data, media, {'duplicates': plan['duplicates']})
            self.assertEqual(result['filesDeleted'], 1)
            self.assertEqual(result['brokenReferences'], [])
            self.assertTrue((media / '2026/09/01/aaaa/shared.gif').exists())
            self.assertFalse((media / '2026/09/02/bbbb').exists())
            repointed = (data / 'articles/2026/09/02/bbbb.json').read_text(encoding='utf-8')
            self.assertIn('2026/09/01/aaaa/shared.gif', repointed)
            self.assertNotIn('2026/09/02/bbbb', repointed)

    def test_unreferenced_duplicate_is_never_the_survivor_and_is_left_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            (media / '2026/09/03/cccc').mkdir(parents=True)
            orphan = media / '2026/09/03/cccc/shared.gif'
            orphan.write_bytes(b'S' * 1000)
            plan = dedupe.build_plan(data, media)
            self.assertEqual(plan['duplicateFiles'], 1)
            self.assertNotIn('2026/09/03/cccc/shared.gif', plan['duplicates'])
            self.assertNotIn('cccc', list(plan['duplicates'].values())[0])
            dedupe.apply_plan(data, media, {'duplicates': plan['duplicates']})
            self.assertTrue(orphan.exists())

    def test_same_size_different_bytes_are_not_collapsed(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            (media / '2026/09/01/aaaa/other.png').write_bytes(b'X' * 1000)
            self.write_snapshot(data / 'articles/2026/09/01/other.json',
                                'data/article-media/2026/09/01/aaaa/other.png')
            plan = dedupe.build_plan(data, media)
            self.assertEqual(plan['duplicateFiles'], 1)
            self.assertNotIn('2026/09/01/aaaa/other.png', plan['duplicates'])

    def test_rewrite_preserves_every_other_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            target = data / 'articles/2026/09/02/bbbb.json'
            payload = json.dumps({'note': '中文说明', 'images': [
                {'src': 'data/article-media/2026/09/02/bbbb/shared.gif', 'alt': '图'}]},
                ensure_ascii=False, indent=2).encode('utf-8').replace(b'\n', b'\r\n')
            target.write_bytes(payload)
            plan = dedupe.build_plan(data, media)
            dedupe.apply_plan(data, media, {'duplicates': plan['duplicates']})
            after = target.read_bytes()
            self.assertIn(b'\r\n', after)                       # line endings kept
            self.assertIn('中文说明'.encode('utf-8'), after)      # unrelated bytes kept
            self.assertIn(b'2026/09/01/aaaa/shared.gif', after)  # path repointed
            self.assertNotIn(b'2026/09/02/bbbb/shared.gif', after)

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            snapshot = data / 'articles/2026/09/02/bbbb.json'
            before = snapshot.read_bytes()
            with patch.object(dedupe, 'DATA_DIR', data), patch.object(dedupe, 'MEDIA_DIR', media):
                self.assertEqual(dedupe.main([]), 0)
            self.assertTrue((media / '2026/09/02/bbbb/shared.gif').exists())
            self.assertEqual(snapshot.read_bytes(), before)

    def test_apply_through_main_collapses_and_stays_consistent(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            with patch.object(dedupe, 'DATA_DIR', data), patch.object(dedupe, 'MEDIA_DIR', media):
                self.assertEqual(dedupe.main(['--apply']), 0)
            referenced, _ = dedupe.collect_referenced(data, media)
            files = dedupe.collect_files(media)
            self.assertEqual(sorted(referenced - set(files)), [])
            self.assertEqual(len(files), 2)


if __name__ == '__main__':
    unittest.main()
