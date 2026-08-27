import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import news_store


class NewsStoreTests(unittest.TestCase):
    def test_writes_manifest_daily_indexes_and_complete_article(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "data/news.json"
            articles = root / "data/articles"
            data = {
                "generatedAt": "2026-08-27T00:00:00Z",
                "sources": [{"name": "Example", "count": 1}],
                "items": [{
                    "id": "0123456789ab",
                    "title": "Example article",
                    "summary": "Source summary",
                    "url": "https://example.com/article",
                    "source": "Example",
                    "sourceDomain": "example.com",
                    "publishedAt": "2026-08-27T03:10:20Z",
                    "category": "agent",
                    "tags": ["Agent"],
                    "score": 88,
                    "signal": "high",
                    "aiReview": {
                        "version": "v1",
                        "importanceLevel": 5,
                        "importanceScore": 91,
                        "summaryZh": "这是一篇完整的中文摘要。",
                        "glossary": [{"term": "Agent", "explanationZh": "智能体。"}],
                    },
                }],
            }

            result = news_store.save_news(data, manifest, articles)

            stored_manifest = json.loads(manifest.read_text(encoding="utf-8"))
            day = json.loads((root / "data/news/2026/08/27.json").read_text(encoding="utf-8"))
            article = json.loads((articles / "2026/08/27/0123456789ab.json").read_text(encoding="utf-8"))
            locator = json.loads((root / "data/article-index/01.json").read_text(encoding="utf-8"))
            self.assertEqual(result, {"items": 1, "days": 1, "prefixes": 1})
            self.assertNotIn("items", stored_manifest)
            self.assertEqual(stored_manifest["days"], {"2026-08-27": 1})
            self.assertNotIn("glossary", day["items"][0]["aiReview"])
            self.assertEqual(article["body"], "Source summary")
            self.assertEqual(article["archiveVersion"], 2)
            self.assertEqual(article["aiReview"]["glossary"][0]["term"], "Agent")
            self.assertEqual(locator, {"0123456789ab": "2026/08/27"})

    def test_load_news_hydrates_full_review_from_article(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "data/news.json"
            articles = root / "data/articles"
            data = {
                "generatedAt": "2026-08-27T00:00:00Z",
                "items": [{
                    "id": "abcdef012345", "title": "Example", "summary": "Summary",
                    "url": "https://example.com", "source": "Example", "sourceDomain": "example.com",
                    "publishedAt": "2026-08-26T00:00:00Z", "category": "release", "tags": [],
                    "score": 70, "signal": "medium",
                    "aiReview": {"version": "v1", "glossary": [{"term": "Token", "explanationZh": "词元。"}]},
                }],
            }
            news_store.save_news(data, manifest, articles)

            loaded = news_store.load_news(manifest, articles)

            self.assertEqual(loaded["items"][0]["aiReview"]["glossary"][0]["term"], "Token")

    def test_rejects_duplicate_article_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = {
                "id": "abcdef012345", "title": "Example", "summary": "Summary",
                "url": "https://example.com", "source": "Example",
                "publishedAt": "2026-08-26T00:00:00Z", "category": "release",
                "tags": [], "score": 70, "signal": "medium",
            }
            with self.assertRaisesRegex(ValueError, "duplicate article id"):
                news_store.save_news(
                    {"items": [item, dict(item)]},
                    root / "data/news.json",
                    root / "data/articles",
                )


if __name__ == "__main__":
    unittest.main()
