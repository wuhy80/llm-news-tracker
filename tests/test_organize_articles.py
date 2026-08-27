import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import organize_articles


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class OrganizeArticlesTests(unittest.TestCase):
    def test_moves_news_and_orphan_snapshots_by_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            articles = root / "articles"
            news_file = root / "news.json"
            write_json(news_file, {
                "items": [{"id": "0123456789ab", "publishedAt": "2026-08-27T03:10:20Z"}],
            })
            write_json(articles / "0123456789ab.json", {"id": "0123456789ab", "fetchedAt": "2026-08-28T00:00:00Z"})
            write_json(articles / "abcdef012345.json", {"id": "abcdef012345", "fetchedAt": "2025-04-03T12:00:00Z"})

            result = organize_articles.organize_articles(news_file, articles)

            self.assertEqual(result, {"total": 2, "moved": 2, "unchanged": 0, "orphans": 1})
            self.assertTrue((articles / "2026/08/27/0123456789ab.json").exists())
            self.assertTrue((articles / "2025/04/03/abcdef012345.json").exists())

    def test_collision_is_rejected_before_any_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            articles = root / "articles"
            news_file = root / "news.json"
            write_json(news_file, {
                "items": [{"id": "0123456789ab", "publishedAt": "2026-08-27T03:10:20Z"}],
            })
            flat = articles / "0123456789ab.json"
            nested = articles / "2026/08/27/0123456789ab.json"
            write_json(flat, {"id": "0123456789ab", "fetchedAt": "2026-08-28T00:00:00Z"})
            write_json(nested, {"id": "0123456789ab", "fetchedAt": "2026-08-28T00:00:00Z"})

            with self.assertRaisesRegex(ValueError, "destination collision"):
                organize_articles.organize_articles(news_file, articles)

            self.assertTrue(flat.exists())
            self.assertTrue(nested.exists())

    def test_invalid_date_is_rejected_before_any_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            articles = root / "articles"
            news_file = root / "news.json"
            write_json(news_file, {
                "items": [
                    {"id": "0123456789ab", "publishedAt": "2026-08-27T03:10:20Z"},
                    {"id": "abcdef012345", "publishedAt": "not-a-date"},
                ],
            })
            first = articles / "0123456789ab.json"
            second = articles / "abcdef012345.json"
            write_json(first, {"id": "0123456789ab"})
            write_json(second, {"id": "abcdef012345"})

            with self.assertRaisesRegex(ValueError, "no valid date"):
                organize_articles.organize_articles(news_file, articles)

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())


if __name__ == "__main__":
    unittest.main()
