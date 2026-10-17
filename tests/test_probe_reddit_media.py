import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import probe_reddit_media as probe


class ProbeRedditMediaTests(unittest.TestCase):
    def write_snapshot(self, path, *urls):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'images': [{'src': u, 'originalUrl': u} for u in urls]}),
                        encoding='utf-8')

    def make_tree(self, root):
        data = root / 'data'
        (data / 'article-media').mkdir(parents=True)
        self.write_snapshot(
            data / 'articles/2026/10/02/aaaa.json',
            'https://preview.redd.it/irffy7x5v2th1.png?width=140&height=139&auto=webp&s=SIG1')
        self.write_snapshot(
            data / 'articles/2026/10/02/bbbb.json',
            'https://preview.redd.it/irffy7x5v2th1.png?width=140&height=139&auto=webp&s=SIG1',
            'https://preview.redd.it/other.png?width=640&crop=smart&auto=webp&s=SIG2')
        return data, data / 'article-media'

    def test_picks_the_smallest_variant_per_image_name(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            targets = probe.collect_targets(data, media)
            self.assertEqual(sorted(targets), ['irffy7x5v2th1.png', 'other.png'])
            self.assertEqual(targets['irffy7x5v2th1.png']['width'], 140)
            self.assertEqual(targets['irffy7x5v2th1.png']['uses'], 2)
            self.assertEqual(targets['other.png']['width'], 640)

    def test_candidates_offer_the_original_host_and_a_larger_width(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            targets = probe.collect_targets(data, media)
            pairs = probe.candidates(targets['irffy7x5v2th1.png'])
            labels = [label for label, _ in pairs]
            urls = [url for _, url in pairs]
            self.assertIn('original host i.redd.it', labels)
            self.assertIn('https://i.redd.it/irffy7x5v2th1.png', urls)
            self.assertTrue(any('width=1080' in url and 's=SIG1' in url for url in urls))
            self.assertTrue(any(label == 'params stripped' for label in labels))

    def test_external_preview_has_no_original_host_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            data, media = self.make_tree(Path(directory))
            self.write_snapshot(
                data / 'articles/2026/10/02/cccc.json',
                'https://external-preview.redd.it/abc.png?width=140&height=62&auto=webp&s=SIG3')
            targets = probe.collect_targets(data, media)
            labels = [label for label, _ in probe.candidates(targets['abc.png'])]
            self.assertNotIn('original host i.redd.it', labels)
            self.assertIn('width=1080 with signature', labels)

    def test_probe_reports_http_failures_instead_of_raising(self):
        import urllib.error
        with mock.patch.object(probe.urllib.request, 'urlopen',
                               side_effect=urllib.error.HTTPError('u', 403, 'no', {}, None)):
            result = probe.probe('https://preview.redd.it/x.png')
        self.assertEqual(result['status'], 403)
        self.assertEqual(result['bytes'], 0)

    def test_main_is_a_noop_without_a_media_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'nope'
            with mock.patch.object(probe, 'MEDIA_DIR', missing):
                self.assertEqual(probe.main([]), 0)


if __name__ == '__main__':
    unittest.main()
