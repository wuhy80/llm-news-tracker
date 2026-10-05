import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cleanup_stale_news as cleanup


class CleanupStaleNewsTests(unittest.TestCase):
    def write_shard(self, news_dir, date, items):
        year, month, day = date.split("-")
        path = news_dir / year / month / f"{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"date": date, "items": items}, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def make_tree(self, root):
        news = root / "data" / "news"
        # A Bing search item keeps sourceDomain bing.com even though the embedded
        # publisher rewrote its name, which is what the cleanup keys on.
        bing = {"id": "aaa", "title": "Some Nature article", "source": "Nature",
                "sourceDomain": "bing.com", "publishedAt": "2026-08-18T00:00:00Z"}
        reddit = {"id": "bbb", "title": "A rather long reddit headline " * 8,
                  "source": "Reddit · LocalLLaMA", "sourceDomain": "reddit.com",
                  "publishedAt": "2026-08-19T00:00:00Z"}
        official = {"id": "ccc", "title": "OpenAI ships something", "source": "OpenAI",
                    "sourceDomain": "openai.com", "publishedAt": "2026-08-19T00:00:00Z"}
        self.write_shard(news, "2026-08-18", [bing])
        self.write_shard(news, "2026-08-19", [reddit, official])
        return news

    def test_drops_removed_sources_by_domain_and_trims_forum_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            news = self.make_tree(Path(directory))

            plan = cleanup.build_plan(news)

            self.assertEqual((plan["items"], plan["dropped"], plan["trimmed"]), (3, 1, 1))
            self.assertEqual(plan["droppedByDomain"], {"bing.com": 1})
            self.assertEqual(plan["changedFiles"], 2)

    def test_applying_removes_the_item_and_shortens_the_title(self):
        with tempfile.TemporaryDirectory() as directory:
            news = self.make_tree(Path(directory))

            cleanup.apply_plan(cleanup.build_plan(news))

            day_one = json.loads((news / "2026/08/18.json").read_text(encoding="utf-8"))
            day_two = json.loads((news / "2026/08/19.json").read_text(encoding="utf-8"))
            self.assertEqual(day_one["items"], [])
            self.assertEqual([item["id"] for item in day_two["items"]], ["bbb", "ccc"])
            self.assertTrue(day_two["items"][0]["title"].endswith("…"))
            self.assertLessEqual(len(day_two["items"][0]["title"]), 121)
            self.assertEqual(day_two["items"][1]["title"], "OpenAI ships something")

    def test_an_old_but_valid_article_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            news = self.make_tree(Path(directory))
            self.write_shard(news, "2016-11-14", [
                {"id": "ddd", "title": "An old OpenAI post", "source": "OpenAI",
                 "sourceDomain": "openai.com", "publishedAt": "2016-11-14T00:00:00Z"}])

            plan = cleanup.build_plan(news)

            self.assertEqual(plan["dropped"], 1)
            self.assertEqual(plan["trimmed"], 1)

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            news = self.make_tree(Path(directory))
            before = {path: path.read_bytes() for path in news.rglob("*.json")}

            with mock.patch.object(cleanup, "NEWS_DIR", news):
                self.assertEqual(cleanup.main([]), 0)

            after = {path: path.read_bytes() for path in news.rglob("*.json")}
            self.assertEqual(before, after)

    def test_apply_writes_through_main(self):
        with tempfile.TemporaryDirectory() as directory:
            news = self.make_tree(Path(directory))

            with mock.patch.object(cleanup, "NEWS_DIR", news):
                self.assertEqual(cleanup.main(["--apply"]), 0)

            day_one = json.loads((news / "2026/08/18.json").read_text(encoding="utf-8"))
            self.assertEqual(day_one["items"], [])


if __name__ == "__main__":
    unittest.main()
