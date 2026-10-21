import unittest
from pathlib import Path
from tests.fixtures.workflow import Workflow
from experience_memory.application.services import MemoryApplicationService

class CrossProjectTests(unittest.TestCase):
    def test_two_projects_same_id_and_same_project_two_sessions(self):
        a=Workflow();self.addCleanup(a.close)
        b=a.other_session('root','b');other=a.other_session('second','a')
        ar=a.memory.write(a.handle,'upsert',a.candidate(body='only a'),'new-a')
        br=b.memory.write(b.handle,'upsert',b.candidate(body='only b'),'new-b')
        self.assertNotEqual(ar['record_id'],br['record_id'])
        self.assertEqual(other.memory.read(other.handle,ar['record_id'])['record']['body'],'only a')
        with self.assertRaises(Exception):b.memory.read(b.handle,ar['record_id'])
        with self.assertRaises(Exception):other.memory.read(a.handle,ar['record_id'])
        read=a.memory.read(a.handle,ar['record_id'])
        a.retention.use(a.handle,read['receipt_id'],'P1','adoption','use')
        self.assertEqual(other.memory.status(other.handle)['u_cursor'],0)
        self.assertEqual(b.memory.status(b.handle)['u_cursor'],0)
        # Only IDs/status/hashes in plugin-owned persistent files.
        local=''.join(p.read_text(encoding='utf-8') for p in a.pointers.data_root.glob('*.json'))
        self.assertNotIn('only a',local);self.assertNotIn('rule summary',local)

    def test_project_config_is_never_reused_for_a_different_repository(self):
        import tempfile,subprocess
        from experience_memory.bootstrap import build_runtime
        from experience_memory.config import Config
        from tests.unit.test_mem0_store import PlatformDouble
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);a=root/'a';b=root/'b';a.mkdir();b.mkdir()
            for target in (a,b):subprocess.run(['git','-C',str(target),'init'],check=True,capture_output=True)
            runtime=build_runtime(Config(project_scope='local:a'),data_root=root/'state',
                                  project_root=a,plugin_root=Path(__file__).resolve().parents[2],client=PlatformDouble())
            self.assertEqual(runtime.sessions.resolve_scope(a).project_key,'local:a')
            with self.assertRaises(Exception):runtime.sessions.resolve_scope(b)
