import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import summarize_articles


class SummaryTests(unittest.TestCase):
    def test_load_candidates_discovers_nested_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            news_file = root / "news.json"
            articles = root / "articles"
            snapshot_path = articles / "2026/08/27/0123456789ab.json"
            snapshot_path.parent.mkdir(parents=True)
            news_file.write_text(json.dumps({
                "items": [{"id": "0123456789ab", "publishedAt": "2026-08-27T00:00:00Z"}],
            }), encoding="utf-8")
            snapshot_path.write_text(json.dumps({
                "id": "0123456789ab",
                "contentKind": "page",
                "fetchedAt": "2026-08-27T01:00:00Z",
                "body": "Readable article body",
            }), encoding="utf-8")

            with patch.object(summarize_articles, "NEWS_FILE", news_file), patch.object(
                summarize_articles, "ARTICLES_DIR", articles
            ):
                candidates = summarize_articles.load_candidates(10)

            self.assertEqual([entry[0] for entry in candidates], [snapshot_path])

    def test_extracts_three_informative_sentences(self):
        snapshot = {
            "title": "Model release",
            "body": (
                "Short.\n\nThe company released a new reasoning model with lower serving cost. "
                "It scored 82 points on the published benchmark. "
                "Developers can deploy it through the existing API. "
                "This fourth sentence should not be included."
            ),
        }

        result = summarize_articles.extract_summary_text(snapshot, {})

        self.assertIn("lower serving cost", result)
        self.assertIn("82 points", result)
        self.assertNotIn("fourth sentence", result)

    def test_chinese_text_does_not_call_translation_service(self):
        with patch("summarize_articles.urllib.request.urlopen") as urlopen:
            result = summarize_articles.translate_to_chinese("该模型发布了新的推理版本，并公布了基准测试结果。")

        self.assertIn("基准测试", result)
        urlopen.assert_not_called()

    def test_normalizes_to_three_chinese_sentences(self):
        result = summarize_articles.normalize_summary("第一句。第二句。第三句。第四句。")

        self.assertEqual(result, "第一句。第二句。第三句。")

    def test_metadata_summary_is_chinese_and_uses_article_context(self):
        result = summarize_articles.metadata_summary({
            "title": "New reasoning model",
            "source": "Example Lab",
            "category": "release",
            "tags": ["推理", "Benchmark"],
        })

        self.assertIn("模型发布", result)
        self.assertIn("Example Lab", result)
        self.assertIn("推理、Benchmark", result)


if __name__ == "__main__":
    unittest.main()
