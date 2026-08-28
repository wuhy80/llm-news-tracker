import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fetch_news


class FetchNewsTests(unittest.TestCase):
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
