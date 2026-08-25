import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import article_store


class ArticleTextTests(unittest.TestCase):
    def test_feed_html_becomes_paragraphs(self):
        body = article_store.text_from_html(
            "<p>This is the first sufficiently detailed paragraph for the reader.</p>"
            "<p>This is the second sufficiently detailed paragraph for the reader.</p>"
        )

        self.assertIn("first sufficiently detailed paragraph", body)
        self.assertIn("\n\n", body)

    def test_page_extraction_prefers_article_and_removes_noise(self):
        article_text = " ".join(["A useful model release detail"] * 30)
        page = (
            "<nav>Navigation text that should never appear in the snapshot.</nav>"
            f"<article><h1>Release notes</h1><p>{article_text}</p>"
            "<script>window.secret = true;</script></article>"
            "<footer>Footer text that should never appear in the snapshot.</footer>"
        )

        body = article_store.text_from_html(page, prefer_main=True)

        self.assertIn("useful model release detail", body)
        self.assertNotIn("Navigation text", body)
        self.assertNotIn("window.secret", body)
        self.assertNotIn("Footer text", body)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_schema(self):
        item = {
            "id": "0123456789ab",
            "title": "Example article",
            "source": "Example",
            "url": "https://example.com/article",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(article_store, "ARTICLES_DIR", Path(directory)):
                path = article_store.write_snapshot(item, "Body text", "page")
                snapshot = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(snapshot["id"], item["id"])
        self.assertEqual(snapshot["contentKind"], "page")
        self.assertEqual(snapshot["originalUrl"], item["url"])
        self.assertEqual(snapshot["body"], "Body text")
        self.assertIn("fetchedAt", snapshot)


class PublicUrlTests(unittest.TestCase):
    @patch("article_store.socket.getaddrinfo")
    def test_http_uses_port_80(self, getaddrinfo):
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
        ]

        article_store.validate_public_url("http://example.com/article")

        self.assertEqual(getaddrinfo.call_args.args[1], 80)

    @patch("article_store.socket.getaddrinfo")
    def test_private_address_is_rejected(self, getaddrinfo):
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ]

        with self.assertRaisesRegex(ValueError, "non-public URL"):
            article_store.validate_public_url("https://internal.example/article")

    def test_non_http_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported URL"):
            article_store.validate_public_url("file:///etc/passwd")

    @patch("article_store.socket.getaddrinfo")
    def test_redirect_to_private_address_is_rejected(self, getaddrinfo):
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))
        ]

        with self.assertRaisesRegex(ValueError, "non-public URL"):
            article_store.PublicRedirectHandler().redirect_request(
                None, None, 302, "Found", {}, "http://metadata.internal/latest"
            )


if __name__ == "__main__":
    unittest.main()
