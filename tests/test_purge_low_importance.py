import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from news_store import load_news, save_news
import purge_low_importance as purge
import fetch_news


def make_item(article_id: str, level, url: str) -> dict:
    return {
        "id": article_id,
        "title": "A title for " + article_id,
        "summary": "A short summary for " + article_id,
        "url": url,
        "source": "example.com",
        "sourceDomain": "example.com",
        "publishedAt": "2022/05/01T00:00:00Z".replace("/", "-"),
        "category": "research",
        "tags": ["ai"],
        "score": 40,
        "signal": "test",
        "aiReview": {"version": 1, "importanceLevel": level,
                     "importanceScore": level * 20, "isRelevant": True,
                     "category": "research", "reasonZh": "测试", "summaryZh": ""},
    }


class PurgeLowImportanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.manifest = self.root / "data" / "news.json"
        self.articles = self.root / "data" / "articles"
        self.news_dir = self.root / "data" / "news"
        self.index_dir = self.root / "data" / "article-index"
        self.media_dir = self.root / "data" / "article-media"
        self.translations = self.root / "data" / "translations" / "zh-CN"
        self.blocklist = self.root / "data" / "news-blocklist.json"

        shard_dir = self.news_dir / "2022" / "05"
        shard_dir.mkdir(parents=True)
        self.level1 = make_item("aaaa00000001", 1, "https://example.com/level-one")
        self.level4 = make_item("aaaa00000002", 4, "https://example.com/level-four")
        self.level2 = make_item("aaaa00000003", 2, "https://example.com/level-two")
        shard = {"date": "2022-05-01", "items": [self.level1, self.level4, self.level2]}
        shard_file = shard_dir / "01.json"
        shard_file.write_text(json.dumps(shard), encoding="utf-8")
        save_news({"items": [self.level1, self.level4, self.level2]}, self.manifest, self.articles)

        # Artifacts of the level-1 article that the purge must remove.
        self.media_item = self.media_dir / "2022" / "05" / "01" / self.level1["id"]
        self.media_item.mkdir(parents=True)
        (self.media_item / "img.jpg").write_bytes(b"jpg")
        self.translation = self.translations / "2022" / "05" / "01" / (self.level1["id"] + ".json")
        self.translation.parent.mkdir(parents=True)
        self.translation.write_text(json.dumps({"articleId": self.level1["id"]}), encoding="utf-8")
        self.blocklist.write_text(json.dumps({"blockedUrls": ["https://example.com/already-blocked"]}), encoding="utf-8")

    def plan(self):
        return purge.build_plan(self.news_dir)

    def apply(self, plan):
        purge.apply_plan(plan, manifest_file=self.manifest, articles_dir=self.articles,
                         media_dir=self.media_dir, translations_dir=self.translations,
                         blocklist_file=self.blocklist)

    def test_plan_drops_only_importance_one(self):
        plan = self.plan()
        self.assertEqual(plan["dropped"], 1)
        self.assertEqual([e["id"] for e in plan["purged"]], [self.level1["id"]])
        self.assertEqual(len(plan["changes"]), 1)

    def test_apply_removes_article_and_artifacts_keeps_others(self):
        self.apply(self.plan())
        kept = json.loads((self.news_dir / "2022" / "05" / "01.json").read_text(encoding="utf-8"))
        self.assertEqual({item["id"] for item in kept["items"]}, {self.level4["id"], self.level2["id"]})
        snapshot_dir = self.articles / "2022" / "05" / "01"
        self.assertFalse((snapshot_dir / (self.level1["id"] + ".json")).exists(),
                         "level-1 snapshot must be pruned by save_news")
        self.assertTrue((snapshot_dir / (self.level4["id"] + ".json")).exists())
        self.assertFalse(self.media_item.exists())
        self.assertFalse(self.translation.exists())
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["itemCount"], 2)

    def test_blocklist_gains_purged_url_and_keeps_existing(self):
        self.apply(self.plan())
        blocked = json.loads(self.blocklist.read_text(encoding="utf-8"))["blockedUrls"]
        self.assertIn("https://example.com/level-one", blocked)
        self.assertIn("https://example.com/already-blocked", blocked)

    def test_second_run_is_a_noop(self):
        self.apply(self.plan())
        plan = self.plan()
        self.assertEqual(plan["dropped"], 0)
        self.assertEqual(plan["changes"], [])

    def test_fetch_news_blocklist_loader(self):
        import fetch_news
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "news-blocklist.json").write_text(
                json.dumps({"blockedUrls": ["https://example.com/x", "https://example.com/y"]}),
                encoding="utf-8")
            original_root = fetch_news.ROOT
            try:
                fetch_news.ROOT = root
                self.assertEqual(fetch_news.load_blocked_urls(),
                                 {"https://example.com/x", "https://example.com/y"})
            finally:
                fetch_news.ROOT = original_root
        # A missing file yields an empty set instead of raising.
        with tempfile.TemporaryDirectory() as directory:
            original_root = fetch_news.ROOT
            try:
                fetch_news.ROOT = Path(directory)
                self.assertEqual(fetch_news.load_blocked_urls(), set())
            finally:
                fetch_news.ROOT = original_root


if __name__ == "__main__":
    unittest.main()
