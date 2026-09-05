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

    def test_plain_feed_preserves_indented_paragraph_breaks(self):
        body = article_store.text_from_html(
            "First paragraph with enough detail to remain in the article snapshot.\n"
            "  Second paragraph with enough detail to remain in the article snapshot."
        )

        self.assertIn("First paragraph", body)
        self.assertIn("\n\nSecond paragraph", body)

    def test_feed_html_preserves_preformatted_code(self):
        body = article_store.text_from_html(
            "<p>A paragraph with enough context before the example.</p>"
            "<pre><code>if ready:\n    print('go')\n\nreturn result</code></pre>"
            "<p>A paragraph after the example with more context.</p>"
        )

        self.assertIn("```\nif ready:\n    print('go')\n\nreturn result\n```", body)

    def test_feed_html_turns_preformatted_breaks_into_code_lines(self):
        body = article_store.text_from_html(
            "<article><pre>apiVersion: apps/v1<br>kind: Deployment<br>metadata:<br>  name: model</pre></article>",
            prefer_main=True,
        )

        self.assertIn("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: model", body)

    def test_feed_html_does_not_duplicate_self_closing_pre_breaks(self):
        body = article_store.text_from_html("<pre>line one<br/>line two</pre>")

        self.assertIn("line one\nline two", body)
        self.assertNotIn("line one\n\nline two", body)

    def test_detects_fenced_code_that_was_collapsed_to_one_line(self):
        collapsed = "```\n" + " ".join([
            "apiVersion: apps/v1", "kind: Deployment", "metadata: name: model",
            "spec: replicas: 1", "containers: name: server", "image: example/model",
        ] * 8) + "\n```"
        structured = collapsed.replace(" kind:", "\nkind:").replace(" metadata:", "\nmetadata:")

        self.assertTrue(article_store.has_flattened_code(collapsed))
        self.assertFalse(article_store.has_flattened_code(structured))

    def test_restores_line_boundaries_for_collapsed_yaml_shell_and_python(self):
        yaml = "apiVersion: apps/v1 kind: Deployment metadata: name: model spec: replicas: 1 containers: - name: server image: example/model"
        shell = "hf download org/model \\ --local-dir ./model cd model python convert.py"
        python = "import json p='model.json'; d=json.load(open(p)); print(d)"

        for source in (yaml, shell, python):
            restored = article_store.restore_collapsed_code(source)
            self.assertGreaterEqual(len(restored.splitlines()), 2)
            self.assertFalse(article_store.has_flattened_code(f"```\n{restored}\n```"))

        restored_yaml = article_store.restore_collapsed_code(yaml)
        self.assertIn("apiVersion: apps/v1\nkind: Deployment\nmetadata:", restored_yaml)
        self.assertIn("hf download org/model \\\n --local-dir", article_store.restore_collapsed_code(shell))
        self.assertIn("import json\np='model.json';", article_store.restore_collapsed_code(python))

    def test_body_limit_closes_a_code_block_instead_of_cutting_it_open(self):
        body = "Introductory context.\n\n```python\n" + ("print('line')\n" * 5000) + "```"

        limited = article_store.limit_body(body, 800)

        self.assertLessEqual(len(limited), 800)
        self.assertEqual(limited.count("```"), 2)

    def test_normalizes_inline_and_unterminated_fences(self):
        inline = "Run this: ```lmcache server --port 10001```"
        unterminated = "An example:\n```\nline one\nline two"

        normalized_inline = article_store.normalize_fenced_body(inline)
        normalized_unterminated = article_store.normalize_fenced_body(unterminated)

        self.assertEqual(normalized_inline.count("```"), 2)
        self.assertEqual(normalized_unterminated.count("```"), 2)
        self.assertIn("lmcache server --port 10001", normalized_inline)

    def test_detects_corrupted_binary_like_body(self):
        self.assertTrue(article_store.has_corrupted_text("text \ufffd\ufffd\ufffd"))
        self.assertFalse(article_store.has_corrupted_text("normal text with one \ufffd"))

    def test_feed_refresh_upgrades_legacy_collapsed_code(self):
        item = {
            "id": "0123456789ab",
            "title": "Example article",
            "source": "Example",
            "url": "https://example.com/article",
            "publishedAt": "2026-08-27T03:10:20Z",
        }
        code_lines = [
            "apiVersion: apps/v1", "kind: Deployment", "metadata:", "  name: model",
            "spec:", "  replicas: 1", "  containers:", "    - name: server",
            "      image: example/model", "      args:", "        - --serve",
        ]
        legacy_body = "```\n" + " ".join(code_lines * 4) + "\n```"
        code_html = "\n".join(code_lines * 4)
        feed_html = (
            "<p>A detailed introduction before the deployment configuration example.</p>"
            f"<pre>{code_html}</pre>"
            "<p>A detailed explanation after the deployment configuration example.</p>"
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(article_store, "ARTICLES_DIR", Path(directory)):
                path = article_store.snapshot_path(item)
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({
                    "contentKind": "feed",
                    "archiveVersion": 2,
                    "body": legacy_body,
                }), encoding="utf-8")
                changed = article_store.store_feed_snapshot(item, feed_html)
                snapshot = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(changed)
        self.assertEqual(snapshot["bodyFormatVersion"], article_store.BODY_FORMAT_VERSION)
        self.assertIn("apiVersion: apps/v1\nkind: Deployment\nmetadata:", snapshot["body"])

    def test_feed_refresh_keeps_new_paragraphs_even_when_body_is_shorter(self):
        item = {
            "id": "0123456789ab",
            "title": "Example article",
            "source": "Example",
            "url": "https://example.com/article",
            "publishedAt": "2026-08-27T03:10:20Z",
        }
        old_body = "A single paragraph with older source context. " * 10
        feed_text = (
            "A single paragraph restored from the feed with enough context to remain readable.\n  "
            "A second paragraph restored from the feed with enough context to remain readable. "
        ) * 2
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(article_store, "ARTICLES_DIR", Path(directory)):
                path = article_store.snapshot_path(item)
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({
                    "contentKind": "feed",
                    "bodyFormatVersion": article_store.BODY_FORMAT_VERSION,
                    "body": old_body,
                }), encoding="utf-8")
                changed = article_store.store_feed_snapshot(item, feed_text)
                snapshot = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(changed)
        self.assertIn("\n\nA second paragraph", snapshot["body"])

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

    def test_page_extraction_handles_qbitai_article_container_and_sidebars(self):
        article_text = " ".join(["A useful article detail"] * 35)
        page = (
            '<div class="content"><div class="article">'
            '<h1>Article title</h1>'
            '<div class="article_info">Author and publication metadata</div>'
            '<div class="zhaiyao"><p>Short abstract</p></div>'
            f'<p>{article_text}</p>'
            '<div class="person_box"><li>Author\'s other article</li></div>'
            '<div class="share_pc">Share this article</div>'
            '<div class="line_font">Copyright notice</div>'
            '</div><div class="content_right">'
            '<div class="xiangguan"><h3>相关阅读</h3><div class="item"><h4>Related article</h4></div></div>'
            '<div class="yaowen"><h3>热门文章</h3><div class="item"><h4>Popular article</h4></div></div>'
            '</div></div>'
        )

        body = article_store.text_from_html(page, prefer_main=True)

        self.assertIn("useful article detail", body)
        self.assertNotIn("publication metadata", body)
        self.assertNotIn("Author's other article", body)
        self.assertNotIn("Related article", body)
        self.assertNotIn("Popular article", body)
        self.assertNotIn("Copyright notice", body)

    def test_page_extraction_removes_malformed_image_placeholder(self):
        page = '<div class="article"><p>Useful article content that is long enough to keep.</p>< img id="logo" src="logo.png"></div>'

        body = article_store.text_from_html(page, prefer_main=True)

        self.assertNotIn("< img", body)
        self.assertIn("Useful article content", body)

    def test_extracts_unique_article_image_references(self):
        refs = article_store.extract_image_refs(
            '<img src="/chart.png" alt="Benchmark chart">'
            '<img data-src="https://example.com/chart.png">'
            '<img class="avatar" src="/avatar.png">',
            "https://example.com/posts/entry",
        )

        self.assertEqual(refs, [{"url": "https://example.com/chart.png", "alt": "Benchmark chart"}])

    def test_extracts_reader_markdown_image_references(self):
        refs = article_store.extract_markdown_image_refs(
            "![A chart](/images/chart.webp)\n![A chart](/images/chart.webp)",
            "https://example.com/article",
        )

        self.assertEqual(refs, [{"url": "https://example.com/images/chart.webp", "alt": "A chart"}])

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

    def test_normalizes_latex_paper_markup_without_touching_code(self):
        body = article_store.normalize_article_body(
            r"We introduce \textbf{S\textsuperscript{3}Gym} and compare S$^3$Gym with "
            r"\textit{prior work}. The ratio is \frac{1}{2}."
            "\n\n```python\nlabel = r'\\textbf{keep this}'\n```"
        )

        self.assertIn("We introduce S3Gym and compare S3Gym with prior work.", body)
        self.assertIn("The ratio is (1)/(2).", body)
        self.assertIn("label = r'\\textbf{keep this}'", body)
        self.assertNotIn("\\textbf{S", body)

    def test_detects_unrendered_latex_markup(self):
        self.assertTrue(article_store.has_unrendered_markup(r"A \textbf{paper} with $x^2$."))
        self.assertFalse(article_store.has_unrendered_markup("A normal readable paragraph."))
        self.assertFalse(article_store.has_unrendered_markup("```shell\necho $HOME \\textbf{literal}\n```"))


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
        self.assertEqual(snapshot["bodyFormatVersion"], article_store.BODY_FORMAT_VERSION)
        self.assertEqual(snapshot["originalUrl"], item["url"])
        self.assertEqual(snapshot["body"], "Body text")
        self.assertIn("fetchedAt", snapshot)
        self.assertEqual(path.relative_to(directory).as_posix(), "2026/08/27/0123456789ab.json")

    def test_refresh_preserves_generated_summary_metadata(self):
        item = {
            "id": "0123456789ab",
            "title": "Example article",
            "source": "Example",
            "url": "https://example.com/article",
            "publishedAt": "2026-08-27T03:10:20Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(article_store, "ARTICLES_DIR", Path(directory)):
                path = article_store.snapshot_path(item)
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({
                    "summaryZh": "已有中文摘要。",
                    "summaryGeneratedAt": "2026-08-28T00:00:00Z",
                    "summaryModel": "example/model",
                }), encoding="utf-8")
                article_store.write_snapshot(item, "Refreshed body", "page")
                snapshot = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(snapshot["summaryZh"], "已有中文摘要。")
        self.assertEqual(snapshot["summaryGeneratedAt"], "2026-08-28T00:00:00Z")
        self.assertEqual(snapshot["summaryModel"], "example/model")

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
