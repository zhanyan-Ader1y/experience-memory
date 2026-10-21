import tempfile
from pathlib import Path
import unittest
from tests.fixtures.workflow import Workflow
from experience_memory.application.services import MemoryApplicationService,RetentionApplicationService
from experience_memory.application.contracts import HookRequest
from experience_memory.bootstrap import build_runtime
from experience_memory.config import Config
from experience_memory.interfaces.hooks import handle_event

class HookTests(unittest.TestCase):
    def test_start_precompact_restore_and_full_payload_budget(self):
        f=Workflow();self.addCleanup(f.close)
        service=RetentionApplicationService(f.experiences,f.controls,None,binding=f.binding,
            host_source=lambda:f.host,core_loader=lambda cwd:'fixed core',budget_bytes=8192)
        event={'cwd':str(f.host.cwd),'session_id':f.host.session_id,'hook_event_name':'SessionStart','source':'startup'}
        output=handle_event(service,event)
        self.assertTrue(output['continue']);self.assertIn(f.handle,output['hookSpecificOutput']['additionalContext'])
        before=dict(event,hook_event_name='PreCompact',trigger='manual',turn_id='boundary1');before.pop('source')
        self.assertEqual(handle_event(service,before),{'continue':True,'suppressOutput':True})
        after=dict(event,source='compact',turn_id='boundary1')
        restored=handle_event(service,after)
        self.assertTrue(restored['continue'])
        context=restored['hookSpecificOutput']['additionalContext']
        self.assertIn('正式留存区',context);self.assertLessEqual(len(context.encode('utf-8')),8192)
        self.assertFalse(handle_event(service,dict(after,turn_id='different'))['continue'])
    def test_missing_boundary_or_source_never_uses_last_checkpoint(self):
        f=Workflow();self.addCleanup(f.close)
        service=RetentionApplicationService(f.experiences,f.controls,None,binding=f.binding,
            host_source=lambda:f.host,core_loader=lambda cwd:'fixed core')
        event={'cwd':str(f.host.cwd),'session_id':f.host.session_id,'hook_event_name':'PreCompact','trigger':'auto'}
        self.assertFalse(handle_event(service,event)['continue'])
        runtime=build_runtime(Config())
        with self.assertRaises(Exception):runtime.memory.search('caller-supplied-handle','rule',5)
