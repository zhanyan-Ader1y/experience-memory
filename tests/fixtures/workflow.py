"""Local adapter integration harness; makes no native-host or live-service claim."""
from pathlib import Path
import tempfile
from experience_memory.application.session_binding import SessionBinding,HostContext
from experience_memory.application.memory import MemoryCases
from experience_memory.application.registry import Registry
from experience_memory.infrastructure.control_repository import Mem0ControlRepository
from experience_memory.infrastructure.experience_repository import Mem0ExperienceRepository
from experience_memory.infrastructure.mem0_store import Mem0Store
from experience_memory.infrastructure.session_pointer import SessionPointerStore
from experience_memory.domain.models import Scope
from tests.unit.test_mem0_store import PlatformDouble


class Workflow:
    def __init__(self, shared=None, session='root', project='a'):
        if shared is None:
            self.tmp=tempfile.TemporaryDirectory()
            self.api=PlatformDouble(); self.pointers=SessionPointerStore(Path(self.tmp.name)/'pointers')
        else:
            self.tmp=None;self.api,self.pointers=shared.api,shared.pointers
        self.scope=Scope('local:'+project,'app',project,(),'user')
        self.host=HostContext(self.pointers.data_root.parent,session,'host','test-fixture')
        self.store=Mem0Store(self.api,self.pointers)
        self.controls=Mem0ControlRepository(self.store);self.experiences=Mem0ExperienceRepository(self.store)
        self.binding=SessionBinding(self.pointers,self.controls,lambda path:self.scope)
        self.handle=self.binding.bind(self.host).session_handle
        self.memory=MemoryCases(self.experiences,self.controls,self.binding,lambda:self.host)
        self.retention=Registry(self.experiences,self.controls,self.binding,lambda:self.host)
    def close(self):
        if self.tmp:self.tmp.cleanup()
    def other_session(self,session,project='a'):return Workflow(self,session,project)
    def candidate(self,body='verified rule',eid='EXP-TEST-1',summary='rule summary'):
        return {'experience_id':eid,'body':body,'retention_text':summary,'status':'active',
                'sources':['synthetic-test'],'tags':['python'],'admission_basis':'local integration fixture',
                'metadata':{'evidence_type':'verified'},'deduplication':{'decision':'new','reason':'reviewed'}}
