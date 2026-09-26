import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import article_store
import backfill_article_media as repair
from article_media import extract_media_refs


class HistoricalMediaTests(unittest.TestCase):
    def make_snapshot(self, root, name, **extra):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {'id': path.stem, 'url': 'https://example.com/article', 'publishedAt': '2015-12-11T00:00:00Z', 'contentKind': 'feed', 'body': 'Original  text\n\n\nwith spacing', 'fetchedAt': 'original-time', 'customField': {'keep': True}, 'bodyFormatVersion': 1, **extra}
        path.write_text(json.dumps(record))
        return path, record

    def test_old_year_orphan_and_previously_rechecked_are_included(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p, _ = self.make_snapshot(root, '2015/12/11/0123456789ab.json', mediaRecheckedAt='2026-09-25')
            candidates = repair.archive_candidates(root, {})
            self.assertEqual([path for _, path in candidates], [p])

    def test_completed_and_summary_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.make_snapshot(root, 'done.json', mediaFormatVersion=article_store.MEDIA_FORMAT_VERSION)
            self.make_snapshot(root, 'summary.json', contentKind='summary')
            self.assertEqual(repair.archive_candidates(root, {}), [])

    def test_failed_cooldown_and_never_attempted_priority(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.make_snapshot(root, 'a.json')
            self.make_snapshot(root, 'b.json')
            state = {'failures': {'a.json': {'attemptedAt': article_store.utc_now()}}}
            self.assertEqual([p.name for _, p in repair.archive_candidates(root, state)], ['b.json'])
            state['failures']['a.json']['attemptedAt'] = '2000-01-01T00:00:00Z'
            self.assertEqual([p.name for _, p in repair.archive_candidates(root, state)], ['b.json', 'a.json'])

    def test_repair_preserves_every_non_media_field_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            path, before = self.make_snapshot(Path(d), 'orphan.json', images=[{'src': 'https://example.com/wrong.jpg'}])
            with patch.object(repair, 'fetch_page_text', return_value=('different body', before['url'])):
                article_store.FETCHED_IMAGE_REFS[before['url']] = []
                article_store.FETCHED_VIDEO_REFS[before['url']] = []
                repair.media_backfill_item(before, recheck=True, path=path)
            after = json.loads(path.read_text())
            for k,v in before.items():
                if k != 'images': self.assertEqual(after[k],v,k)
            self.assertEqual(after['images'], [])

    def test_unknown_page_scope_is_not_successful_empty_extraction(self):
        with self.assertRaises(ValueError):
            extract_media_refs('<html><body><div><img src="/photo.jpg"></div></body></html>', 'https://example.com')

    def test_locally_cached_incompatible_image_not_requeued_forever(self):
        self.assertFalse(repair.has_problematic_images({'images': [{'src': 'data/article-media/pic.jpg', 'originalUrl': 'https://i.qbitai.com/wp-content/uploads/2026/09/chart.webp'}]}))
