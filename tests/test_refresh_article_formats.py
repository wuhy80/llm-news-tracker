import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import refresh_article_formats


class FormatRefreshTests(unittest.TestCase):
    def test_needs_refresh_respects_version_and_retry_window(self):
        item = {"id": "0123456789ab", "publishedAt": "2026-08-20T00:00:00Z"}
        now = datetime(2026, 8, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.json"
            with mock.patch.object(refresh_article_formats, "snapshot_path", return_value=path):
                path.write_text(json.dumps({
                    "contentKind": "page",
                    "bodyFormatVersion": refresh_article_formats.BODY_FORMAT_VERSION,
                }), encoding="utf-8")
                self.assertFalse(refresh_article_formats.needs_format_refresh(item, now))

                path.write_text(json.dumps({
                    "contentKind": "page",
                    "body": "Legacy body",
                    "formatRefreshAttemptedAt": "2026-08-29T00:00:00Z",
                }), encoding="utf-8")
                self.assertFalse(refresh_article_formats.needs_format_refresh(item, now))

                path.write_text(json.dumps({
                    "contentKind": "page",
                    "body": "Legacy body",
                    "formatRefreshAttemptedAt": "2026-08-20T00:00:00Z",
                }), encoding="utf-8")
                self.assertTrue(refresh_article_formats.needs_format_refresh(item, now))

    def test_code_like_candidates_are_prioritized(self):
        items = [
            {"id": "aaaaaaaaaaaa", "publishedAt": "2026-08-30T00:00:00Z"},
            {"id": "bbbbbbbbbbbb", "publishedAt": "2026-08-20T00:00:00Z"},
        ]
        records = {
            "aaaaaaaaaaaa": {"body": "A normal prose paragraph without technical syntax."},
            "bbbbbbbbbbbb": {"body": "Use this function example with const value = await loadData();"},
        }

        def loaded(item):
            return Path(f"{item['id']}.json"), records[item["id"]]

        with mock.patch.object(refresh_article_formats, "needs_format_refresh", return_value=True), \
             mock.patch.object(refresh_article_formats, "read_snapshot", side_effect=loaded):
            selected = refresh_article_formats.select_candidates(
                items, datetime.now(timezone.utc), limit=2, retry_days=7
            )

        self.assertEqual([item["id"] for item, _ in selected], ["bbbbbbbbbbbb", "aaaaaaaaaaaa"])

    def test_refresh_requires_structured_code_and_sufficient_content(self):
        previous = {"body": ("const value = await loadData(); " * 20).strip()}
        plain = ("const value = await loadData(); " * 20).strip()
        structured = "```javascript\n" + ("const value = await loadData();\n" * 12) + "```"

        self.assertFalse(refresh_article_formats.acceptable_refresh(previous, plain))
        self.assertTrue(refresh_article_formats.acceptable_refresh(previous, structured))

    def test_markup_damage_can_refresh_to_plain_page_text_without_code(self):
        previous = {"body": r"A paper with \textbf{important} notation and $x^2$."}
        refreshed = (
            "A paper with important notation and x2, followed by readable page text. "
            * 8
        )

        self.assertTrue(refresh_article_formats.acceptable_refresh(previous, refreshed))

    def test_refresh_rejects_code_fences_that_are_still_collapsed(self):
        yaml = " ".join([
            "apiVersion: apps/v1", "kind: Deployment", "metadata: name: model",
            "spec: replicas: 1", "containers: name: server", "image: example/model",
        ] * 8)
        previous = {"body": f"```\n{yaml}\n```"}
        structured = "```yaml\n" + yaml.replace(" kind:", "\nkind:").replace(" metadata:", "\nmetadata:") + "\n```"

        self.assertTrue(refresh_article_formats.likely_flattened_code(previous))
        self.assertFalse(refresh_article_formats.acceptable_refresh(previous, previous["body"]))
        self.assertTrue(refresh_article_formats.acceptable_refresh(previous, structured))

    def test_flattened_code_is_retried_even_after_a_recent_failure(self):
        item = {"id": "0123456789ab", "publishedAt": "2026-08-30T00:00:00Z"}
        yaml = " ".join([
            "apiVersion: apps/v1", "kind: Deployment", "metadata: name: model",
            "spec: replicas: 1", "containers: name: server", "image: example/model",
        ] * 8)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.json"
            path.write_text(json.dumps({
                "contentKind": "feed",
                "body": f"```\n{yaml}\n```",
                "formatRefreshAttemptedAt": "2026-08-30T00:00:00Z",
            }), encoding="utf-8")
            with mock.patch.object(refresh_article_formats, "snapshot_path", return_value=path):
                self.assertTrue(refresh_article_formats.needs_format_refresh(
                    item, datetime(2026, 8, 30, 1, tzinfo=timezone.utc), retry_days=7
                ))

    def test_malformed_fence_is_repaired_from_previous_body_when_fetch_fails(self):
        item = {
            "id": "0123456789ab",
            "url": "https://example.com/article",
            "publishedAt": "2026-08-20T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.json"
            path.write_text(json.dumps({
                "contentKind": "feed",
                "body": "Intro\n\nRun: ```lmcache server --port 10001```",
            }), encoding="utf-8")
            with mock.patch.object(refresh_article_formats, "snapshot_path", return_value=path), \
                 mock.patch.object(refresh_article_formats, "fetch_page_text", side_effect=ValueError("blocked")), \
                 mock.patch("article_store.ARTICLES_DIR", Path(directory)):
                result = refresh_article_formats.refresh_item(item, allow_reader=False)
            record = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(result, "feed")
        self.assertEqual(record["body"].count("```"), 2)

    def test_latex_markup_is_repaired_locally_when_fetch_fails(self):
        item = {
            "id": "0123456789ab",
            "url": "https://example.com/article",
            "publishedAt": "2026-08-20T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.json"
            path.write_text(json.dumps({
                "contentKind": "feed",
                "body": r"We introduce \textbf{S\textsuperscript{3}Gym} for $x^2$ evaluation.",
            }), encoding="utf-8")
            with mock.patch.object(refresh_article_formats, "snapshot_path", return_value=path), \
                 mock.patch("article_store.snapshot_path", return_value=path), \
                 mock.patch.object(refresh_article_formats, "fetch_page_text", side_effect=ValueError("blocked")), \
                 mock.patch("article_store.ARTICLES_DIR", Path(directory)):
                result = refresh_article_formats.refresh_item(item, allow_reader=False)
            record = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(result, "feed")
        self.assertIn("S3Gym", record["body"])
        self.assertNotIn("\\textbf", record["body"])

    def test_failed_refresh_preserves_existing_body(self):
        item = {
            "id": "0123456789ab",
            "url": "https://example.com/article",
            "publishedAt": "2026-08-20T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.json"
            path.write_text(json.dumps({
                "contentKind": "page",
                "body": "Existing readable body that must not be replaced.",
            }), encoding="utf-8")
            with mock.patch.object(refresh_article_formats, "snapshot_path", return_value=path), \
                 mock.patch.object(refresh_article_formats, "fetch_page_text", side_effect=ValueError("blocked")):
                result = refresh_article_formats.refresh_item(item, allow_reader=False)
            record = json.loads(path.read_text(encoding="utf-8"))

        self.assertIsNone(result)
        self.assertEqual(record["body"], "Existing readable body that must not be replaced.")
        self.assertIn("formatRefreshAttemptedAt", record)


if __name__ == "__main__":
    unittest.main()
