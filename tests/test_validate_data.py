import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import news_store
import validate_data


class ValidateDataTests(unittest.TestCase):
    def test_validates_generated_store(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            item = {
                "id": "abcdef012345", "title": "Example", "summary": "Summary",
                "url": "https://example.com", "source": "Example", "sourceDomain": "example.com",
                "publishedAt": "2026-08-26T00:00:00Z", "category": "release", "tags": [],
                "score": 70, "signal": "medium",
                "aiReview": {"version": "ai-editor-v3", "glossary": []},
            }
            news_store.save_news({"items": [item]}, data_dir / "news.json", data_dir / "articles")

            result = validate_data.validate(data_dir)

            self.assertEqual(result, {"items": 1, "days": 1, "locators": 1})

    def test_rejects_legacy_full_news_file(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            data_dir.mkdir(parents=True)
            (data_dir / "news.json").write_text('{"items": []}\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "still contains an items array"):
                validate_data.validate(data_dir)


if __name__ == "__main__":
    unittest.main()
