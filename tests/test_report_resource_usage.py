import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import report_resource_usage as usage

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


class GrowthTests(unittest.TestCase):
    def sample(self, days_ago, key, value):
        moment = NOW - timedelta(days=days_ago)
        return {"measuredAt": moment.isoformat().replace("+00:00", "Z"), key: value}

    def test_growth_needs_two_samples_at_least_a_week_apart(self):
        self.assertIsNone(usage.growth_per_day([self.sample(0, "repoBytes", 100)], "repoBytes", NOW))
        self.assertIsNone(usage.growth_per_day(
            [self.sample(0, "repoBytes", 100), self.sample(2, "repoBytes", 50)], "repoBytes", NOW))

    def test_growth_is_bytes_per_day(self):
        history = [self.sample(14, "repoBytes", 1000), self.sample(0, "repoBytes", 2400)]

        self.assertAlmostEqual(usage.growth_per_day(history, "repoBytes", NOW), 100.0)

    def test_runway_covers_zero_growth_over_limit_days_and_months(self):
        self.assertEqual(usage.human_runway(0, 1024, None), "增长为 0，无法估算")
        self.assertEqual(usage.human_runway(2048, 1024, 10), "**已超出**")
        self.assertIn("天", usage.human_runway(1024 - 100, 1024, 10))
        self.assertIn("个月", usage.human_runway(1024 - 10_000_000, 1024, 1000))
        self.assertEqual(usage.human_runway(None, 1024, 10), "—")


class RenderTests(unittest.TestCase):
    def record(self):
        return {"measuredAt": "2026-10-06T00:00:00Z", "pagesArtifactBytes": 200 * 1024 ** 2,
                "repoBytes": 700 * 1024 ** 2, "mediaBytes": 218 * 1024 ** 2, "mediaFiles": 1926,
                "articleBytes": 87 * 1024 ** 2, "items": 8728}

    def test_block_carries_both_markers_and_the_limits(self):
        block = usage.render(self.record(), [], NOW)

        self.assertTrue(block.startswith(usage.START))
        self.assertTrue(block.endswith(usage.END))
        self.assertIn("GitHub Pages 站点", block)
        self.assertIn("仓库体积", block)
        self.assertIn("8,728 篇", block)
        self.assertIn("样本不足", block)

    def test_a_measured_trend_replaces_the_short_sample_note(self):
        history = [{"measuredAt": (NOW - timedelta(days=14)).isoformat().replace("+00:00", "Z"),
                    "repoBytes": 600 * 1024 ** 2},
                   {"measuredAt": NOW.isoformat().replace("+00:00", "Z"),
                    "repoBytes": 700 * 1024 ** 2}]

        block = usage.render(self.record(), history, NOW)

        self.assertNotIn("仓库 样本不足", block)
        self.assertIn("/天", block)

    def test_replace_block_swaps_between_markers_and_appends_when_absent(self):
        text = f"before\n{usage.START}\nold\n{usage.END}\nafter\n"

        replaced = usage.replace_block(text, f"{usage.START}\nnew\n{usage.END}")

        self.assertIn("new", replaced)
        self.assertNotIn("old", replaced)
        self.assertIn("before", replaced)
        self.assertIn("after", replaced)

        appended = usage.replace_block("no markers here", f"{usage.START}\nnew\n{usage.END}")

        self.assertIn("no markers here", appended)
        self.assertIn("new", appended)


class MeasureTests(unittest.TestCase):
    def test_measure_sums_the_trees_and_reads_the_item_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            (media / "2026").mkdir(parents=True)
            (media / "2026" / "a.jpg").write_bytes(b"x" * 100)
            articles = root / "articles"
            articles.mkdir()
            (articles / "b.json").write_text("{}", encoding="utf-8")
            data = root / "data"
            data.mkdir()
            (data / "news.json").write_text(json.dumps({"itemCount": 42}), encoding="utf-8")

            with mock.patch.object(usage, "TREES", {"mediaBytes": media, "articleBytes": articles}), \
                 mock.patch.object(usage, "DATA", data), \
                 mock.patch.object(usage, "github_json", lambda path: None):
                record = usage.measure(NOW)

        self.assertEqual(record["mediaBytes"], 100)
        self.assertEqual(record["mediaFiles"], 1)
        self.assertEqual(record["items"], 42)
        self.assertIsNone(record["repoBytes"])
        self.assertIsNone(record["pagesArtifactBytes"])

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text("hello", encoding="utf-8")
            history = Path(directory) / "usage.jsonl"
            with mock.patch.object(usage, "README", readme), \
                 mock.patch.object(usage, "HISTORY", history), \
                 mock.patch.object(usage, "TREES", {}), \
                 mock.patch.object(usage, "DATA", Path(directory)), \
                 mock.patch.object(usage, "github_json", lambda path: None):
                self.assertEqual(usage.main([]), 0)

            self.assertEqual(readme.read_text(encoding="utf-8"), "hello")
            self.assertFalse(history.exists())

    def test_write_updates_the_readme_and_appends_the_history(self):
        with tempfile.TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(f"head\n{usage.START}\nold\n{usage.END}\ntail\n", encoding="utf-8")
            history = Path(directory) / "usage.jsonl"
            with mock.patch.object(usage, "README", readme), \
                 mock.patch.object(usage, "HISTORY", history), \
                 mock.patch.object(usage, "TREES", {}), \
                 mock.patch.object(usage, "DATA", Path(directory)), \
                 mock.patch.object(usage, "github_json", lambda path: None):
                self.assertEqual(usage.main(["--write"]), 0)

            text = readme.read_text(encoding="utf-8")
            self.assertIn("head", text)
            self.assertIn("tail", text)
            self.assertNotIn("old", text)
            self.assertEqual(len(history.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
