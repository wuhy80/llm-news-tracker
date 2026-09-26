import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import translation_queue as queue
import translate_articles as translate

BODY = 'This is an English article about artificial intelligence and software engineering. ' * 20

class QueueTests(unittest.TestCase):
    def issue(self, number, ident='0123456789ab', **extra):
        return {'number': number, 'title': '[优先翻译] '+ident, 'state':'open', 'user':{'login':'owner'}, **extra}

    def test_authorization_and_deduplication(self):
        rows=[self.issue(1),self.issue(3),self.issue(2,'abcdef012345'),
              self.issue(4,'111111111111',user={'login':'outsider'}),self.issue(5,state='closed'),
              self.issue(6,pull_request={'url':'pr'}),self.issue(7,title='[优先翻译] ../../secrets')]
        result=queue.authorized_requests(rows,lambda login:'admin' if login=='owner' else 'read')
        self.assertEqual(list(result),['0123456789ab','abcdef012345'])
        self.assertEqual(result['0123456789ab']['issueNumber'],3)

    def test_manual_priority_bypasses_level_and_precedes_partial_automatic(self):
        items=[{'id':'auto','publishedAt':'2026-09-26','aiReview':{'importanceLevel':5}},
               {'id':'older','publishedAt':'2020-01-01','aiReview':{'importanceLevel':1}},
               {'id':'newer','publishedAt':'2020-01-01','aiReview':{'importanceLevel':2}},
               {'id':'ignored','aiReview':{'importanceLevel':1}}]
        with patch.object(translate,'snapshot_path',return_value=Path('any.json')), \
             patch.object(translate,'read_json',return_value={'contentKind':'feed','body':BODY}), \
             patch.object(translate,'load_record',return_value=(Path('t.json'),{'status':'partial','translatedBlocks':1})):
            result=translate.select_candidates(items,datetime.now(timezone.utc),priority_ids=['newer','older'])
        self.assertEqual([x[0]['id'] for x in result],['newer','older','auto'])

    def test_manual_priority_obeys_backoff_and_completion(self):
        items=[{'id':'waiting'},{'id':'done'}]
        future=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        def record(item,*args):
            return Path('t.json'), {'status':'complete'} if item['id']=='done' else {'status':'partial','nextAttemptAt':future}
        with patch.object(translate,'snapshot_path',return_value=Path('any.json')), \
             patch.object(translate,'read_json',return_value={'contentKind':'feed','body':BODY}), \
             patch.object(translate,'load_record',side_effect=record):
            result=translate.select_candidates(items,datetime.now(timezone.utc),priority_ids=['waiting','done'])
        self.assertEqual(result,[])

    def test_no_token_does_not_trust_cached_unverified_requests(self):
        with patch.dict('os.environ',{'GITHUB_TOKEN':''}):
            self.assertEqual(queue.sync_requests(),{})

    def test_github_pagination_and_permission_caching(self):
        filler=[{'number':i,'title':'unrelated'} for i in range(100)]
        def api(path,token):
            if '/collaborators/' in path:return {'permission':'write'}
            return filler if 'page=1' in path and '&page=1' in path else [self.issue(101),self.issue(102,'abcdef012345')]
        with patch.dict('os.environ',{'GITHUB_TOKEN':'test-token'}),patch.object(queue,'github_get',side_effect=api) as get:
            result=queue.sync_requests()
        self.assertEqual(len(result),2)
        self.assertEqual(sum('/collaborators/' in call.args[0] for call in get.call_args_list),1)
