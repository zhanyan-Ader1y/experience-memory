"""Opt-in public API integration in independent synthetic scopes. No native G1 proof."""
import os
from pathlib import Path
import tempfile
import unittest
import uuid
import time
from experience_memory.domain.errors import MemoryError, ErrorCode
from experience_memory.infrastructure.mem0_client import Mem0Client,credentials
from experience_memory.infrastructure.mem0_store import Mem0Store
from experience_memory.infrastructure.session_pointer import SessionPointerStore
from experience_memory.infrastructure.experience_repository import Mem0ExperienceRepository
from experience_memory.infrastructure.control_repository import Mem0ControlRepository
from experience_memory.application.session_binding import HostContext,SessionBinding
from experience_memory.application.memory import MemoryCases
from experience_memory.application.registry import Registry
from experience_memory.application.checkpoint import CheckpointCases
from experience_memory.domain.models import Scope


def settle(operation, timeout=120):
    """Resume the same logical operation; never hide failed or conflicting results."""
    deadline=time.monotonic()+timeout
    while True:
        try:
            result=operation()
        except MemoryError as error:
            if error.code not in (ErrorCode.OPERATION_PENDING,ErrorCode.OPERATION_UNKNOWN):
                raise
            state=error.code.value
        else:
            if not isinstance(result,dict) or result.get('status') not in ('pending','unknown'):
                return result
            state=result['status']
        if time.monotonic()>=deadline:
            raise AssertionError('Synthetic operation did not settle: '+state)
        print('synthetic operation awaiting '+state,flush=True)
        time.sleep(1)


@unittest.skipUnless(os.environ.get('EXPERIENCE_MEMORY_LIVE_FUNCTIONAL')=='1','live functional API opt-in absent; unverified')
class FunctionalPlatformTests(unittest.TestCase):
    def test_two_synthetic_projects_write_read_use_restore_update_invalidate(self):
        key=credentials()
        if not key:self.skipTest('credentials absent; unverified')
        run=uuid.uuid4().hex
        print('synthetic functional run='+run,flush=True)
        with tempfile.TemporaryDirectory() as directory:
            pointers=SessionPointerStore(Path(directory)/'state')
            store=Mem0Store(Mem0Client(key),pointers)
            controls=Mem0ControlRepository(store);experiences=Mem0ExperienceRepository(store)
            first=None
            for suffix in ('a','b'):
                scope=Scope('local:functional-'+run+'-'+suffix,'experience-memory-contract-test','contract-'+run+'-'+suffix,(),'synthetic-user')
                host=HostContext(Path(directory),'session-'+suffix,'synthetic-host','live-test-fixture')
                binding=SessionBinding(pointers,controls,lambda cwd:scope)
                ctx=settle(lambda:binding.bind(host))
                memory=MemoryCases(experiences,controls,binding,lambda:host)
                candidate={'experience_id':'EXP-SYNTHETIC-1','body':'Synthetic deterministic rule '+suffix,
                    'retention_text':'Synthetic summary '+suffix,'sources':['synthetic functional test'],
                    'status':'active','metadata':{'evidence_type':'verified'},'tags':['synthetic'],
                    'admission_basis':'synthetic fixture','deduplication':{'decision':'new','reason':'reviewed fixture'}}
                saved=settle(lambda:memory.write(ctx.session_handle,'upsert',candidate,'create-'+suffix))
                self.assertEqual(saved['status'],'confirmed')
                results=memory.search(ctx.session_handle,'Synthetic deterministic rule '+suffix,5)
                self.assertTrue(any(r['record_id']==saved['record_id'] for r in results['records']))
                read=settle(lambda:memory.read(ctx.session_handle,saved['record_id']))
                registry=Registry(experiences,controls,binding,lambda:host)
                used=settle(lambda:registry.use(ctx.session_handle,read['receipt_id'],'P1','synthetic adoption','use-'+suffix))
                self.assertEqual(used['entry']['last_used_seq'],1)
                checkpoint=CheckpointCases(experiences,controls,binding,lambda:host)
                settle(lambda:checkpoint.prepare(ctx.session_handle,'boundary','core{entries}end',8192))
                restored=settle(lambda:checkpoint.restore(ctx.session_handle,'boundary','core{entries}end',8192))
                self.assertIn('EXP-SYNTHETIC-1',restored['additional_context'])
                self.assertEqual(memory.status(ctx.session_handle)['u_cursor'],1)
                candidate.update(record_id=saved['record_id'],expected_fingerprint=read['record']['fingerprint'])
                candidate['deduplication']['decision']='replace';candidate['body']='Synthetic revised '+suffix
                updated=settle(lambda:memory.write(ctx.session_handle,'upsert',candidate,'update-'+suffix))
                candidate['expected_fingerprint']=updated['fingerprint']
                invalidated=settle(lambda:memory.write(ctx.session_handle,'invalidate',candidate,'invalidate-'+suffix))
                self.assertEqual(invalidated['status'],'confirmed')
                self.assertEqual(settle(lambda:memory.read(ctx.session_handle,saved['record_id']))['record']['status'],'invalidated')
                if first:
                    with self.assertRaises(Exception):memory.read(ctx.session_handle,first)
                first=saved['record_id']
