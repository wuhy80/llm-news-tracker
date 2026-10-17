import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prune_orphan_media as prune


class PruneOrphanMediaTests(unittest.TestCase):
    """Fixture paths use the real data/article-media/ prefix because that is the
    string the detector matches on; only the part below the media directory is
    used when the referenced set and the files on disk are compared."""

    def make_tree(self, root):
        data = root / 'data'
        media = data / 'article-media'
        (media / '2026/09/01/aaaa').mkdir(parents=True)
        (media / '2026/09/01/aaaa/keep.png').write_bytes(b'k' * 10)
        (media / '2026/09/01/aaaa/orphan.png').write_bytes(b'o' * 990)
        (media / '2026/09/02/bbbb').mkdir(parents=True)
        (media / '2026/09/02/bbbb/lonely.gif').write_bytes(b'l' * 5000)
        snapshots = data / 'articles' / '2026' / '09' / '01'
        snapshots.mkdir(parents=True)
        self.write_snapshot(snapshots / 'aaaa.json', 'data/article-media/2026/09/01/aaaa/keep.png')
        return data, media

    def write_snapshot(self, path, src):
        path.write_text(json.dumps({'images': [{'src': src, 'alt': ''}]}), encoding='utf-8')

    def test_report_separates_orphans_from_referenced_files(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            report = prune.build_report(data, media)
            self.assertEqual(report['mediaFiles'], 3)
            self.assertEqual(report['keptFiles'], 1)
            self.assertEqual(report['orphanFiles'], 2)
            self.assertEqual(report['totalBytes'], 6000)
            self.assertEqual(report['orphanBytes'], 5990)
            self.assertEqual(report['orphanSharePercent'], 99.83)
            self.assertEqual(sorted(report['orphans']),
                             ['2026/09/01/aaaa/orphan.png', '2026/09/02/bbbb/lonely.gif'])

    def test_apply_removes_only_orphans_and_empty_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            removed = prune.apply_prune(media, prune.build_report(data, media)['orphans'])
            self.assertEqual(removed, 2)
            self.assertTrue((media / '2026/09/01/aaaa/keep.png').exists())
            self.assertTrue((media / '2026/09/01/aaaa').is_dir())
            self.assertFalse((media / '2026/09/02/bbbb').exists())

    def test_references_outside_the_article_snapshots_are_honoured(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            news = data / 'news'
            news.mkdir()
            (news / 'index.json').write_text(json.dumps({
                'cover': 'data/article-media/2026/09/02/bbbb/lonely.gif',
            }), encoding='utf-8')
            report = prune.build_report(data, media)
            self.assertEqual(report['orphanFiles'], 1)
            self.assertNotIn('2026/09/02/bbbb/lonely.gif', report['orphans'])

    def test_dangling_reference_is_reported_and_does_not_rescue_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            self.write_snapshot(data / 'articles' / '2026' / '09' / '01' / 'aaaa.json',
                                'data/article-media/2026/09/01/aaaa/gone.png')
            report = prune.build_report(data, media)
            self.assertEqual(report['danglingReferences'], ['2026/09/01/aaaa/gone.png'])
            # keep.png lost its only reference and must now count as an orphan.
            self.assertIn('2026/09/01/aaaa/keep.png', report['orphans'])

    def test_unusual_characters_in_a_referenced_name_do_not_orphan_it(self):
        # The dangerous direction: a name the regex mis-reads would be deleted
        # while a snapshot still points at it.
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            name = 'a b+c%d(1).png'
            (media / '2026/09/01/aaaa' / name).write_bytes(b'x' * 7)
            self.write_snapshot(data / 'articles' / '2026' / '09' / '01' / 'aaaa.json',
                                'data/article-media/2026/09/01/aaaa/' + name)
            report = prune.build_report(data, media)
            self.assertNotIn('2026/09/01/aaaa/' + name, report['orphans'])

    def test_media_directory_itself_is_never_scanned_for_references(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            report = prune.build_report(data, media)
            self.assertEqual(report['documentsScanned'], 1)

    def test_apply_refuses_to_delete_when_no_reference_was_found_at_all(self):
        # A detector that suddenly matches nothing must not wipe the archive.
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'data'
            media = data / 'article-media'
            (media / '2026/09/01/aaaa').mkdir(parents=True)
            lonely = media / '2026/09/01/aaaa/lonely.png'
            lonely.write_bytes(b'x' * 4)
            (data / 'articles').mkdir()
            with patch.object(prune, 'DATA_DIR', data), patch.object(prune, 'MEDIA_DIR', media):
                self.assertEqual(prune.main(['--apply']), 2)
            self.assertTrue(lonely.exists())


if __name__ == '__main__':
    unittest.main()
