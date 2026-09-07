import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import article_store


class ArticleMediaTests(unittest.TestCase):
    def test_filters_qbitai_branding(self):
        self.assertTrue(article_store.is_site_chrome_image(
            "https://www.qbitai.com/wp-content/uploads/imgs/qbitai-logo-1.png"
        ))
        self.assertTrue(article_store.is_site_chrome_image(
            "https://www.qbitai.com/wp-content/uploads/2019/01/qrcode_QbitAI_1.jpg"
        ))
        self.assertFalse(article_store.is_site_chrome_image(
            "https://i.qbitai.com/wp-content/uploads/2026/09/chart.webp"
        ))
        self.assertFalse(article_store.is_site_chrome_image(
            "https://not-qbitai.com/assets/logo.png"
        ))

    def test_marks_qbitai_cdn_for_local_storage(self):
        self.assertTrue(article_store.is_browser_incompatible_image(
            "https://i.qbitai.com/wp-content/uploads/2026/09/chart.webp"
        ))
        self.assertFalse(article_store.is_browser_incompatible_image(
            "https://www.qbitai.com/wp-content/uploads/2026/09/chart.webp"
        ))


if __name__ == "__main__":
    unittest.main()
