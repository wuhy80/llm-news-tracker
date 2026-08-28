import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_news


class FetchNewsTests(unittest.TestCase):
    def test_canonical_url_removes_tracking_and_normalizes_order(self):
        first = "HTTPS://Example.COM:443/posts/launch/?utm_source=rss&b=2&a=1#details"
        second = "https://example.com/posts/launch?a=1&b=2"

        self.assertEqual(fetch_news.canonical_url(first), fetch_news.canonical_url(second))
        self.assertEqual(fetch_news.canonical_url("https://example.com:invalid/news"), "")

    def test_url_index_matches_same_article_with_a_different_title(self):
        previous = {
            "id": "0123456789ab",
            "title": "Original title",
            "url": "https://example.com/news/item?utm_medium=feed",
        }

        _, by_url = fetch_news.previous_indexes([previous])

        self.assertIs(by_url[fetch_news.canonical_url("https://example.com/news/item")], previous)

    def test_content_comparison_ignores_item_order_but_detects_changes(self):
        first = [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}]
        reordered = list(reversed(first))
        changed = copy.deepcopy(first)
        changed[0]["title"] = "Updated"

        self.assertFalse(fetch_news.news_content_changed(first, reordered))
        self.assertTrue(fetch_news.news_content_changed(first, changed))

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
            "score": 91,
            "signal": "high",
            "aiReview": review,
        }

        result = fetch_news.preserve_archive_metadata(item, previous)

        self.assertIs(result, item)
        self.assertEqual(result["publishedAt"], previous["publishedAt"])
        self.assertEqual(result["score"], previous["score"])
        self.assertEqual(result["signal"], previous["signal"])
        self.assertIs(result["aiReview"], review)

    def test_new_article_keeps_fetched_publication_date(self):
        item = {"id": "0123456789ab", "publishedAt": "2026-08-23T10:06:41Z"}

        result = fetch_news.preserve_archive_metadata(item, None)

        self.assertEqual(result["publishedAt"], "2026-08-23T10:06:41Z")


if __name__ == "__main__":
    unittest.main()
