import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from summarize_articles import parse_model_response


class SummaryResponseTests(unittest.TestCase):
    def test_parses_fenced_json_and_keeps_chinese_summary(self):
        result = parse_model_response(
            '```json\n{"0123456789ab":"文章发布了一款新的推理模型，并公布了关键评测结果。其重点是降低部署成本。"}\n```'
        )

        self.assertIn("0123456789ab", result)

    def test_discards_non_chinese_or_too_short_values(self):
        result = parse_model_response('{"one":"English only summary", "two":"太短"}')

        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
