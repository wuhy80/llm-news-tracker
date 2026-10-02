import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from huggingface_content import extract,remap_translation
from translate_articles import article_blocks,body_hash,TRANSLATION_VERSION
from repair_huggingface import repair_one
import article_store
URL='https://huggingface.co/blog/test'
def page(body):
    avatars=''.join(f'<img src="/avatars/{i}.svg" class="rounded-full">' for i in range(15))
    return f'<html><main><div class="blog-content"><h1>Title outside body</h1>{avatars}<div class="relative overflow-clip">{body}</div></div><article>Recommended stuff<img src="/wrong.jpg"></article><form>Upload images</form></main></html>'
class HFTests(unittest.TestCase):
    def test_private_download_links_are_not_published(self):
        private = 'https://internal-api-drive-stream.feishu.cn/download?code=EXAMPLE'
        r = extract(page(f'<p><a href="{private}">Download</a></p><img src="{private}">'), URL)
        self.assertNotIn(private, json.dumps(r))
        self.assertEqual(r['images'], [])
        self.assertEqual(r['contentIntegrity']['status'], 'partial')
    def test_scope_all_images_and_positions(self):
        r=extract(page('<p>Opening.</p>'+''.join(f'<p><img src="/{i}.png"></p><p>Step {i}.</p>' for i in range(18))),URL)
        self.assertEqual(len(r['images']),18);self.assertNotIn('Recommended',r['body']);self.assertNotIn('Upload',r['body'])
        self.assertEqual(r['mediaLayout'][-1]['afterBlockId'],'b0018');self.assertEqual(r['contentIntegrity']['sourceImages'],18)
        self.assertFalse(any('avatar' in x['src'] for x in r['images']))
    def test_table_images_blank_cells_links_and_short_text(self):
        r=extract(page('<h2>Setup</h2><p>Use <a href="/datasets/test"><strong>this dataset</strong></a>.</p><table><tr><th># Params</th><th>Score</th></tr><tr><td>27B<img src="/chart.png"></td><td></td></tr></table>'),URL)
        self.assertEqual(len(r['images']),1);self.assertEqual(r['mediaLayout'][0]['afterBlockId'],'b0005')
        self.assertEqual(len([a for a in r['inlineContent'] if 'table' in a]),4)
        b=r['inlineContent'][1];s=next(s for s in b['spans'] if s['kind']=='a')
        self.assertEqual(b['source'][s['start']:s['end']],'this dataset');self.assertEqual(s['href'],'https://huggingface.co/datasets/test')
    def test_group_direct_inline_children(self):
        r=extract(page('Read <a href="/test">this</a> paragraph.<p>Next.</p>'),URL)
        self.assertEqual(r['inlineContent'][0]['source'],'Read this paragraph.')
        self.assertEqual(len(r['inlineContent']),2)
    def test_code_exact_and_long_prose(self):
        code='def hello():\n    print("```literal```")'
        r=extract(page('<pre><code>'+code+'</code></pre><p>'+'Long content. '*3000+'</p>'),URL)
        self.assertEqual(r['codeContent'][0]['source'],code);self.assertGreater(len(r['body']),30000)
        self.assertEqual(len(article_blocks(r['body'])),1);self.assertFalse(r['contentIntegrity']['bodyTruncated'])
    def test_scope_failure_and_unsafe_urls(self):
        with self.assertRaises(ValueError):extract('<main>Sign in</main>',URL)
        r=extract(page('<p><a href="javascript:alert(1)">Text</a><script>evil()</script></p>'),URL)
        self.assertEqual(r['body'],'Text');self.assertEqual(r['inlineContent'][0]['spans'],[])
    def test_no_shifted_translation_or_changed_text_reuse(self):
        old='Page title\n\nOriginal paragraph.\n\nRecommendation.'
        record={'translationVersion':TRANSLATION_VERSION,'sourceBodyHash':body_hash(old),'blocks':[{**b,'translationZh':'译文'+b['id']} for b in article_blocks(old)],'completedAt':'old','blockFailures':{},'wordWise':[]}
        n=remap_translation(record,old,'Original paragraph.\n\nNew paragraph.')
        self.assertEqual(n['blocks'][0]['id'],'b0001');self.assertEqual(n['blocks'][0]['translationZh'],'译文b0002')
        self.assertEqual(n['status'],'partial');self.assertNotIn('completedAt',n);self.assertNotIn('blockFailures',n)
        with self.assertRaises(ValueError):remap_translation(record,'mismatched','new')
    def test_historical_summary_repair_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p=root/'data/articles/2021/01/01/0123456789ab.json';p.parent.mkdir(parents=True)
            p.write_text(json.dumps({'id':p.stem,'url':URL,'body':'summary','contentKind':'summary','aiReview':{'score':99},'summaryZh':'摘要'}))
            repair_one(p,page('<p>Full source.</p>'),root);r=json.loads(p.read_text())
            self.assertEqual(r['body'],'Full source.');self.assertEqual(r['contentKind'],'page');self.assertEqual(r['summaryZh'],'摘要');self.assertEqual(r['aiReview']['score'],99)
    def test_new_snapshot_and_feed_no_downgrade(self):
        item={'id':'0123456789ab','url':URL,'publishedAt':'2020-01-01T00:00:00Z'}
        with tempfile.TemporaryDirectory() as t,patch.object(article_store,'ARTICLES_DIR',Path(t)):
            p=article_store.write_snapshot(item,'old','page',media_source=(page('<p>Full source.</p>'),'html'))
            article_store.write_snapshot(item,'new summary','feed')
            self.assertEqual(json.loads(p.read_text())['body'],'Full source.')
if __name__=='__main__':unittest.main()
