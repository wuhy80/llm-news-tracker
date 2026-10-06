import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_review
import recap_routine_releases as recap

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def review(score, level):
    """A review whose dimensions sum to its score, like normalize_review produces."""
    remaining = score
    dimensions = {}
    for name, maximum in ai_review.DIMENSION_LIMITS.items():
        take = min(maximum, remaining)
        dimensions[name] = take
        remaining -= take
    return {"version": "ai-editor-v3", "dimensions": dimensions,
            "importanceScore": score, "importanceLevel": level}


class RecapTests(unittest.TestCase):
    def write_snapshot(self, root, name, title, score, level):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"title": title, "aiReview": review(score, level)},
                                   ensure_ascii=False), encoding="utf-8")
        return path

    def make_tree(self, root):
        self.write_snapshot(root, "2026/10/06/a.json", "v0.40.0-rc4: MLX: version bump (#18720)", 100, 5)
        self.write_snapshot(root, "2026/10/06/b.json", "Introducing Qwen 3.8 with a new coder variant", 100, 5)
        self.write_snapshot(root, "2026/10/06/c.json", "v0.6.0", 40, 2)
        return root

    def test_only_routine_titles_above_the_ceiling_are_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = recap.build_plan(self.make_tree(Path(directory)), NOW)

        self.assertEqual((plan["scanned"], plan["matched"], plan["changed"]), (3, 2, 1))
        self.assertEqual(plan["levels"], {"5->2": 1})

    def test_the_recapped_review_stays_internally_consistent(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = recap.build_plan(self.make_tree(Path(directory)), NOW)
            updated = plan["changes"][0]["review"]

        self.assertEqual(sum(updated["dimensions"].values()), updated["importanceScore"])
        self.assertLessEqual(updated["importanceScore"], recap.RECAP_CEILING)
        self.assertEqual(updated["importanceLevel"], ai_review.level_for_score(updated["importanceScore"]))
        self.assertEqual(updated["importanceLevel"], 2)
        self.assertIn("recappedAt", updated)

    def test_apply_rewrites_only_the_routine_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_tree(Path(directory))
            recap.apply_plan(recap.build_plan(root, NOW))

            routine = json.loads((root / "2026/10/06/a.json").read_text(encoding="utf-8"))
            ordinary = json.loads((root / "2026/10/06/b.json").read_text(encoding="utf-8"))
            low = json.loads((root / "2026/10/06/c.json").read_text(encoding="utf-8"))

        self.assertEqual(routine["aiReview"]["importanceLevel"], 2)
        self.assertEqual(ordinary["aiReview"]["importanceLevel"], 5)
        self.assertNotIn("recappedAt", ordinary["aiReview"])
        self.assertEqual(low["aiReview"]["importanceLevel"], 2)
        self.assertNotIn("recappedAt", low["aiReview"])

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_tree(Path(directory))
            before = {path: path.read_bytes() for path in root.rglob("*.json")}

            with mock.patch.object(recap, "ARTICLES_DIR", root):
                self.assertEqual(recap.main([]), 0)

            after = {path: path.read_bytes() for path in root.rglob("*.json")}
        self.assertEqual(before, after)

    def test_apply_through_main_writes_the_recap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_tree(Path(directory))

            with mock.patch.object(recap, "ARTICLES_DIR", root):
                self.assertEqual(recap.main(["--apply"]), 0)

            routine = json.loads((root / "2026/10/06/a.json").read_text(encoding="utf-8"))
        self.assertEqual(routine["aiReview"]["importanceLevel"], 2)


if __name__ == "__main__":
    unittest.main()
