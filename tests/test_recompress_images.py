import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recompress_images as webp


def fake_webp(fraction=0.25):
    """Stand-in for ffmpeg: writes a WebP a fraction of the source size."""
    def convert(source, destination, quality):
        destination.write_bytes(b'w' * max(1, int(source.stat().st_size * fraction)))
    return convert


class RecompressImagesTests(unittest.TestCase):
    """Fixture paths use the real data/article-media/ prefix because that is the
    string the stored documents contain."""

    def write_snapshot(self, path, src):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'images': [{'src': src}]}), encoding='utf-8')

    def make_tree(self, root):
        data = root / 'data'
        media = data / 'article-media'
        deep = media / '2026' / '09' / '01' / 'aaaa'
        deep.mkdir(parents=True)
        (deep / 'shot.png').write_bytes(b'P' * 8000)
        (deep / 'photo.jpeg').write_bytes(b'J' * 4000)
        (deep / 'orphan.png').write_bytes(b'O' * 4000)
        self.write_snapshot(data / 'articles/2026/09/01/aaaa.json',
                            'data/article-media/2026/09/01/aaaa/shot.png')
        self.write_snapshot(data / 'articles/2026/09/01/bbbb.json',
                            'data/article-media/2026/09/01/aaaa/photo.jpeg')
        return data, media

    def test_dry_run_reports_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            before = {p: p.read_bytes() for p in media.rglob('*') if p.is_file()}
            with patch.object(webp, 'to_webp', fake_webp()):
                report = webp.run(media, data, quality=90, commit=False)
            self.assertEqual(report['candidates'], 2)          # orphan.png excluded
            self.assertEqual(report['converted'], 2)
            self.assertEqual(report['beforeBytes'], 12000)
            self.assertEqual(report['afterBytes'], 3000)
            self.assertEqual(report['savingsBytes'], 9000)
            self.assertFalse(report['applied'])
            after = {p: p.read_bytes() for p in media.rglob('*') if p.is_file()}
            self.assertEqual(before, after)

    def test_apply_writes_webp_removes_original_and_repoints_the_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            with patch.object(webp, 'to_webp', fake_webp()):
                report = webp.run(media, data, quality=90, commit=True)
            self.assertEqual(report['brokenReferences'], [])
            self.assertEqual(report['documentsRewritten'], 2)
            self.assertTrue((media / '2026/09/01/aaaa/shot.webp').exists())
            self.assertFalse((media / '2026/09/01/aaaa/shot.png').exists())
            snapshot = (data / 'articles/2026/09/01/aaaa.json').read_text(encoding='utf-8')
            self.assertIn('2026/09/01/aaaa/shot.webp', snapshot)
            self.assertNotIn('shot.png', snapshot)
            # Regression guard: a WindowsPath string would write backslashes into
            # the document and silently break every path in it.
            self.assertNotIn('\\', snapshot)

    def test_a_conversion_that_is_not_smaller_leaves_the_file_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            with patch.object(webp, 'to_webp', fake_webp(fraction=2.0)):
                report = webp.run(media, data, quality=90, commit=True)
            self.assertEqual(report['converted'], 0)
            self.assertEqual(report['notSmaller'], 2)
            self.assertTrue((media / '2026/09/01/aaaa/shot.png').exists())
            self.assertFalse((media / '2026/09/01/aaaa/shot.webp').exists())
            snapshot = (data / 'articles/2026/09/01/aaaa.json').read_text(encoding='utf-8')
            self.assertIn('shot.png', snapshot)

    def test_an_existing_target_is_treated_as_a_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            (media / '2026/09/01/aaaa/shot.webp').write_bytes(b'existing')
            with patch.object(webp, 'to_webp', fake_webp()):
                report = webp.run(media, data, quality=90, commit=True)
            self.assertEqual(report['collisions'], 1)
            self.assertEqual(report['converted'], 1)          # only the jpeg went through
            self.assertEqual((media / '2026/09/01/aaaa/shot.webp').read_bytes(), b'existing')

    def test_orphans_and_non_images_are_not_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            (media / '2026/09/01/aaaa/clip.gif').write_bytes(b'G' * 9000)
            referenced, files = webp.collect_referenced(data, media), webp.collect_files(media)
            names = webp.candidates(referenced[0], files)
            self.assertEqual(names, ['2026/09/01/aaaa/shot.png', '2026/09/01/aaaa/photo.jpeg'])

    def test_main_refuses_without_ffmpeg_and_on_bad_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            with patch.object(webp, 'DATA_DIR', data), patch.object(webp, 'MEDIA_DIR', media), \
                 patch.object(webp.shutil, 'which', return_value=None):
                self.assertEqual(webp.main([]), 2)
            with patch.object(webp, 'DATA_DIR', data), patch.object(webp, 'MEDIA_DIR', media), \
                 patch.object(webp.shutil, 'which', return_value='/usr/bin/ffmpeg'):
                self.assertEqual(webp.main(['--quality', '500']), 2)

    def test_apply_through_main_reports_no_broken_references(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            with patch.object(webp, 'DATA_DIR', data), patch.object(webp, 'MEDIA_DIR', media), \
                 patch.object(webp.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(webp, 'to_webp', fake_webp()):
                self.assertEqual(webp.main(['--apply']), 0)
            referenced, _ = webp.collect_referenced(data, media)
            self.assertEqual(sorted(referenced - set(webp.collect_files(media))), [])


if __name__ == '__main__':
    unittest.main()
