import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_translation_progress import build_progress
from translate_articles import article_blocks, translatable_blocks, body_hash, TRANSLATION_VERSION

BODY = ('This is an English article about artificial intelligence and software development. ' * 3 + '\n\n' + 'Another paragraph explains how to use the model to build useful applications. ' * 3)

class ProgressTests(unittest.TestCase):
    def write(self, root, path, data):
        path = root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def snapshot(self, root, ident, body=BODY, kind='feed'):
        self.write(root, f'data/articles/2020/01/01/{ident}.json', {'id': ident, 'body': body, 'contentKind': kind})

    def translation(self, root, ident, count, stale=False):
        blocks = translatable_blocks(article_blocks(BODY))
        self.write(root, f'data/translations/zh-CN/2020/01/01/{ident}.json', {
            'articleId': ident, 'targetLanguage': 'zh-CN', 'translationVersion': TRANSLATION_VERSION,
            'sourceBodyHash': 'stale' if stale else body_hash(BODY),
            'status': 'complete', 'translatedBlocks': 999, 'totalBlocks': 999,
            'blocks': [{'id': b['id'], 'sourceHash': b['sourceHash'], 'translationZh': '中文译文'} for b in blocks[:count]],
        })

    def test_global_counts_all_states_and_actual_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.write(root, 'data/news.json', {'items': [{'id': str(i), 'aiReview': {'importanceLevel': 5 if i == 0 else 1}} for i in range(6)]})
            for i in range(3): self.snapshot(root,str(i))
            self.translation(root,'0',2)
            self.translation(root,'1',1)
            self.snapshot(root,'3',body='这是一篇关于人工智能的中文文章。')
            self.snapshot(root,'4',kind='summary')
            self.snapshot(root,'orphan')
            result=build_progress(root)
            self.assertEqual(result['documents'], {'total':7,'complete':1,'partial':1,'pending':2,'notRequired':1,'awaitingBody':2})
            self.assertEqual(result['translatedBlocks'],3)
            self.assertEqual(result['totalBlocks'],8)
            self.assertEqual(result['blockPercent'],37.5)
            self.assertEqual(result['documentPercent'],25)
            self.assertEqual(result['automaticEligibleDocuments'],1)

    def test_stale_records_do_not_count_as_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.write(root,'data/news.json',{'items':[{'id':'1'}]})
            self.snapshot(root,'1')
            self.translation(root,'1',2,stale=True)
            result=build_progress(root)
            self.assertEqual(result['staleDocuments'],1)
            self.assertEqual(result['documents']['pending'],1)
            self.assertEqual(result['translatedBlocks'],0)

    def test_empty_site_is_finite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.write(root,'data/news.json',{'items':[]})
            result=build_progress(root)
            self.assertEqual(result['documentPercent'],0)
            self.assertEqual(result['blockPercent'],0)
            self.assertEqual(result['documents']['total'],0)

    def test_duplicate_blocks_not_double_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.write(root,'data/news.json',{'items':[{'id':'1'},{'id':'1'}]})
            self.snapshot(root,'1')
            self.translation(root,'1',1)
            p=root/'data/translations/zh-CN/2020/01/01/1.json'
            record=json.loads(p.read_text());record['blocks']*=3;p.write_text(json.dumps(record))
            result=build_progress(root)
            self.assertEqual(result['documents']['total'],1)
            self.assertEqual(result['translatedBlocks'],1)


class RecentProgressTests(ProgressTests):
    def test_rolling_completion_counts_and_future_timestamp(self):
        from datetime import datetime, timezone, timedelta
        now=datetime(2026,9,26,9,0,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.write(root,'data/news.json',{'items':[]})
            ages=[timedelta(minutes=30),timedelta(hours=1),timedelta(hours=23),timedelta(days=6),timedelta(days=8),timedelta(minutes=-1)]
            for i,age in enumerate(ages):
                ident=str(i);self.snapshot(root,ident);self.translation(root,ident,2)
                p=root/f'data/translations/zh-CN/2020/01/01/{ident}.json'
                record=json.loads(p.read_text());record['completedAt']=(now-age).isoformat();p.write_text(json.dumps(record))
            result=build_progress(root,now)
            self.assertEqual(result['recentCompleted'],{'hour':2,'day':3,'week':4})

    def test_missing_completed_timestamp_not_inferred_from_attempt(self):
        from datetime import datetime, timezone
        now=datetime(2026,9,26,9,0,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.write(root,'data/news.json',{'items':[]})
            self.snapshot(root,'1');self.translation(root,'1',2)
            p=root/'data/translations/zh-CN/2020/01/01/1.json'
            record=json.loads(p.read_text());record['updatedAt']=now.isoformat();p.write_text(json.dumps(record))
            result=build_progress(root,now)
            self.assertEqual(result['recentCompleted'],{'hour':0,'day':0,'week':0})
            self.assertEqual(result['completedWithoutTimestamp'],1)
