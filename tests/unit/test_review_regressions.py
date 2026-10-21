"""Final independent-review regressions: public workflows with interrupted remote writes."""
import json
import unittest
from tests.fixtures.workflow import Workflow
from experience_memory.domain.errors import MemoryError,ErrorCode
from experience_memory.application.checkpoint import CheckpointCases
from experience_memory.application.services import RetentionApplicationService
from experience_memory.interfaces.hooks import handle_event


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.f=Workflow();self.addCleanup(self.f.close)

    def async_kind(self,kind):
        f=self.f;original=f.api.request;status=['RUNNING']
        def request(method,path,payload=None):
            if path=='/v1/event/review-event/':return {'status':status[0]}
            if path=='/v3/memories/add/' and payload['metadata']['record_kind']==kind:
                original(method,path,payload)
                return {'event_id':'review-event','status':'PENDING'}
            return original(method,path,payload)
        f.api.request=request
        return status

    def create(self):return self.f.memory.write(self.f.handle,'upsert',self.f.candidate(),'create')

    def adopted(self):
        f=self.f;row=self.create();receipt=f.memory.read(f.handle,row['record_id'])['receipt_id']
        return receipt

    def test_read_receipt_async_and_lost_response_resume_original_operation(self):
        f=self.f;row=self.create();status=self.async_kind('read_receipt')
        for _ in range(2):
            with self.assertRaises(MemoryError):f.memory.read(f.handle,row['record_id'])
        self.assertEqual(sum(r['metadata']['record_kind']=='read_receipt' for r in f.api.rows.values()),1)
        status[0]='SUCCEEDED'
        first=f.memory.read(f.handle,row['record_id'])
        self.assertEqual(first,f.memory.read(f.handle,row['record_id']))
        self.assertEqual(sum(r['metadata']['record_kind']=='read_receipt' for r in f.api.rows.values()),1)

    def test_read_receipt_response_loss_does_not_create_a_second_receipt(self):
        f=self.f;row=self.create();f.api.lose_response=True
        with self.assertRaises(MemoryError):f.memory.read(f.handle,row['record_id'])
        receipt=f.memory.read(f.handle,row['record_id'])['receipt_id']
        self.assertEqual(sum(r['metadata']['record_kind']=='read_receipt' for r in f.api.rows.values()),1)
        self.assertEqual(receipt,f.memory.read(f.handle,row['record_id'])['receipt_id'])

    def test_visible_use_event_is_not_confirmed_until_original_event_finishes(self):
        f=self.f;receipt=self.adopted();status=self.async_kind('use_event')
        for _ in range(2):
            with self.assertRaises(MemoryError):f.retention.use(f.handle,receipt,'P1','actual adoption','use')
        pointer=f.pointers.read(f.scope.project_key,f.host.session_id)
        self.assertEqual(pointer.state,'pending')
        status[0]='SUCCEEDED'
        result=f.retention.use(f.handle,receipt,'P1','actual adoption','use')
        self.assertEqual(result['entry']['last_used_seq'],1)

    def test_visible_restore_event_never_publishes_before_event_finishes(self):
        f=self.f;receipt=self.adopted();f.retention.use(f.handle,receipt,'P1','adopted','use')
        cases=CheckpointCases(f.experiences,f.controls,f.binding,lambda:f.host)
        cases.prepare(f.handle,'boundary','{entries}',8192)
        status=self.async_kind('restore_event')
        for _ in range(2):
            with self.assertRaises(MemoryError):cases.restore(f.handle,'boundary','{entries}',8192)
        self.assertEqual(f.pointers.read(f.scope.project_key,f.host.session_id).state,'pending')
        status[0]='SUCCEEDED'
        self.assertIn('EXP-TEST-1',cases.restore(f.handle,'boundary','{entries}',8192)['additional_context'])

    def test_definite_write_failure_stays_failed_and_new_operation_can_proceed(self):
        f=self.f;original=f.api.request;failing=[True]
        def request(method,path,payload=None):
            if failing[0] and path=='/v3/memories/add/' and payload['metadata']['record_kind']=='project_experience':
                raise MemoryError(ErrorCode.AUTHENTICATION_FAILED)
            return original(method,path,payload)
        f.api.request=request
        first=f.memory.write(f.handle,'upsert',f.candidate(),'failed')
        self.assertEqual(first['status'],'failed')
        failing[0]=False
        self.assertEqual(f.memory.write(f.handle,'upsert',f.candidate(),'failed')['status'],'failed')
        self.assertEqual(f.memory.write(f.handle,'upsert',f.candidate(),'new-operation')['status'],'confirmed')

    def test_public_resume_hook_reconciles_interrupted_window(self):
        f=self.f;row=self.create();receipt=f.memory.read(f.handle,row['record_id'])['receipt_id']
        f.retention.use(f.handle,receipt,'P2','adopted','use')
        candidate=f.candidate();candidate.update(record_id=row['record_id'],expected_fingerprint=row['fingerprint'])
        candidate['deduplication']['decision']='replace';f.memory.write(f.handle,'invalidate',candidate,'invalid')
        service=RetentionApplicationService(f.experiences,f.controls,None,binding=f.binding,
            host_source=lambda:f.host,core_loader=lambda cwd:'core')
        event={'hook_event_name':'SessionStart','source':'resume','cwd':str(f.host.cwd),'session_id':f.host.session_id}
        f.api.lose_response=True
        self.assertFalse(handle_event(service,event)['continue'])
        output=handle_event(service,event)
        self.assertTrue(output['continue'],output)
        self.assertNotIn('EXP-TEST-1',output['hookSpecificOutput']['additionalContext'])

    def test_failed_guard_publication_interruption_recovers_terminal_before_new_write(self):
        from unittest.mock import patch
        f=self.f;original_request=f.api.request;original_publish=f.pointers._publish
        failing=[True]
        def request(method,path,payload=None):
            if failing[0] and path=='/v3/memories/add/' and payload['metadata']['record_kind']=='project_experience':
                raise MemoryError(ErrorCode.AUTHENTICATION_FAILED)
            return original_request(method,path,payload)
        def publish(pointer):
            if pointer.session_id.startswith('logical-') and pointer.operation_status=='failed':
                raise MemoryError(ErrorCode.POINTER_IO_ERROR)
            return original_publish(pointer)
        f.api.request=request
        with patch.object(f.pointers,'_publish',side_effect=publish):
            with self.assertRaises(MemoryError):f.memory.write(f.handle,'upsert',f.candidate(),'failed')
        failing[0]=False
        with self.assertRaises(MemoryError):f.memory.write(f.handle,'upsert',f.candidate(body='changed retry'),'failed')
        self.assertEqual(f.memory.write(f.handle,'upsert',f.candidate(),'failed')['status'],'failed')
        self.assertEqual(f.memory.write(f.handle,'upsert',f.candidate(),'new')['status'],'confirmed')
