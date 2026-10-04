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
            self.assertEqual(result, {"items": 1, "days": 1, "prefixes": 1, "unlistedCommunity": 0})
            self.assertNotIn("items", stored_manifest)
            self.assertEqual(stored_manifest["days"], {"2026-08-27": 1})
            self.assertNotIn("glossary", day["items"][0]["aiReview"])
            self.assertEqual(article["body"], "Source summary")
            self.assertEqual(article["archiveVersion"], 2)
            self.assertEqual(article["bodyFormatVersion"], news_store.BODY_FORMAT_VERSION)
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

    def test_removes_article_snapshots_not_referenced_by_current_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "data/news.json"
            articles = root / "data/articles"
            stale = articles / "2026/08/25/deadbeefcafe.json"
            stale.parent.mkdir(parents=True)
            stale.write_text("{}", encoding="utf-8")
            item = {
                "id": "abcdef012345", "title": "Current", "summary": "Summary",
                "url": "https://example.com/current", "source": "Example",
                "sourceDomain": "example.com", "publishedAt": "2026-08-26T00:00:00Z",
                "category": "release", "tags": [], "score": 70, "signal": "medium",
            }

            news_store.save_news({"items": [item]}, manifest, articles)

            self.assertFalse(stale.exists())
            self.assertTrue((articles / "2026/08/26/abcdef012345.json").exists())


    def write_snapshot(self, articles, article_id, **fields):
        path = articles / f"2026/08/27/{article_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": article_id, "fetchedAt": "2026-08-27T00:00:00Z", **fields}),
                        encoding="utf-8")
        return path

    def community_item(self, article_id="abcdef012345", **overrides):
        item = {
            "id": article_id, "title": "A one-line reddit post", "summary": "one line",
            "url": f"https://www.reddit.com/r/LocalLLaMA/comments/x/{article_id}/",
            "source": "Reddit · LocalLLaMA", "sourceDomain": "reddit.com",
            "publishedAt": "2026-08-27T03:10:20Z", "category": "industry",
            "tags": [], "score": 60, "signal": "normal",
        }
        item.update(overrides)
        return item

    def test_an_attempted_community_post_without_a_body_is_unlisted_but_keeps_its_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "data/news.json"
            articles = root / "data/articles"
            path = self.write_snapshot(articles, "abcdef012345", contentKind="summary",
                                       note="community:ValueError:community body not found")

            result = news_store.save_news({"items": [self.community_item()]}, manifest, articles)

            self.assertEqual(result["items"], 0)
            self.assertEqual(result["unlistedCommunity"], 1)
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["itemCount"], 0)
            self.assertTrue(path.exists(),
                            "the snapshot must survive so the archive retry cooldown still applies")

    def test_a_queued_community_post_is_still_listed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "data/news.json"
            articles = root / "data/articles"
            self.write_snapshot(articles, "abcdef012345", contentKind="summary",
                                note="archive:not attempted")

            result = news_store.save_news({"items": [self.community_item()]}, manifest, articles)

            self.assertEqual(result["items"], 1)
            self.assertEqual(result["unlistedCommunity"], 0)
            self.assertTrue((root / "data/news/2026/08/27.json").exists())

    def test_a_community_post_with_a_body_is_listed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "data/news.json"
            articles = root / "data/articles"
            self.write_snapshot(articles, "abcdef012345", contentKind="community")

            result = news_store.save_news({"items": [self.community_item()]}, manifest, articles)

            self.assertEqual(result["items"], 1)
            self.assertEqual(result["unlistedCommunity"], 0)

    def test_an_editorial_item_without_a_body_is_still_listed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "data/news.json"
            articles = root / "data/articles"
            self.write_snapshot(articles, "abcdef012345", contentKind="summary",
                                note="page:URLError:offline")
            item = self.community_item(source="Example", sourceDomain="example.com",
                                       url="https://example.com/post")

            result = news_store.save_news({"items": [item]}, manifest, articles)

            self.assertEqual(result["items"], 1)
            self.assertEqual(result["unlistedCommunity"], 0)


if __name__ == "__main__":
    unittest.main()
