import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_news


class FetchNewsTests(unittest.TestCase):
    def test_existing_article_keeps_archive_date_and_ai_review(self):
        item = {
            "id": "0123456789ab",
            "publishedAt": "2026-08-23T10:06:41Z",
            "title": "Updated title",
        }
        review = {"version": "ai-editor-v3"}
        previous = {
            "id": item["id"],
            "publishedAt": "2026-08-24T07:31:41Z",
            "aiReview": review,
        }

        result = fetch_news.preserve_archive_metadata(item, previous)

        self.assertIs(result, item)
        self.assertEqual(result["publishedAt"], previous["publishedAt"])
        self.assertIs(result["aiReview"], review)

    def test_new_article_keeps_fetched_publication_date(self):
        item = {"id": "0123456789ab", "publishedAt": "2026-08-23T10:06:41Z"}

        result = fetch_news.preserve_archive_metadata(item, None)

        self.assertEqual(result["publishedAt"], "2026-08-23T10:06:41Z")


if __name__ == "__main__":
    unittest.main()
