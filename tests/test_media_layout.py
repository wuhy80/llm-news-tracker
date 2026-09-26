import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import article_store
import backfill_article_media as backfill
from media_layout import build_layout, layout_blocks, body_digest, layout_current
from translate_articles import article_blocks

A = 'The first paragraph has enough text to survive the article content cleaner.'
B = 'The second paragraph describes performance and implementation in detail.'
C = 'The final paragraph finishes this technical article with a conclusion.'
IMAGE = {'src': 'https://example.com/a.png', 'alt': 'Chart'}

class LayoutTests(unittest.TestCase):
    def build(self, body, source, images=None, videos=None, fmt='html', base='https://example.com/post'):
        return build_layout(body, copy.deepcopy(images or [IMAGE]), copy.deepcopy(videos or []), source, fmt, base)

    def test_html_image_between_paragraphs(self):
        result=self.build(A+'\n\n'+B, f'<article><p>{A}</p><img src="/a.png"><p>{B}</p></article>')
        self.assertEqual(result['mediaLayout'][0]['afterBlockId'],'b0001')
        self.assertEqual(result['mediaLayoutStatus'],'complete')

    def test_markdown_multiple_and_repeated_images_keep_occurrences(self):
        source=f'{A}\n\n![one](/a.png)\n\n![two](/a.png)\n\n{B}'
        result=self.build(A+'\n\n'+B, source,fmt='markdown')
        self.assertEqual(len(result['mediaLayout']),2)
        self.assertEqual([x['afterBlockId'] for x in result['mediaLayout']],['b0001','b0001'])

    def test_cover_and_trailing_media(self):
        for source, expected in [(f'![one](/a.png)\n\n{A}',None),(f'{A}\n\n![one](/a.png)','b0001')]:
            self.assertEqual(self.build(A,source,fmt='markdown')['mediaLayout'][0]['afterBlockId'],expected)

    def test_code_has_independent_id_and_text_ids_match_translation(self):
        body=A+'\n\n```python\nprint("hello")\n```\n\n'+B
        blocks=layout_blocks(body)
        self.assertEqual([b['id'] for b in blocks],['b0001','c0001','b0002'])
        self.assertEqual([b['id'] for b in blocks if b['kind']!='code'],[b['id'] for b in article_blocks(body)])
        source=f'{A}\n\n```python\nprint("hello")\n```\n\n![one](/a.png)\n\n{B}'
        self.assertEqual(self.build(body,source,fmt='markdown')['mediaLayout'][0]['afterBlockId'],'c0001')

    def test_image_markup_inside_code_is_not_media(self):
        body=f'```md\n![one](/a.png)\n```\n\n{A}'
        result=self.build(body,body,fmt='markdown')
        self.assertEqual(result['mediaLayout'],[])

    def test_changed_or_ambiguous_context_is_not_guessed(self):
        for body in (A+'\n\n'+C, A+'\n\n'+B+'\n\n'+A+'\n\n'+B):
            result=self.build(body, f'{A}\n\n![one](/a.png)\n\n{B}',fmt='markdown')
            self.assertEqual(result['mediaLayout'],[])
            self.assertEqual(result['mediaLayoutStatus'],'unmatched')

    def test_headers_lists_and_inline_translation_block_ids(self):
        body=f'## {A}\n\n- {B}\n- {C}'
        source=f'<article><h2>{A}</h2><ul><li>{B}<img src="/a.png"></li><li>{C}</li></ul></article>'
        result=self.build(body,source)
        self.assertEqual(result['mediaLayout'][0]['afterBlockId'],'b0002')

    def test_template_cover_video_not_displaced_by_header_text(self):
        video={'kind':'embed','src':'https://www.youtube-nocookie.com/embed/Xfgq79gV_GM'}
        source=f'<main><header><h1>{C}</h1><template><iframe src="https://www.youtube.com/embed/Xfgq79gV_GM"></iframe></template></header><section class="post__content"><p>{A}</p></section></main>'
        result=self.build(A,source,videos=[video],base='https://github.blog/test')
        self.assertEqual(result['mediaLayout'][0]['type'],'video')
        self.assertIsNone(result['mediaLayout'][0]['afterBlockId'])

    def test_page_chrome_does_not_supply_position(self):
        result=self.build(A,f'<nav><img src="/a.png"></nav><article><p>{A}</p></article>')
        self.assertEqual(result['mediaLayout'],[])

    def test_exact_body_hash_invalidates_stale_layout(self):
        result=self.build(A,f'![one](/a.png)\n\n{A}',fmt='markdown')
        self.assertTrue(layout_current({'body':A,**result}))
        self.assertFalse(layout_current({'body':A+' changed',**result}))

    def test_backfill_preserves_body_and_all_non_media_fields(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'article.json'
            snapshot={'id':'0123456789ab','url':'https://example.com/post','body':A+'\n\n'+B,'contentKind':'page','images':[IMAGE],'aiReview':{'x':'y'},'fetchedAt':'old','custom':{'keep':True}}
            path.write_text(json.dumps(snapshot))
            def fetch(url):
                article_store.record_media_source(url,f'<article><p>{A}</p><img src="/a.png"><p>{B}</p></article>','html')
                return 'different body',url
            with patch.object(backfill,'fetch_page_text',side_effect=fetch):
                result=backfill.layout_backfill_item(snapshot,path=path)
            self.assertEqual(result,'layout:1')
            after=json.loads(path.read_text())
            for k,v in snapshot.items():
                if k!='images':self.assertEqual(after[k],v)
            self.assertEqual(after['images'][0]['src'],IMAGE['src'])

    def test_write_snapshot_preserves_layout_for_unchanged_body(self):
        item={'id':'0123456789ab','url':'https://example.com/post','publishedAt':'2026-09-26T00:00:00Z'}
        with tempfile.TemporaryDirectory() as d,patch.object(article_store,'ARTICLES_DIR',Path(d)):
            path=article_store.write_snapshot(item,A+'\n\n'+B,'feed',images=[copy.deepcopy(IMAGE)],media_source=(f'<p>{A}</p><img src="/a.png"><p>{B}</p>','html'))
            initial=json.loads(path.read_text());self.assertEqual(initial['mediaLayoutMatched'],1)
            article_store.write_snapshot(item,A+'\n\n'+B,'feed')
            self.assertEqual(json.loads(path.read_text())['mediaLayout'],initial['mediaLayout'])
            article_store.write_snapshot(item,C,'feed')
            self.assertNotIn('mediaLayout',json.loads(path.read_text()))
