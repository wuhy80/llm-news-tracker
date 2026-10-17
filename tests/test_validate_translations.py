import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import validate_translations
from translate_articles import body_hash


class ValidateTranslationsTests(unittest.TestCase):
    def test_validates_complete_translation_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            articles = root / "articles"
            translations = root / "translations"
            source_path = articles / "2026" / "09" / "04" / "0123456789ab.json"
            translation_path = translations / "2026" / "09" / "04" / "0123456789ab.json"
            source_path.parent.mkdir(parents=True)
            translation_path.parent.mkdir(parents=True)
            source_path.write_text(json.dumps({"body": "An English source article."}), encoding="utf-8")
            translation_path.write_text(json.dumps({
                "articleId": "0123456789ab",
                "translationVersion": "openrouter-zh-v2",
                "sourceBodyHash": body_hash("An English source article."),
                "status": "complete",
                "totalBlocks": 1,
                "translatedBlocks": 1,
                "blocks": [{"id": "b0001", "translationZh": "一篇英文原文。"}],
                "wordWise": [{"term": "source", "briefZh": "来源", "explanationZh": "原始内容来源。"}],
            }), encoding="utf-8")

            result = validate_translations.validate(translations, articles)

        self.assertEqual(result, {"partial": 0, "complete": 1, "total": 1})


if __name__ == "__main__":
    unittest.main()
