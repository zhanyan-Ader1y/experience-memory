import unittest
from tests.fixtures.workflow import Workflow
from experience_memory.application.checkpoint import CheckpointCases


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.f=Workflow();self.addCleanup(self.f.close)
        f=self.f
        self.cases=CheckpointCases(f.experiences,f.controls,f.binding,lambda:f.host)
    def adopt(self,eid,priority='P2',summary='rule summary'):
        f=self.f;row=f.memory.write(f.handle,'upsert',f.candidate(eid=eid,summary=summary),'new-'+eid)
        receipt=f.memory.read(f.handle,row['record_id'])['receipt_id']
        f.retention.use(f.handle,receipt,priority,'actual adoption','use-'+eid)
        return row
    def test_consecutive_compression_eviction_is_authoritative_u_not_refreshed(self):
        self.adopt('EXP-A','P1');self.adopt('EXP-B','P3')
        f=self.f
        self.cases.prepare(f.handle,'boundary1','fixed{entries}end',8192)
        restored=self.cases.restore(f.handle,'boundary1','fixed{entries}end',50)
        self.assertNotIn('EXP-B',restored['additional_context'])
        self.assertEqual(f.memory.status(f.handle)['u_cursor'],2)
        self.cases.prepare(f.handle,'boundary2','fixed{entries}end',8192)
        restored2=self.cases.restore(f.handle,'boundary2','fixed{entries}end',8192)
        self.assertNotIn('EXP-B',restored2['additional_context'])
        self.assertEqual(f.memory.status(f.handle)['u_cursor'],2)
        self.assertEqual(restored2,self.cases.restore(f.handle,'boundary2','fixed{entries}end',8192))
        with self.assertRaises(Exception):self.cases.restore(f.handle,'old-boundary','fixed{entries}end',8192)
    def test_p1_changed_blocks_p2_changed_drops_and_invalidated_p1_drops(self):
        a=self.adopt('EXP-A','P1');b=self.adopt('EXP-B','P2');f=self.f
        self.cases.prepare(f.handle,'boundary','{entries}',8192)
        candidate=f.candidate(eid='EXP-A',body='changed');candidate.update(record_id=a['record_id'],expected_fingerprint=a['fingerprint'])
        candidate['deduplication']['decision']='replace'
        updated=f.memory.write(f.handle,'upsert',candidate,'change')
        with self.assertRaises(Exception):self.cases.restore(f.handle,'boundary','{entries}',8192)
        candidate['expected_fingerprint']=updated['fingerprint']
        f.memory.write(f.handle,'invalidate',candidate,'invalidate')
        candidate=f.candidate(eid='EXP-B',body='changed');candidate.update(record_id=b['record_id'],expected_fingerprint=b['fingerprint'])
        candidate['deduplication']['decision']='replace'
        f.memory.write(f.handle,'upsert',candidate,'change-b')
        result=self.cases.restore(f.handle,'boundary','{entries}',8192)
        self.assertEqual(result['additional_context'],'')
        self.assertEqual(len(result['dropped']),2)
    def test_missing_or_failed_current_checkpoint_never_uses_old_ready(self):
        f=self.f
        with self.assertRaises(Exception):self.cases.restore(f.handle,'missing','{entries}',8192)
        self.cases.prepare(f.handle,'one','{entries}',8192)
        self.cases.restore(f.handle,'one','{entries}',8192)
        f.api.lose_response=True
        with self.assertRaises(Exception):self.cases.prepare(f.handle,'two','{entries}',8192)
        with self.assertRaises(Exception):self.cases.restore(f.handle,'one','{entries}',8192)
        # Retrying the same boundary reconciles the server-side write without another add.
        self.cases.prepare(f.handle,'two','{entries}',8192)
        self.cases.restore(f.handle,'two','{entries}',8192)

    def test_resume_restores_current_window_instead_of_empty_formal_area(self):
        from experience_memory.application.services import RetentionApplicationService
        from experience_memory.interfaces.hooks import handle_event
        self.adopt('EXP-A','P1')
        f=self.f
        service=RetentionApplicationService(f.experiences,f.controls,None,binding=f.binding,
            host_source=lambda:f.host,core_loader=lambda cwd:'core')
        output=handle_event(service,{'hook_event_name':'SessionStart','source':'resume',
                                    'session_id':f.host.session_id,'cwd':str(f.host.cwd)})
        self.assertTrue(output['continue'])
        self.assertIn('EXP-A',output['hookSpecificOutput']['additionalContext'])
