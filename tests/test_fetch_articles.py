import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_articles


class FetchArticlesTests(unittest.TestCase):
    def test_unattempted_summary_snapshot_is_archived_immediately(self):
        item = {
            "id": "0123456789ab",
            "publishedAt": "2025-01-01T00:00:00Z",
        }
        snapshot = {
            "contentKind": "summary",
            "archiveVersion": 2,
            "fetchedAt": "2026-08-28T14:22:39Z",
            "note": "archive:not attempted",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            with mock.patch.object(fetch_articles, "snapshot_path", return_value=path):
                result = fetch_articles.needs_archive(
                    item,
                    datetime(2026, 8, 28, 14, 23, tzinfo=timezone.utc),
                )

        self.assertTrue(result)

    def test_failed_fetch_marks_snapshots_that_need_the_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed = root / "failed.json"
            failed.write_text(json.dumps({"note": "page:HTTPError:HTTP Error 403: Forbidden"}),
                              encoding="utf-8")
            queued = root / "queued.json"
            queued.write_text(json.dumps({"note": "archive:not attempted"}), encoding="utf-8")

            self.assertTrue(fetch_articles.failed_fetch(failed))
            self.assertFalse(fetch_articles.failed_fetch(queued))
            self.assertFalse(fetch_articles.failed_fetch(root / "missing.json"))


if __name__ == "__main__":
    unittest.main()
