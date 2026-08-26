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
            "category": "unknown",
            "dimensions": {
                "relevance": 99,
                "impact": 22,
                "novelty": 14,
                "credibility": 15,
                "usefulness": 10,
                "timeliness": 10,
            },
            "evidenceLevel": "clear",
            "informationType": "original",
            "tags": ["Agent", "Agent", "tools"],
            "reasonZh": "这是一篇重要的智能体技术文章。",
            "summaryZh": "文章介绍了新的工具调用方法。它降低了智能体执行失败率。",
            "duplicateKey": " Agent tools ",
        }, {"category": "agent", "score": 60, "tags": []}, "gemini", "example/model", "2026-01-01T00:00:00Z")

        self.assertEqual(result["category"], "agent")
        self.assertEqual(result["dimensions"]["relevance"], 25)
        self.assertEqual(result["importanceScore"], 96)
        self.assertEqual(result["importanceLevel"], 5)
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
                "category": "release",
                "dimensions": {
                    "relevance": 24,
                    "impact": 22,
                    "novelty": 13,
                    "credibility": 14,
                    "usefulness": 8,
                    "timeliness": 7,
                },
                "evidenceLevel": "clear",
                "informationType": "original",
                "tags": ["模型发布"],
                "reasonZh": "该文章发布了新的大模型。",
                "summaryZh": "该公司发布了新的大模型，并公布了部署方式。",
                "duplicateKey": "new-model",
            }]

            with patch.object(ai_review, "ARTICLES_DIR", articles):
                completed = ai_review.apply_reviews(items, raw, "gemini", "example/model", "2026-01-01T00:00:00Z")

            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(completed, 1)
            self.assertEqual(items[0]["aiReview"]["importanceScore"], 88)
            self.assertEqual(items[0]["aiReview"]["importanceLevel"], 5)
            self.assertEqual(snapshot["summaryZh"], raw[0]["summaryZh"])
            self.assertEqual(snapshot["summaryModel"], "gemini:example/model")

    def test_editorial_ceiling_limits_off_topic_and_unverified_items(self):
        base = {
            "category": "agent",
            "dimensions": {name: maximum for name, maximum in ai_review.DIMENSION_LIMITS.items()},
            "tags": [],
            "reasonZh": "该内容的证据和新增信息有限。",
            "summaryZh": "该内容讨论了一项大模型相关动态。",
            "duplicateKey": "event",
        }
        item = {"category": "agent", "score": 60, "tags": []}

        off_topic = ai_review.normalize_review(
            {**base, "isRelevant": False, "evidenceLevel": "clear", "informationType": "original"},
            item, "gemini", "model", "2026-01-01T00:00:00Z",
        )
        rumor = ai_review.normalize_review(
            {**base, "isRelevant": True, "evidenceLevel": "partial", "informationType": "rumor"},
            item, "gemini", "model", "2026-01-01T00:00:00Z",
        )

        self.assertEqual((off_topic["importanceScore"], off_topic["importanceLevel"]), (29, 1))
        self.assertEqual(sum(off_topic["dimensions"].values()), off_topic["importanceScore"])
        self.assertEqual((rumor["importanceScore"], rumor["importanceLevel"]), (69, 3))

    def test_community_post_needs_clear_evidence_for_level_four(self):
        raw = {
            "isRelevant": True,
            "category": "agent",
            "dimensions": {name: maximum for name, maximum in ai_review.DIMENSION_LIMITS.items()},
            "evidenceLevel": "partial",
            "informationType": "original",
            "tags": [],
            "reasonZh": "社区帖子提出了一个重要结论，但缺少可复现证据。",
            "summaryZh": "帖子介绍了新的智能体方法。",
            "duplicateKey": "community-event",
        }
        item = {"source": "Reddit LocalLLaMA", "category": "agent", "score": 60, "tags": []}

        result = ai_review.normalize_review(raw, item, "gemini", "model", "2026-01-01T00:00:00Z")

        self.assertEqual((result["importanceScore"], result["importanceLevel"]), (69, 3))

if __name__ == "__main__":
    unittest.main()
