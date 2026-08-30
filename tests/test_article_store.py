import base64
import json
import socket
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import article_store
from news_store import load_news


class ArticleTextTests(unittest.TestCase):
    def test_feed_html_becomes_paragraphs(self):
        body = article_store.text_from_html(
            "<p>This is the first sufficiently detailed paragraph for the reader.</p>"
            "<p>This is the second sufficiently detailed paragraph for the reader.</p>"
        )

        self.assertIn("first sufficiently detailed paragraph", body)
        self.assertIn("\n\n", body)

    def test_feed_html_preserves_preformatted_code(self):
        body = article_store.text_from_html(
            "<p>A paragraph with enough context before the example.</p>"
            "<pre><code>if ready:\n    print('go')\n\nreturn result</code></pre>"
            "<p>A paragraph after the example with more context.</p>"
        )

        self.assertIn("```\nif ready:\n    print('go')\n\nreturn result\n```", body)

    def test_page_extraction_prefers_article_and_removes_noise(self):
        article_text = " ".join(["A useful model release detail"] * 30)
        page = (
            "<nav>Navigation text that should never appear in the snapshot.</nav>"
            f"<article><h1>Release notes</h1><p>{article_text}</p>"
            "<p>Sponsored by Example, an advertisement that should not enter the reader.</p>"
            "<script>window.secret = true;</script></article>"
            "<footer>Footer text that should never appear in the snapshot.</footer>"
        )

        body = article_store.text_from_html(page, prefer_main=True)

        self.assertIn("useful model release detail", body)
        self.assertNotIn("Navigation text", body)
        self.assertNotIn("Sponsored by", body)
        self.assertNotIn("window.secret", body)
        self.assertNotIn("Footer text", body)

    def test_reader_markdown_removes_metadata_and_links(self):
        body = article_store.text_from_reader(
            "Title: Example\n\nURL Source: https://example.com\n\nMarkdown Content:\n\n"
            "## Main heading\n\nA detailed paragraph with a [source link](https://example.com/source) "
            "and enough useful article content to remain in the internal reader."
        )

        self.assertNotIn("URL Source", body)
        self.assertNotIn("https://example.com/source", body)
        self.assertIn("source link", body)

    def test_reader_markdown_preserves_fenced_code(self):
        body = article_store.text_from_reader(
            "Introductory paragraph with enough detail to remain in the reader.\n\n"
            "```python\nfor item in items:\n    print(item)\n```\n\n"
            "A paragraph after the code block with enough detail to remain in the reader."
        )

        self.assertIn("```python\nfor item in items:\n    print(item)\n```", body)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_schema(self):
        item = {
            "id": "0123456789ab",
            "title": "Example article",
            "source": "Example",
            "url": "https://example.com/article",
            "publishedAt": "2026-08-27T03:10:20Z",
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
        self.assertEqual(path.relative_to(directory).as_posix(), "2026/08/27/0123456789ab.json")

    def test_snapshot_path_rejects_missing_or_invalid_publication_date(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(article_store, "ARTICLES_DIR", Path(directory)):
                with self.assertRaisesRegex(ValueError, "invalid publishedAt"):
                    article_store.snapshot_path({"id": "0123456789ab"})
                with self.assertRaisesRegex(ValueError, "invalid publishedAt"):
                    article_store.snapshot_path({"id": "0123456789ab", "publishedAt": "2026-02-30"})

    def test_snapshot_redacts_access_tokens(self):
        secrets = [
            "hf_" + "a" * 34,
            "github_pat_" + "b" * 40,
            "ghp_" + "c" * 36,
            "sk-" + "d" * 40,
            "AIza" + "e" * 35,
            "AKIA" + "F" * 16,
        ]
        item = {
            "id": "0123456789ab",
            "title": "Example article",
            "source": "Example",
            "url": "https://example.com/article",
            "publishedAt": "2026-08-27T03:10:20Z",
            "aiReview": {"reasonZh": secrets[0], "glossary": [{"term": secrets[1]}]},
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(article_store, "ARTICLES_DIR", Path(directory)):
                path = article_store.write_snapshot(item, " ".join(secrets), "page")
                snapshot = json.loads(path.read_text(encoding="utf-8"))

        for secret in secrets:
            self.assertNotIn(secret, snapshot["body"])
        self.assertEqual(snapshot["body"].count("[REDACTED]"), len(secrets))
        self.assertNotIn(secrets[0], snapshot["aiReview"]["reasonZh"])
        self.assertNotIn(secrets[1], snapshot["aiReview"]["glossary"][0]["term"])

    @patch("article_store.fetch_reader_text")
    @patch("article_store.fetch_page_text")
    def test_archive_uses_reader_after_page_failure(self, fetch_page, fetch_reader):
        fetch_page.side_effect = ValueError("blocked")
        fetch_reader.return_value = ("Reader body " * 40, "https://example.com/article")
        item = {
            "id": "0123456789ab",
            "title": "Example article",
            "source": "Example",
            "url": "https://example.com/article",
            "summary": "Summary",
            "publishedAt": "2026-08-27T03:10:20Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(article_store, "ARTICLES_DIR", Path(directory)):
                _, kind = article_store.archive_item(item)
                snapshot = json.loads(article_store.snapshot_path(item).read_text(encoding="utf-8"))

        self.assertEqual(kind, "reader")
        self.assertEqual(snapshot["contentKind"], "reader")

    def test_news_archive_markers_match_snapshot_files(self):
        news = load_news(article_store.ROOT / "data" / "news.json")
        marked = [item for item in news["items"] if item.get("articleKind")]

        self.assertGreater(len(marked), 0)
        for item in marked:
            self.assertEqual(article_store.snapshot_kind(item), item["articleKind"])
            self.assertNotEqual(item["articleKind"], "summary")


class GoogleNewsTests(unittest.TestCase):
    def test_legacy_google_news_url_decodes_without_network(self):
        target = "https://example.com/a-model-release"
        payload = b"\x08\x13\x22" + bytes([len(target)]) + target.encode() + b"\xd2\x01\x00"
        token = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        source = f"https://news.google.com/rss/articles/{token}?oc=5"

        resolved = article_store.resolve_google_news_urls([source])

        self.assertEqual(resolved[source], target)

    @patch("article_store.validate_public_url")
    def test_bing_redirect_url_decodes_without_network(self, validate):
        target = "https://example.com/direct-article"
        source = "https://www.bing.com/news/apiclick.aspx?url=" + urllib.parse.quote(target)

        resolved = article_store.resolve_google_news_urls([source])

        self.assertEqual(resolved[source], target)
        validate.assert_called_once_with(target)


class PublicUrlTests(unittest.TestCase):
    def test_community_urls_use_structured_endpoints(self):
        reddit = article_store.community_api_url(
            "https://www.reddit.com/r/LocalLLaMA/comments/abc123/example/"
        )
        linux_do = article_store.community_api_url("https://linux.do/t/topic-name/12345")

        self.assertEqual(
            reddit,
            "https://www.reddit.com/r/LocalLLaMA/comments/abc123/example.json?raw_json=1",
        )
        self.assertEqual(linux_do, "https://linux.do/t/topic-name/12345.json")

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
