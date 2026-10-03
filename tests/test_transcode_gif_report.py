import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import transcode_gif_report as gif


def fake_transcode(eighth=True):
    """Stand-in for ffmpeg: writes a file an eighth of the source size."""
    def run(source, destination):
        destination.write_bytes(b'x' * max(1, source.stat().st_size // 8))
    return run


class TranscodeGifReportTests(unittest.TestCase):
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
        (deep / 'big.gif').write_bytes(b'G' * 8000)
        (deep / 'small.gif').write_bytes(b'g' * 800)
        (deep / 'orphan.gif').write_bytes(b'O' * 4000)
        (deep / 'photo.png').write_bytes(b'P' * 2000)
        self.write_snapshot(data / 'articles/2026/09/01/aaaa.json',
                            'data/article-media/2026/09/01/aaaa/big.gif')
        self.write_snapshot(data / 'articles/2026/09/01/bbbb.json',
                            'data/article-media/2026/09/01/aaaa/small.gif')
        return data, media

    def collect(self, data, media):
        referenced, _ = gif.collect_referenced(data, media)
        files = gif.collect_files(media)
        return referenced, files

    def test_only_referenced_gifs_are_considered(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            referenced, files = self.collect(data, media)
            report = gif.build_report(media, referenced, files, transcode=fake_transcode())
            paths = sorted(row['path'] for row in report['rows'])
            self.assertEqual(paths, ['2026/09/01/aaaa/big.gif', '2026/09/01/aaaa/small.gif'])
            self.assertNotIn('2026/09/01/aaaa/orphan.gif', paths)   # unreferenced
            self.assertNotIn('2026/09/01/aaaa/photo.png', paths)    # not a GIF

    def test_savings_are_measured_and_related_to_total_media(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            referenced, files = self.collect(data, media)
            report = gif.build_report(media, referenced, files, transcode=fake_transcode())
            self.assertEqual(report['gifBytes'], 8800)
            self.assertEqual(report['mp4Bytes'], 1100)
            self.assertEqual(report['savingsBytes'], 7700)
            self.assertEqual(report['savingsPercent'], 87.5)
            self.assertEqual(report['transcoded'], 2)
            self.assertEqual(report['failed'], [])

    def test_limit_keeps_the_largest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            referenced, files = self.collect(data, media)
            report = gif.build_report(media, referenced, files, transcode=fake_transcode(), limit=1)
            self.assertEqual(report['referencedGifs'], 1)
            self.assertEqual(report['rows'][0]['path'], '2026/09/01/aaaa/big.gif')

    def test_a_failing_transcode_is_reported_and_excluded_from_totals(self):
        def explode(source, destination):
            raise RuntimeError('ffmpeg died')

        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            referenced, files = self.collect(data, media)
            report = gif.build_report(media, referenced, files, transcode=explode)
            self.assertEqual(report['transcoded'], 0)
            self.assertEqual(report['gifBytes'], 0)
            self.assertEqual(sorted(item['error'] for item in report['failed']),
                             ['RuntimeError', 'RuntimeError'])

    def test_main_refuses_without_ffmpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            with patch.object(gif, 'DATA_DIR', data), patch.object(gif, 'MEDIA_DIR', media), \
                 patch.object(gif.shutil, 'which', return_value=None):
                self.assertEqual(gif.main([]), 2)

    def test_main_reports_without_touching_the_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            before = {path: path.read_bytes() for path in media.rglob('*') if path.is_file()}
            original = gif.build_report

            def with_fake(md, ref, fl, **kwargs):
                kwargs.pop('transcode', None)
                return original(md, ref, fl, transcode=fake_transcode(), **kwargs)

            with patch.object(gif, 'DATA_DIR', data), patch.object(gif, 'MEDIA_DIR', media), \
                 patch.object(gif.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(gif, 'build_report', side_effect=with_fake):
                self.assertEqual(gif.main([]), 0)
            after = {path: path.read_bytes() for path in media.rglob('*') if path.is_file()}
            self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
