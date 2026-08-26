import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ai_review


class AIModelSelectionTests(unittest.TestCase):
    def test_candidates_prefer_latest_gemini_and_honor_override(self):
        candidates = ai_review.model_candidates("gemini")

        self.assertEqual(candidates[0], "gemini-3.7-flash")
        self.assertEqual(candidates[-1], "gemini-2.5-flash-lite")
        self.assertEqual(ai_review.model_candidates("gemini", "custom-model"), ["custom-model"])

    def test_gemini_falls_back_to_older_model(self):
        with patch.object(ai_review, "request_reviews", side_effect=[ValueError("unavailable"), []]) as request:
            reviews, model = ai_review.request_reviews_with_fallback(
                "gemini", "endpoint", "token", ["gemini-3.7-flash", "gemini-3.6-flash"], []
            )

        self.assertEqual(reviews, [])
        self.assertEqual(model, "gemini-3.6-flash")
        self.assertEqual(request.call_count, 2)

    def test_openrouter_does_not_switch_models(self):
        with patch.object(ai_review, "request_reviews", side_effect=ValueError("unavailable")) as request:
            with self.assertRaises(ValueError):
                ai_review.request_reviews_with_fallback(
                    "openrouter", "endpoint", "token", ["openrouter/free", "another-model"], []
                )

        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
