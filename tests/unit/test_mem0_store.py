import json
from pathlib import Path
import tempfile
import unittest

from experience_memory.domain.models import Scope
from experience_memory.domain.ports import OperationStatus
from experience_memory.infrastructure.mem0_store import Mem0Store
from experience_memory.infrastructure.session_pointer import SessionPointerStore


class PlatformDouble:
    """In-process server substitute: exercises public request payloads, no network proof."""
    def __init__(self):
        self.rows = {}; self.calls = []; self.lose_response = False

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "GET":
            return dict(self.rows[path.split('/')[3]])
        if path == "/v3/memories/add/":
            rid = str(len(self.rows) + 1)
            self.rows[rid] = dict(id=rid, memory=payload['messages'][0]['content'],
                                  metadata=payload['metadata'], app_id=payload['app_id'], agent_id=payload['agent_id'])
            if self.lose_response:
                from experience_memory.domain.errors import MemoryError, ErrorCode
                self.lose_response = False
                raise MemoryError(ErrorCode.OPERATION_UNKNOWN)
            return {'results': [{'id': rid}]}
        if method == "PUT":
            row = self.rows[path.split('/')[3]]
            row.update(memory=payload['text'], metadata=payload['metadata'])
            return {'message': 'updated'}
        filters = payload['filters']['AND']
        if any(len(term.get('metadata',{}))>1 for term in filters):
            from experience_memory.domain.errors import MemoryError,ErrorCode
            raise MemoryError(ErrorCode.REMOTE_FAILURE)
        def matches(row):
            for term in filters:
                for key, value in term.items():
                    if key == 'metadata':
                        if any(row['metadata'].get(k) != v for k,v in value.items()): return False
                    elif row.get(key) != value: return False
            return True
        rows = [dict(r) for r in self.rows.values() if matches(r)]
        if path.startswith('/v3/memories/search/'):
            return {'results': rows[:payload['top_k']]}
        return {'results': rows, 'next': None}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.api = PlatformDouble()
        self.store = Mem0Store(self.api, SessionPointerStore(Path(self.tmp.name)))
        self.scope = Scope('local:a','app','agent',(), 'user')

    def test_save_readback_and_same_operation_does_not_write_twice(self):
        a = self.store.save(self.scope,'window','one',{'u':0},'op1')
        b = self.store.save(self.scope,'window','one',{'u':0},'op1')
        self.assertEqual(a.status, OperationStatus.CONFIRMED)
        self.assertEqual(a.record_ids,b.record_ids)
        self.assertEqual(sum(p=='/v3/memories/add/' for _,p,_ in self.api.calls),1)
        self.assertEqual(self.store.get(self.scope,a.record_ids[0],'window')['u'],0)

    def test_lost_response_reconciles_without_resubmission(self):
        self.api.lose_response = True
        first = self.store.save(self.scope,'window','one',{'u':0},'op1')
        self.assertEqual(first.status,OperationStatus.UNKNOWN)
        second = self.store.save(self.scope,'window','one',{'u':0},'op1')
        self.assertEqual(second.status,OperationStatus.CONFIRMED)
        self.assertEqual(len(self.api.rows),1)

    def test_unknown_and_temporarily_invisible_result_never_resubmits(self):
        self.api.lose_response = True
        self.store.save(self.scope,'window','one',{'u':0},'op1')
        self.api.rows.clear()
        self.assertEqual(self.store.save(self.scope,'window','one',{'u':0},'op1').status,OperationStatus.UNKNOWN)
        self.assertEqual(sum(p=='/v3/memories/add/' for _,p,_ in self.api.calls),1)

    def test_cross_scope_duplicate_key_and_changed_retry_rejected(self):
        a=self.store.save(self.scope,'window','one',{'u':0},'op1')
        with self.assertRaises(Exception): self.store.get(Scope('local:b','app','other',(),'user'),a.record_ids[0],'window')
        with self.assertRaises(Exception): self.store.save(self.scope,'window','one',{'u':1},'op1')
        # A distinct operation cannot create a second immutable logical key.
        with self.assertRaises(Exception): self.store.save(self.scope,'window','one',{'u':0},'op2')
        local=''.join(p.read_text() for p in Path(self.tmp.name).glob('*.json'))
        self.assertNotIn('"u"',local)

    def test_unknown_logical_key_cannot_be_retried_with_a_new_operation_id(self):
        self.api.lose_response=True
        self.store.save(self.scope,'window','one',{'u':0},'op1')
        self.api.rows.clear()
        with self.assertRaises(Exception):self.store.save(self.scope,'window','one',{'u':0},'different-op')
        self.assertEqual(sum(p=='/v3/memories/add/' for _,p,_ in self.api.calls),1)

    def test_async_event_pending_then_success_never_resubmits(self):
        original=self.api.request
        event_status=['RUNNING']
        def request(method,path,payload=None):
            if path=='/v3/memories/add/':
                original(method,path,payload)
                return {'event_id':'event-one','status':'PENDING'}
            if path=='/v1/event/event-one/':return {'status':event_status[0]}
            return original(method,path,payload)
        self.api.request=request
        first=self.store.save(self.scope,'window','one',{'u':0},'async')
        self.assertEqual(first.status,OperationStatus.PENDING)
        running=self.store.save(self.scope,'window','one',{'u':0},'async')
        self.assertEqual(running.status,OperationStatus.PENDING)
        event_status[0]='SUCCEEDED'
        done=self.store.save(self.scope,'window','one',{'u':0},'async')
        self.assertEqual(done.status,OperationStatus.CONFIRMED)
        self.assertEqual(len(self.api.rows),1)
    def test_guard_publication_interruption_blocks_new_operation_after_dispatch(self):
        from unittest.mock import patch
        from experience_memory.domain.errors import MemoryError,ErrorCode
        original=self.store.pointers._publish
        counter=[0]
        def interrupted(pointer):
            counter[0]+=1
            if counter[0]==2:raise MemoryError(ErrorCode.POINTER_IO_ERROR)
            return original(pointer)
        with patch.object(self.store.pointers,'_publish',side_effect=interrupted):
            with self.assertRaises(MemoryError):self.store.save(self.scope,'window','one',{'u':0},'first')
        # A crash before HTTP dispatch may conservatively freeze. Never create two operations for this key.
        with self.assertRaises(MemoryError):self.store.save(self.scope,'window','one',{'u':0},'different')
