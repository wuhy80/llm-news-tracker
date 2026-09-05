import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import translate_articles


class TranslateArticlesTests(unittest.TestCase):
    def test_article_blocks_match_reader_structure_and_skip_code(self):
        body = (
            "# Heading\n\nFirst paragraph line\ncontinues here.\n\n"
            "- First item\n\n> A quotation\n\n```python\nprint('not translated')\n```\n\nFinal paragraph."
        )

        blocks = translate_articles.article_blocks(body)

        self.assertEqual(
            [(block["id"], block["kind"], block["source"]) for block in blocks],
            [
                ("b0001", "heading", "Heading"),
                ("b0002", "paragraph", "First paragraph line continues here."),
                ("b0003", "list-item", "First item"),
                ("b0004", "blockquote", "A quotation"),
                ("b0005", "paragraph", "Final paragraph."),
            ],
        )
        self.assertNotIn("not translated", json.dumps(blocks))

    def test_response_requires_all_ids_and_normalizes_word_wise(self):
        chunk = [
            {"id": "b0001", "kind": "paragraph", "source": "Agent memory uses relevance decay.", "sourceHash": "a"},
            {"id": "b0002", "kind": "paragraph", "source": "The workflow runs nightly.", "sourceHash": "b"},
        ]
        payload = {
            "translations": [
                {"id": "b0001", "translationZh": "智能体记忆使用相关性衰减。"},
                {"id": "b0002", "translationZh": "该工作流每晚运行。"},
            ],
            "wordWise": [{
                "term": "relevance decay",
                "briefZh": "相关性衰减",
                "explanationZh": "记忆的重要性会随时间降低。",
            }],
        }

        translated, word_wise = translate_articles.normalize_response(payload, chunk)

        self.assertEqual([block["id"] for block in translated], ["b0001", "b0002"])
        self.assertEqual(word_wise[0]["term"], "relevance decay")

    def test_response_rejects_missing_block(self):
        chunk = [
            {"id": "b0001", "kind": "paragraph", "source": "First source block.", "sourceHash": "a"},
            {"id": "b0002", "kind": "paragraph", "source": "Second source block.", "sourceHash": "b"},
        ]
        with self.assertRaises(ValueError):
            translate_articles.normalize_response({
                "translations": [{"id": "b0001", "translationZh": "第一个段落。"}],
                "wordWise": [],
            }, chunk)

    def test_response_accepts_id_map_from_free_model(self):
        chunk = [
            {"id": "b0001", "kind": "paragraph", "source": "First source block.", "sourceHash": "a"},
            {"id": "b0002", "kind": "paragraph", "source": "Second source block.", "sourceHash": "b"},
        ]

        translated, _ = translate_articles.normalize_response({
            "data": {
                "b0001": "第一个来源段落。",
                "b0002": "第二个来源段落。",
            },
        }, chunk)

        self.assertEqual([block["id"] for block in translated], ["b0001", "b0002"])

    def test_translatable_blocks_include_short_english_prose(self):
        blocks = [
            {"id": "b0001", "source": "AI", "kind": "heading"},
            {"id": "b0002", "source": "A short note.", "kind": "paragraph"},
            {"id": "b0003", "source": "https://example.com", "kind": "paragraph"},
            {"id": "b0004", "source": "标签: AI", "kind": "paragraph"},
        ]

        self.assertEqual(
            [block["id"] for block in translate_articles.translatable_blocks(blocks)],
            ["b0001", "b0002"],
        )

    def test_candidates_prioritize_partial_level_five_translations(self):
        english = "Long-running agent memory needs careful relevance scoring. " * 12
        items = [
            {"id": "aaaaaaaaaaaa", "publishedAt": "2026-09-02T00:00:00Z", "aiReview": {"importanceLevel": 5}},
            {"id": "bbbbbbbbbbbb", "publishedAt": "2026-09-03T00:00:00Z", "aiReview": {"importanceLevel": 4}},
            {"id": "cccccccccccc", "publishedAt": "2026-09-04T00:00:00Z", "aiReview": {"importanceLevel": 3}},
        ]
        snapshots = {
            "aaaaaaaaaaaa": {"contentKind": "page", "body": english},
            "bbbbbbbbbbbb": {"contentKind": "page", "body": english},
            "cccccccccccc": {"contentKind": "page", "body": english},
        }

        def snapshot_file(item):
            return Path(item["id"] + ".json")

        def loaded(path):
            article_id = path.stem
            return snapshots.get(article_id)

        def record(item, snapshot, blocks):
            value = translate_articles.new_record(item, snapshot, blocks)
            if item["id"] == "aaaaaaaaaaaa":
                value["translatedBlocks"] = 1
                value["blocks"] = [{"id": "b0001", "translationZh": "已翻译", "sourceHash": "x"}]
            return Path(item["id"] + "-translation.json"), value

        with mock.patch.object(translate_articles, "snapshot_path", side_effect=snapshot_file), \
             mock.patch.object(translate_articles, "read_json", side_effect=loaded), \
             mock.patch.object(translate_articles, "load_record", side_effect=record):
            selected = translate_articles.select_candidates(items, datetime.now(timezone.utc))

        self.assertEqual([entry[0]["id"] for entry in selected], ["aaaaaaaaaaaa", "bbbbbbbbbbbb"])

    def test_apply_chunk_marks_complete_and_merges_word_wise(self):
        record = {
            "status": "partial",
            "totalBlocks": 1,
            "translatedBlocks": 0,
            "blocks": [],
            "wordWise": [],
        }

        translate_articles.apply_chunk(
            record,
            [{"id": "b0001", "kind": "paragraph", "sourceHash": "a", "translationZh": "完整译文。"}],
            [{"term": "agent", "briefZh": "智能体", "explanationZh": "执行任务的软件系统。"}],
            "free/model",
        )

        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["translatedBlocks"], 1)
        self.assertEqual(record["wordWise"][0]["briefZh"], "智能体")

    def test_daily_state_resets_on_a_new_utc_day(self):
        state = {"utcDate": "2026-09-04", "requestsToday": 45, "nextAttemptAt": "2026-09-05T00:05:00Z"}

        translate_articles.reset_daily_state(state, datetime(2026, 9, 5, 1, tzinfo=timezone.utc))

        self.assertEqual(state, {"schemaVersion": 1, "utcDate": "2026-09-05", "requestsToday": 0})

    def test_build_translation_index_contains_progress_and_location(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translations = root / "zh-CN" / "2026" / "09" / "05"
            translations.mkdir(parents=True)
            path = translations / "0123456789ab.json"
            path.write_text(json.dumps({
                "articleId": "0123456789ab",
                "status": "partial",
                "translatedBlocks": 2,
                "totalBlocks": 5,
                "importanceLevel": 5,
                "updatedAt": "2026-09-05T00:00:00Z",
                "model": "free/model",
            }), encoding="utf-8")
            with mock.patch.object(translate_articles, "TRANSLATIONS_DIR", root / "zh-CN"):
                index = translate_articles.build_translation_index()

        self.assertEqual(index["articles"]["0123456789ab"]["location"], "2026/09/05")
        self.assertEqual(index["articles"]["0123456789ab"]["translatedBlocks"], 2)

    def test_response_headers_are_normalized_for_rate_limit_tracking(self):
        headers = translate_articles.normalize_headers({"X-RateLimit-Remaining": 49, "X-RateLimit-Limit": 50})
        self.assertEqual(headers["x-ratelimit-remaining"], "49")
        self.assertEqual(headers["x-ratelimit-limit"], "50")


if __name__ == "__main__":
    unittest.main()
