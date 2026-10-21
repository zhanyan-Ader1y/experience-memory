import json
import time
import unittest
from unittest.mock import patch
from dataclasses import replace
from tests.fixtures.workflow import Workflow
from experience_memory.infrastructure.mem0_client import Mem0Client,deadline
from experience_memory.application.checkpoint import CheckpointCases
from experience_memory.domain.errors import MemoryError,ErrorCode
from experience_memory.infrastructure.host_context import request_host


class FailureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=Workflow();self.addCleanup(self.f.close)
    def adopted(self):
        f=self.f;row=f.memory.write(f.handle,'upsert',f.candidate(),'create')
        receipt=f.memory.read(f.handle,row['record_id'])['receipt_id']
        return receipt
    def test_unknown_use_window_reconciles_original_u_and_old_retry_cannot_roll_back(self):
        f=self.f;receipt=self.adopted();f.api.lose_response=True
        with self.assertRaises(MemoryError):f.retention.use(f.handle,receipt,'P1','used','u1')
        with self.assertRaises(MemoryError):f.retention.use(f.handle,receipt,'P1','used','new-op')
        first=f.retention.use(f.handle,receipt,'P1','used','u1')
        second=f.retention.use(f.handle,receipt,'P1','used again','u2')
        self.assertEqual(first['entry']['last_used_seq'],1);self.assertEqual(second['entry']['last_used_seq'],2)
        self.assertEqual(first,f.retention.use(f.handle,receipt,'P1','used','u1'))
        self.assertEqual(f.memory.status(f.handle)['u_cursor'],2)
    def test_receipt_expiry_and_session_binding_recovery(self):
        f=self.f;receipt=self.adopted()
        for row in f.api.rows.values():
            if row['metadata']['record_kind']=='read_receipt':
                value=json.loads(row['memory']);value['expires_at']=0;row['memory']=json.dumps(value)
        with self.assertRaises(MemoryError):f.retention.use(f.handle,receipt,'P1','used','expired')
        self.assertEqual(f.memory.status(f.handle)['u_cursor'],0)
    def test_failed_window_publication_never_returns_restore_body_and_retries_exactly(self):
        f=self.f;receipt=self.adopted();f.retention.use(f.handle,receipt,'P1','used','use')
        cases=CheckpointCases(f.experiences,f.controls,f.binding,lambda:f.host)
        cases.prepare(f.handle,'b1','{entries}',8192)
        original=f.pointers._publish
        def fail_publish(pointer):
            if pointer.session_id==f.host.session_id and pointer.checkpoint_state=='restored':
                raise MemoryError(ErrorCode.POINTER_IO_ERROR)
            return original(pointer)
        with patch.object(f.pointers,'_publish',side_effect=fail_publish):
            with self.assertRaises(MemoryError):cases.restore(f.handle,'b1','{entries}',8192)
        windows_before=sum(row['metadata']['record_kind']=='active_window' for row in f.api.rows.values())
        restored=cases.restore(f.handle,'b1','{entries}',8192)
        self.assertIn('EXP-TEST-1',restored['additional_context'])
        self.assertEqual(windows_before,sum(row['metadata']['record_kind']=='active_window' for row in f.api.rows.values()))
    def test_unverified_metadata_contract_is_disabled_even_with_a_real_handle(self):
        f=self.f
        with self.assertRaises(MemoryError):
            with request_host({'experience_memory_host':{'cwd':str(f.host.cwd),'session_id':f.host.session_id}},'host'):
                f.binding.verify(f.handle,f.host)
    def test_http_deadline_and_auth_failure_are_codes_only(self):
        import urllib.error
        client=Mem0Client('synthetic-secret')
        with patch('experience_memory.infrastructure.mem0_client.time.monotonic',side_effect=[1.0,2.0]):
            with deadline(.001),patch.object(client.http,'open') as opening:
                with self.assertRaises(MemoryError) as caught:client.request('GET','/v1/memories/r/')
                self.assertEqual(caught.exception.code,ErrorCode.OPERATION_UNKNOWN);opening.assert_not_called()
        error=urllib.error.HTTPError('https://api.mem0.ai',401,'private-response',{},None)
        with patch.object(client.http,'open',side_effect=error):
            with self.assertRaises(MemoryError) as caught:client.request('GET','/v1/memories/r/')
            self.assertEqual(str(caught.exception),'AUTHENTICATION_FAILED')
