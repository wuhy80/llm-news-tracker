import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ai_review


class AIReviewTests(unittest.TestCase):
    def test_extracts_json_from_fenced_response(self):
        result = ai_review.extract_json_object('```json\n{"reviews": []}\n```')

        self.assertEqual(result, {"reviews": []})

    def test_select_candidates_skips_current_review_version(self):
        items = [
            {"id": "old", "publishedAt": "2026-01-01T00:00:00Z"},
            {
                "id": "done",
                "publishedAt": "2026-03-01T00:00:00Z",
                "aiReview": {"version": ai_review.REVIEW_VERSION},
            },
            {"id": "new", "publishedAt": "2026-02-01T00:00:00Z"},
        ]

        result = ai_review.select_candidates(items, 10)

        self.assertEqual([item["id"] for item in result], ["new", "old"])

    def test_normalize_review_validates_fields(self):
        result = ai_review.normalize_review({
            "isRelevant": True,
            "relevanceScore": 140,
            "category": "unknown",
            "tags": ["Agent", "Agent", "tools"],
            "reasonZh": "这是一篇重要的智能体技术文章。",
            "summaryZh": "文章介绍了新的工具调用方法。它降低了智能体执行失败率。",
            "duplicateKey": " Agent tools ",
        }, {"category": "agent", "score": 60, "tags": []}, "gemini", "example/model", "2026-01-01T00:00:00Z")

        self.assertEqual(result["category"], "agent")
        self.assertEqual(result["relevanceScore"], 100)
        self.assertEqual(result["tags"], ["Agent", "tools"])
        self.assertEqual(result["provider"], "gemini")
        self.assertEqual(result["model"], "example/model")

    def test_credentials_prefer_gemini_and_support_openrouter(self):
        with patch.dict(ai_review.os.environ, {"GEMINI_API_KEY": "gemini", "OPENROUTER_API_KEY": "router"}, clear=True):
            self.assertEqual(ai_review.model_credentials("auto"), ("gemini", "gemini"))
            self.assertEqual(ai_review.model_credentials("openrouter"), ("openrouter", "router"))

    def test_apply_reviews_updates_item_and_readable_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            articles = Path(directory)
            snapshot_path = articles / "abc123.json"
            snapshot_path.write_text(json.dumps({
                "id": "abc123",
                "contentKind": "page",
                "body": "Article body",
            }), encoding="utf-8")
            items = [{
                "id": "abc123",
                "category": "industry",
                "score": 55,
                "tags": [],
            }]
            raw = [{
                "id": "abc123",
                "isRelevant": True,
                "relevanceScore": 88,
                "category": "release",
                "tags": ["模型发布"],
                "reasonZh": "该文章发布了新的大模型。",
                "summaryZh": "该公司发布了新的大模型，并公布了部署方式。",
                "duplicateKey": "new-model",
            }]

            with patch.object(ai_review, "ARTICLES_DIR", articles):
                completed = ai_review.apply_reviews(items, raw, "gemini", "example/model", "2026-01-01T00:00:00Z")

            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(completed, 1)
            self.assertEqual(items[0]["aiReview"]["relevanceScore"], 88)
            self.assertEqual(snapshot["summaryZh"], raw[0]["summaryZh"])
            self.assertEqual(snapshot["summaryModel"], "gemini:example/model")


if __name__ == "__main__":
    unittest.main()
