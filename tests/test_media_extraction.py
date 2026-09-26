import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import article_store
import backfill_article_media
from article_media import extract_media_refs


class MediaRegressionTests(unittest.TestCase):
    def test_scopes_media_and_ignores_page_metadata_and_related_cards(self):
        images, videos = extract_media_refs('''<html><head><meta property="og:image" content="/social.jpg"></head>
        <body class="no-sidebar"><nav><img src="/nav.jpg"></nav><main><article>
        <div class="entry-content"><img src="/real.jpg"><aside><img src="/aside.jpg"></aside></div>
        <div class="related-posts"><img src="/related.jpg"></div></article></main>
        <script type="application/ld+json">{"contentUrl":"https://example.com/not-an-image.mp4"}</script></body></html>''', 'https://example.com/post')
        self.assertEqual(images, [{'url': 'https://example.com/real.jpg', 'alt': ''}])
        self.assertEqual(videos, [])

    def test_github_template_video_excludes_facade_thumbnail(self):
        images, videos = extract_media_refs('''<html><body class="no-sidebar"><nav><img src="/nav.jpg"></nav>
        <main><header><div class="tease-thumbnail"><img src="/cover.jpg">
        <template><iframe src="https://www.youtube.com/embed/Xfgq79gV_GM?autoplay=1"></iframe></template>
        </div></header><section class="post__content"><p>Article</p></section>
        <article class="author-bio"><img src="/avatar.jpg"></article></main></body></html>''', 'https://github.blog/post')
        self.assertEqual(images, [])
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]['src'], 'https://www.youtube-nocookie.com/embed/Xfgq79gV_GM')

    def test_native_video_sources_are_not_images(self):
        images, videos = extract_media_refs('''<article><video poster="/poster.jpg"><source src="/demo.webm" type="video/webm"></video>
        <img src="/poster.jpg"><img src="/chart.jpg"></article>''', 'https://example.com/post')
        self.assertEqual([i['url'] for i in images], ['https://example.com/chart.jpg'])
        self.assertEqual(videos[0]['src'], 'https://example.com/demo.webm')
        self.assertEqual(videos[0]['poster'], 'https://example.com/poster.jpg')

    def test_rejects_untrusted_embeds_and_non_http_media(self):
        images, videos = extract_media_refs('''<article><iframe src="https://evil.example/embed/foo"></iframe>
        <iframe src="https://www.youtube.com.evil.example/embed/Xfgq79gV_GM"></iframe>
        <video src="javascript:alert(1)"></video><img src="data:foo"></article>''', 'https://example.com/post')
        self.assertEqual((images, videos), ([], []))

    def test_recheck_clears_old_images_preserves_body_review_and_translation_metadata(self):
        item = {'id': '0123456789ab', 'url': 'https://example.com/post', 'publishedAt': '2026-09-25T00:00:00Z'}
        with tempfile.TemporaryDirectory() as temp, patch.object(article_store, 'ARTICLES_DIR', Path(temp)):
            path = article_store.write_snapshot({**item, 'aiReview': {'version': 'keep'}}, 'Body', 'feed', images=[{'src': 'https://example.com/wrong.jpg'}])
            old = json.loads(path.read_text()); old['summaryZh'] = '保留摘要'; old['mediaRecheckedAt'] = 'old'; path.write_text(json.dumps(old))
            with patch.object(backfill_article_media, 'fetch_page_text', return_value=('New body not used', item['url'])):
                article_store.FETCHED_IMAGE_REFS[item['url']] = []
                article_store.FETCHED_VIDEO_REFS[item['url']] = [{'kind': 'video', 'src': 'https://example.com/demo.mp4'}]
                backfill_article_media.media_backfill_item(item, recheck=True)
            new = json.loads(path.read_text())
            self.assertEqual(new['images'], [])
            self.assertEqual(len(new['videos']), 1)
            self.assertEqual(new['body'], 'Body')
            self.assertEqual(new['summaryZh'], '保留摘要')
            self.assertEqual(new['aiReview'], {'version': 'keep'})
            self.assertEqual(new['mediaFormatVersion'], 2)
            with patch.object(backfill_article_media, 'fetch_page_text', side_effect=AssertionError('Should skip')):
                self.assertEqual(backfill_article_media.media_backfill_item(item, recheck=True), 'skip')

    def test_failed_recheck_does_not_mark_or_clear_existing_media(self):
        item = {'id': '0123456789ab', 'url': 'https://example.com/post', 'publishedAt': '2026-09-25T00:00:00Z'}
        with tempfile.TemporaryDirectory() as temp, patch.object(article_store, 'ARTICLES_DIR', Path(temp)):
            path = article_store.write_snapshot(item, 'Body', 'feed', images=[{'src': 'https://example.com/old.jpg'}])
            before = path.read_text()
            with patch.object(backfill_article_media, 'fetch_page_text', side_effect=ValueError('offline')):
                self.assertEqual(backfill_article_media.media_backfill_item(item, recheck=True), 'error:ValueError')
            self.assertEqual(path.read_text(), before)

    def test_omitted_media_preserved_but_empty_media_clears(self):
        item = {'id': '0123456789ab', 'url': 'https://example.com/post', 'publishedAt': '2026-09-25T00:00:00Z'}
        with tempfile.TemporaryDirectory() as temp, patch.object(article_store, 'ARTICLES_DIR', Path(temp)):
            path = article_store.write_snapshot(item, 'Body', 'feed', images=[{'src': 'https://example.com/old.jpg'}], videos=[{'src': 'https://example.com/v.mp4'}])
            article_store.write_snapshot(item, 'Body', 'feed')
            self.assertEqual(len(json.loads(path.read_text())['images']), 1)
            article_store.write_snapshot(item, 'Body', 'feed', images=[], videos=[])
            self.assertEqual(json.loads(path.read_text())['images'], [])
            self.assertEqual(json.loads(path.read_text())['videos'], [])
