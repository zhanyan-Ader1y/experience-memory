import tempfile
from pathlib import Path
import unittest
from experience_memory.application.session_binding import SessionBinding, HostContext
from experience_memory.domain.models import Scope
from experience_memory.infrastructure.control_repository import Mem0ControlRepository
from experience_memory.infrastructure.mem0_store import Mem0Store
from experience_memory.infrastructure.session_pointer import SessionPointerStore
from tests.unit.test_mem0_store import PlatformDouble

class BindingTests(unittest.TestCase):
    def test_binding_survives_restart_and_rejects_other_session_host_project(self):
        with tempfile.TemporaryDirectory() as directory:
            pointers=SessionPointerStore(Path(directory)/"state")
            store=Mem0Store(PlatformDouble(),pointers)
            controls=Mem0ControlRepository(store)
            scope=Scope("local:a","app","agent",(),"user")
            binder=SessionBinding(pointers,controls,lambda cwd: scope)
            host=HostContext(Path(directory),"session-a","host", "test-fixture")
            ctx=binder.bind(host)
            self.assertEqual(binder.bind(host),ctx)
            self.assertEqual(binder.verify(ctx.session_handle,host),ctx)
            for other in (HostContext(Path(directory),"session-b","host","test-fixture"),
                          HostContext(Path(directory),"session-a","other","test-fixture")):
                with self.assertRaises(Exception): binder.verify(ctx.session_handle,other)
            wrong=SessionBinding(pointers,controls,lambda cwd: Scope("local:b","app","other",(),"user"))
            with self.assertRaises(Exception): wrong.verify(ctx.session_handle,host)
            with self.assertRaises(Exception): binder.verify(ctx.session_handle,None)
