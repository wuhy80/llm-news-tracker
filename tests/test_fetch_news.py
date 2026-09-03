import copy
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_news


class FetchNewsTests(unittest.TestCase):
    def test_anthropic_uses_direct_sitemap_source(self):
        source = next(source for source in fetch_news.SOURCES if source["name"] == "Anthropic News")

        self.assertEqual(source["url"], "https://www.anthropic.com/sitemap.xml")
        self.assertEqual(source["format"], "sitemap")
        self.assertTrue(source["official"])
        self.assertIn("/news/", source["sitemap_prefixes"])

    def test_anthropic_sitemap_discovers_direct_article_urls(self):
        source = next(source for source in fetch_news.SOURCES if source["name"] == "Anthropic News")
        payload = b"""<?xml version=\"1.0\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">
          <url><loc>https://www.anthropic.com/claude-fable-and-mythos-5-1</loc><lastmod>2026-09-01T12:00:00Z</lastmod></url>
          <url><loc>https://www.anthropic.com/news/claude-fable-5-mythos-5</loc><lastmod>2026-08-30T12:00:00Z</lastmod></url>
          <url><loc>https://www.anthropic.com/about</loc><lastmod>2026-09-01T12:00:00Z</lastmod></url>
        </urlset>"""

        items = fetch_news.parse_sitemap(
            payload,
            source,
            now=fetch_news.parse_date("2026-09-02T00:00:00Z"),
        )

        self.assertEqual(
            [item["url"] for item in items],
            [
                "https://www.anthropic.com/claude-fable-and-mythos-5-1",
                "https://www.anthropic.com/news/claude-fable-5-mythos-5",
            ],
        )

    def test_anthropic_page_metadata_reads_title_summary_and_publication_date(self):
        payload = (
            b'<html><head><meta property="og:title" content="Introducing Claude Fable 5.1 and Claude Mythos 5.1 \\ Anthropic">'
            b'<meta name="description" content="Our most advanced models."></head>'
            b'<body><script>publishedOn\\":\\"2026-09-01T12:00:00.000Z\\"</script></body></html>'
        )

        metadata = fetch_news.parse_page_metadata(payload)

        self.assertEqual(metadata["title"], "Introducing Claude Fable 5.1 and Claude Mythos 5.1")
        self.assertEqual(metadata["summary"], "Our most advanced models.")
        self.assertEqual(metadata["published"], "2026-09-01T12:00:00.000Z")

    def test_page_metadata_prefers_open_graph_description_and_supports_json_ld_date(self):
        payload = (
            b'<html><head><meta name="description" content="Generic site description.">'
            b'<meta property="og:description" content="Article-specific summary."></head>'
            b'<body><script>{"datePublished":"2026-09-01"}</script></body></html>'
        )

        metadata = fetch_news.parse_page_metadata(payload)

        self.assertEqual(metadata["summary"], "Article-specific summary.")
        self.assertEqual(metadata["published"], "2026-09-01")

    def test_legacy_anthropic_links_are_migrated_and_deduplicated(self):
        old_url = "https://news.google.com/rss/articles/legacy?oc=5"
        items = [
            {"id": "aaaaaaaaaaaa", "source": "Anthropic", "sourceDomain": "anthropic.com", "url": old_url},
            {"id": "bbbbbbbbbbbb", "source": "Anthropic", "sourceDomain": "anthropic.com", "url": old_url, "summaryZh": "kept"},
            {"id": "cccccccccccc", "source": "OpenAI", "sourceDomain": "openai.com", "url": old_url},
        ]
        direct_url = "https://www.anthropic.com/news/example"
        with mock.patch.object(fetch_news, "resolve_google_news_urls", return_value={old_url: direct_url}):
            migrated = fetch_news.migrate_legacy_anthropic_items(items)

        self.assertEqual(len(migrated), 2)
        self.assertEqual(migrated[0]["url"], direct_url)
        self.assertEqual(migrated[0]["summaryZh"], "kept")
        self.assertEqual(migrated[1]["url"], old_url)

    def test_curated_official_sources_are_unique_and_configured(self):
        sources = {source["name"]: source for source in fetch_news.SOURCES}

        self.assertEqual(len(sources), len(fetch_news.SOURCES))
        expected = {
            "Claude Blog": ("claude.com", "agent"),
            "Claude Code Releases": ("github.com", "agent"),
            "Google DeepMind": ("deepmind.google", None),
            "AWS Machine Learning": ("aws.amazon.com", None),
            "GitHub AI & ML": ("github.blog", "agent"),
            "Cloudflare AI": ("blog.cloudflare.com", None),
        }
        for name, (domain, hint) in expected.items():
            with self.subTest(source=name):
                source = sources[name]
                self.assertTrue(source["official"])
                self.assertEqual(source["domain"], domain)
                self.assertEqual(source.get("hint"), hint)

        self.assertEqual(
            sources["Claude Code Releases"]["url"],
            "https://github.com/anthropics/claude-code/releases.atom",
        )
        self.assertEqual(sources["Claude Code Releases"]["title_prefix"], "Claude Code")
        self.assertEqual(sources["Claude Blog"]["url"], "https://claude.com/blog")
        self.assertEqual(sources["Claude Blog"]["format"], "html-cards")

    def test_official_publisher_sources_do_not_use_news_search(self):
        search_hosts = {"news.google.com", "www.google.com", "bing.com", "www.bing.com"}
        direct_sources = {
            "Mistral AI News": ("https://mistral.ai/sitemap.xml", "/news/"),
            "xAI News": ("https://x.ai/sitemap.xml", "/news/"),
            "Artificial Analysis": ("https://artificialanalysis.ai/sitemap.xml", "/articles/"),
        }
        sources = {source["name"]: source for source in fetch_news.SOURCES}
        for name, (url, prefix) in direct_sources.items():
            with self.subTest(source=name):
                source = sources[name]
                self.assertTrue(source["official"])
                self.assertEqual(source["url"], url)
                self.assertEqual(source["format"], "sitemap")
                self.assertNotIn("fallback_urls", source)
                self.assertIn(prefix, source["sitemap_prefixes"])

        linux_do = sources["LINUX DO · 444"]
        self.assertNotIn("fallback_urls", linux_do)
        for source in fetch_news.SOURCES:
            with self.subTest(source=source["name"]):
                if source.get("official"):
                    self.assertNotIn(urllib.parse.urlparse(source["url"]).hostname, search_hosts)

    def test_artificial_analysis_sitemap_discovers_direct_article_urls(self):
        source = next(source for source in fetch_news.SOURCES if source["name"] == "Artificial Analysis")
        payload = b'''<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://artificialanalysis.ai/articles/claude-fable-5-1</loc><lastmod>2026-09-01T00:00:00Z</lastmod></url>
          <url><loc>https://artificialanalysis.ai/models/claude-fable-5-1</loc><lastmod>2026-09-01T00:00:00Z</lastmod></url>
        </urlset>'''

        items = fetch_news.parse_sitemap(
            payload,
            source,
            now=fetch_news.parse_date("2026-09-02T00:00:00Z"),
        )

        self.assertEqual([item["url"] for item in items], [
            "https://artificialanalysis.ai/articles/claude-fable-5-1",
        ])

    def test_feed_title_prefix_makes_version_only_releases_identifiable(self):
        payload = b"""
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <title>v2.1.250</title>
                <link rel="alternate" href="https://github.com/anthropics/claude-code/releases/tag/v2.1.250" />
                <updated>2026-08-28T00:49:16Z</updated>
              </entry>
            </feed>
        """
        source = next(
            source for source in fetch_news.SOURCES if source["name"] == "Claude Code Releases"
        )

        items = fetch_news.parse_feed(payload, source)

        self.assertEqual(items[0]["title"], "Claude Code v2.1.250")


    def test_arxiv_atom_feed_is_parsed_as_large_model_research(self):
        payload = b"""
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>http://arxiv.org/abs/2608.12345</id>
                <title>Scaling large language model reasoning with tool use</title>
                <summary>We study foundation model reasoning and evaluation.</summary>
                <published>2026-08-29T08:00:00Z</published>
                <updated>2026-08-29T10:00:00Z</updated>
                <link rel="alternate" type="text/html" href="https://arxiv.org/abs/2608.12345" />
              </entry>
            </feed>
        """
        source = next(source for source in fetch_news.SOURCES if source["name"] == "arXiv · 大模型研究")

        items = fetch_news.parse_feed(payload, source)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Scaling large language model reasoning with tool use")
        self.assertEqual(items[0]["url"], "https://arxiv.org/abs/2608.12345")
        self.assertEqual(items[0]["source"], "arXiv · 大模型研究")
        self.assertEqual(items[0]["sourceDomain"], "arxiv.org")
        self.assertEqual(items[0]["published"].isoformat(), "2026-08-29T08:00:00+00:00")

    def test_blog_card_parser_extracts_dates_and_deduplicates_marquee_cards(self):
        payload = b"""
            <div role="listitem">
              <h2>Claude Code now supports artifacts</h2>
              <div class="u-text-style-caption">June 18, 2026</div>
              <a data-cta-copy="Claude Code now supports artifacts"
                 href="/blog/artifacts-in-claude-code">Read more</a>
            </div>
            <div role="listitem">
              <h2>Claude Code now supports artifacts</h2>
              <div class="u-text-style-caption">June 18, 2026</div>
              <a href="/blog/artifacts-in-claude-code">Read more</a>
            </div>
        """
        source = next(source for source in fetch_news.SOURCES if source["name"] == "Claude Blog")

        items = fetch_news.parse_blog_cards(payload, source)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Claude Code now supports artifacts")
        self.assertEqual(items[0]["url"], "https://claude.com/blog/artifacts-in-claude-code")
        self.assertEqual(items[0]["published"].isoformat(), "2026-06-18T00:00:00+00:00")

    def test_canonical_url_removes_tracking_and_normalizes_order(self):
        first = "HTTPS://Example.COM:443/posts/launch/?utm_source=rss&b=2&a=1#details"
        second = "https://example.com/posts/launch?a=1&b=2"

        self.assertEqual(fetch_news.canonical_url(first), fetch_news.canonical_url(second))
        self.assertEqual(fetch_news.canonical_url("https://example.com:invalid/news"), "")

    def test_url_index_matches_same_article_with_a_different_title(self):
        previous = {
            "id": "0123456789ab",
            "title": "Original title",
            "url": "https://example.com/news/item?utm_medium=feed",
        }

        _, by_url = fetch_news.previous_indexes([previous])

        self.assertIs(by_url[fetch_news.canonical_url("https://example.com/news/item")], previous)

    def test_content_comparison_ignores_item_order_but_detects_changes(self):
        first = [{"id": "a", "title": "One"}, {"id": "b", "title": "Two"}]
        reordered = list(reversed(first))
        changed = copy.deepcopy(first)
        changed[0]["title"] = "Updated"

        self.assertFalse(fetch_news.news_content_changed(first, reordered))
        self.assertTrue(fetch_news.news_content_changed(first, changed))

    def test_existing_article_keeps_archive_date_and_ai_review(self):
        item = {
            "id": "0123456789ab",
            "publishedAt": "2026-08-23T10:06:41Z",
            "title": "Updated title",
        }
        review = {"version": "ai-editor-v3"}
        previous = {
            "id": item["id"],
            "publishedAt": "2026-08-24T07:31:41Z",
            "score": 91,
            "signal": "high",
            "aiReview": review,
        }

        result = fetch_news.preserve_archive_metadata(item, previous)

        self.assertIs(result, item)
        self.assertEqual(result["publishedAt"], previous["publishedAt"])
        self.assertEqual(result["score"], previous["score"])
        self.assertEqual(result["signal"], previous["signal"])
        self.assertIs(result["aiReview"], review)

    def test_new_article_keeps_fetched_publication_date(self):
        item = {"id": "0123456789ab", "publishedAt": "2026-08-23T10:06:41Z"}

        result = fetch_news.preserve_archive_metadata(item, None)

        self.assertEqual(result["publishedAt"], "2026-08-23T10:06:41Z")


if __name__ == "__main__":
    unittest.main()
