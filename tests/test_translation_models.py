import copy
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from translation_models import ModelPool, free_text_model, PREFERRED
import translate_articles as tr


def model(ident):
    return {'id': ident, 'pricing': {'prompt': '0', 'completion': '0'},
            'architecture': {'input_modalities': ['text'], 'output_modalities': ['text']},
            'context_length': 32000, 'top_provider': {'max_completion_tokens': 8192}}


class ModelTests(unittest.TestCase):
    def test_reject_paid_specialist_nontext_and_inadequate_models(self):
        base = model(PREFERRED[0])
        self.assertTrue(free_text_model(base))
        for item in [dict(base, id='paid/model'), dict(base, pricing={'prompt':'0','completion':'1'}),
                     dict(base, pricing={'prompt':'0','completion':'0','request':'1'}),
                     dict(base, pricing={'prompt':'0'}), dict(base, context_length=1000),
                     dict(base, architecture={'input_modalities':['text'],'output_modalities':['audio']}),
                     dict(base, id='nvidia/content-safety:free')]:
            self.assertFalse(free_text_model(item), item)

    def test_quality_allowlist_cannot_be_bypassed_by_preference_or_catalog(self):
        rejected=['liquid/lfm-2.5-2.6b:free','openrouter/free','unknown/large-new-model:free']
        for ident in rejected:
            self.assertFalse(free_text_model(model(ident)))
            with tempfile.TemporaryDirectory() as d:
                pool=ModelPool(Path(d)/'health.json',ident,[model(ident),model(PREFERRED[0])])
                self.assertEqual(pool.select(),PREFERRED[0])
        with tempfile.TemporaryDirectory() as d:
            pool=ModelPool(Path(d)/'health.json',catalog=[model(x) for x in rejected])
            with self.assertRaises(RuntimeError): pool.select()

    def test_removed_model_ignored_and_persistent_cooldown_expires(self):
        now=datetime(2026,9,26,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'health.json'; catalog=[model(x) for x in PREFERRED]
            pool=ModelPool(path,'retired:free',catalog,now)
            self.assertEqual(pool.select(),PREFERRED[0])
            pool.failed(PREFERRED[0],'404',True)
            self.assertEqual(ModelPool(path,'',catalog,now).select(),PREFERRED[1])
            self.assertEqual(ModelPool(path,'',catalog,now+timedelta(days=1)).select(),PREFERRED[0])

    def test_failover_bounded_and_no_paid_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            pool=ModelPool(Path(d)/'health.json',catalog=[model(x) for x in PREFERRED])
            for _ in range(3): pool.failed(pool.select(),'failure')
            with self.assertRaises(RuntimeError): pool.select()
            pool=ModelPool(Path(d)/'empty.json',catalog=[model('paid/model')])
            with self.assertRaises(RuntimeError): pool.select()

    def test_shared_provider_limit_is_distinct_from_account_limits(self):
        for source, expected in [('upstream_provider_shared_pool',True),('account_daily_quota',False),('',False)]:
            detail=json.dumps({'error':{'metadata':{'limit_source':source}}}).encode()
            error=urllib.error.HTTPError('https://example.com',429,'limit',{},io.BytesIO(detail))
            message=tr.error_message(error)
            self.assertEqual(tr.shared_pool_limit(error),expected)
            self.assertEqual(tr.old_shared_pool_pause({'lastError':message}),expected)

    def test_request_enforces_zero_price(self):
        result={'choices':[{'message':{'content':'{"translations":[]}'}}]}
        class Response(io.BytesIO):
            headers={}
        with patch.object(tr.urllib.request,'urlopen',return_value=Response(json.dumps(result).encode())) as urlopen:
            tr.request_translation('not-a-real-token',PREFERRED[0],[],tr.OPENROUTER_ENDPOINT)
        payload=json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload['provider']['max_price'],{'prompt':0,'completion':0})

    def test_main_failover_retries_without_article_penalty_and_counts_budget(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);state={};record={'totalBlocks':1,'translatedBlocks':0}
            chunk=[{'id':'b0001','kind':'paragraph','source':'An example paragraph','sourceHash':'a'}]
            candidate=({'id':'one'}, {}, chunk,path/'article.json',record)
            attempts=[]
            def request(token, chosen, blocks, endpoint):
                attempts.append(chosen)
                if len(attempts)==1:
                    raise urllib.error.HTTPError(endpoint,404,'gone',{},io.BytesIO(b'gone'))
                return {},chosen,{}
            def candidates(*args): return [] if record.get('status')=='complete' else [candidate]
            def apply(rec,*args): rec.update(status='complete',translatedBlocks=1)
            with patch.dict(tr.os.environ,{'OPENROUTER_API_KEY':'test'}), \
                 patch.object(sys,'argv',['translate','--request-limit','3','--interval','0']), \
                 patch.object(tr,'remove_stale_records',return_value=0), \
                 patch.object(tr,'load_news',return_value={'items':[]}), \
                 patch.object(tr,'sync_requests',return_value={}), \
                 patch.object(tr,'resolve_items',return_value=[]), \
                 patch.object(tr,'publish_queue'), \
                 patch.object(tr,'load_state',return_value=state), \
                 patch.object(tr,'STATE_FILE',path/'state.json'), \
                 patch.object(tr,'INDEX_FILE',path/'index.json'), \
                 patch.object(tr,'MODEL_HEALTH_FILE',path/'health.json'), \
                 patch('translation_models.fetch_catalog',return_value=[model(x) for x in PREFERRED]), \
                 patch.object(tr,'select_candidates',side_effect=candidates), \
                 patch.object(tr,'pending_chunk',return_value=chunk), \
                 patch.object(tr,'request_translation',side_effect=request), \
                 patch.object(tr,'normalize_response',return_value=([{}],[])), \
                 patch.object(tr,'apply_chunk',side_effect=apply), \
                 patch.object(tr,'build_translation_index',return_value={}):
                self.assertEqual(tr.main(),0)
            self.assertEqual(attempts,PREFERRED[:2])
            self.assertEqual(state['requestsToday'],2)
            self.assertNotIn('failureCount',record)
            self.assertNotIn('nextAttemptAt',record)

if __name__=='__main__': unittest.main()
