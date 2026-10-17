import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import media_recompression_report as recompress


def fake_converter(divisor):
    """Stand-in for ffmpeg: writes a file a fraction of the source size."""
    def convert(source, destination):
        destination.write_bytes(b'x' * max(1, source.stat().st_size // divisor))
    return convert


class MediaRecompressionReportTests(unittest.TestCase):
    """Fixture paths use the real data/article-media/ prefix because that is the
    string the stored documents contain."""

    def write_snapshot(self, path, *sources):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'images': [{'src': src} for src in sources]}), encoding='utf-8')

    def make_tree(self, root):
        data = root / 'data'
        media = data / 'article-media'
        deep = media / '2026' / '09' / '01' / 'aaaa'
        deep.mkdir(parents=True)
        (deep / 'shot.png').write_bytes(b'P' * 8000)
        (deep / 'anim.gif').write_bytes(b'G' * 4000)
        (deep / 'photo.jpeg').write_bytes(b'J' * 2000)
        (deep / 'orphan.png').write_bytes(b'O' * 1600)
        self.write_snapshot(
            data / 'articles/2026/09/01/aaaa.json',
            'data/article-media/2026/09/01/aaaa/shot.png',
            'data/article-media/2026/09/01/aaaa/anim.gif',
            'data/article-media/2026/09/01/aaaa/photo.jpeg')
        return data, media

    def collect(self, data, media):
        referenced, _ = recompress.collect_referenced(data, media)
        return referenced, recompress.collect_files(media)

    def strategies(self, divisor=8):
        return {
            'gif-to-mp4': {'extensions': ('.gif',), 'suffix': '.mp4',
                           'convert': fake_converter(divisor)},
            'png-to-webp': {'extensions': ('.png',), 'suffix': '.webp',
                            'convert': fake_converter(divisor)},
        }

    def test_each_strategy_only_touches_its_own_extensions_and_referenced_files(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            referenced, files = self.collect(data, media)
            report = recompress.build_report(media, referenced, files, strategies=self.strategies())
            gif = report['strategies']['gif-to-mp4']
            png = report['strategies']['png-to-webp']
            self.assertEqual([row['path'] for row in gif['rows']], ['2026/09/01/aaaa/anim.gif'])
            self.assertEqual([row['path'] for row in png['rows']], ['2026/09/01/aaaa/shot.png'])
            # the unreferenced PNG must not be a candidate
            self.assertEqual(png['candidates'], 1)

    def test_savings_and_share_of_media_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            referenced, files = self.collect(data, media)
            report = recompress.build_report(media, referenced, files, strategies=self.strategies())
            self.assertEqual(report['mediaBytes'], 15600)
            png = report['strategies']['png-to-webp']
            self.assertEqual(png['beforeBytes'], 8000)
            self.assertEqual(png['afterBytes'], 1000)
            self.assertEqual(png['savingsBytes'], 7000)
            self.assertEqual(png['savingsPercent'], 87.5)
            self.assertEqual(png['shareOfMediaPercent'], 44.9)

    def test_a_failing_conversion_is_reported_and_excluded_from_totals(self):
        def explode(source, destination):
            raise RuntimeError('ffmpeg died')

        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            referenced, files = self.collect(data, media)
            report = recompress.build_report(media, referenced, files, strategies={
                'png-to-webp': {'extensions': ('.png',), 'suffix': '.webp', 'convert': explode}})
            png = report['strategies']['png-to-webp']
            self.assertEqual(png['converted'], 0)
            self.assertEqual(png['beforeBytes'], 0)
            self.assertEqual([item['error'] for item in png['failed']], ['RuntimeError'])

    def test_limit_keeps_the_largest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            deep = media / '2026' / '09' / '02' / 'bbbb'
            deep.mkdir(parents=True)
            (deep / 'huge.png').write_bytes(b'H' * 20000)
            self.write_snapshot(data / 'articles/2026/09/02/bbbb.json',
                                'data/article-media/2026/09/02/bbbb/huge.png')
            referenced, files = self.collect(data, media)
            report = recompress.build_report(media, referenced, files,
                                             strategies=self.strategies(), limit=1)
            png = report['strategies']['png-to-webp']
            self.assertEqual(png['candidates'], 1)
            self.assertEqual(png['rows'][0]['path'], '2026/09/02/bbbb/huge.png')

    def test_main_refuses_without_ffmpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            with patch.object(recompress, 'DATA_DIR', data), patch.object(recompress, 'MEDIA_DIR', media), \
                 patch.object(recompress.shutil, 'which', return_value=None):
                self.assertEqual(recompress.main([]), 2)

    def test_main_rejects_an_unknown_strategy(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            with patch.object(recompress, 'DATA_DIR', data), patch.object(recompress, 'MEDIA_DIR', media), \
                 patch.object(recompress.shutil, 'which', return_value='/usr/bin/ffmpeg'):
                self.assertEqual(recompress.main(['--strategy', 'nope']), 2)

    def test_main_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            before = {path: path.read_bytes() for path in media.rglob('*') if path.is_file()}
            original = recompress.build_report

            def with_fakes(md, ref, fl, **kwargs):
                return original(md, ref, fl, strategies=self.strategies(), limit=0)

            with patch.object(recompress, 'DATA_DIR', data), patch.object(recompress, 'MEDIA_DIR', media), \
                 patch.object(recompress.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(recompress, 'build_report', side_effect=with_fakes):
                self.assertEqual(recompress.main([]), 0)
            after = {path: path.read_bytes() for path in media.rglob('*') if path.is_file()}
            self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
