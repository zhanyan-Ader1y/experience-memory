import unittest
from tests.fixtures.workflow import Workflow

class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.flow=Workflow(); self.addCleanup(self.flow.close)
    def test_write_search_read_update_invalidate_and_tags(self):
        f=self.flow
        self.assertEqual(f.memory.search(f.handle,"rule",5)["records"],[])
        saved=f.memory.write(f.handle,"upsert",f.candidate(),"create")
        self.assertEqual(saved["status"],"confirmed")
        rid=saved["record_id"]
        self.assertEqual(f.memory.write(f.handle,"upsert",f.candidate(),"create")["record_id"],rid)
        result=f.memory.search(f.handle,"rule",5)
        self.assertEqual(result["records"][0]["metadata"]["tags"],["python"])
        read=f.memory.read(f.handle,rid)
        candidate=f.candidate(body="changed"); candidate["record_id"]=rid
        candidate["deduplication"]["decision"]="replace"
        candidate["expected_fingerprint"]=read["record"]["fingerprint"]
        updated=f.memory.write(f.handle,"upsert",candidate,"update")
        self.assertEqual(updated["record_id"],rid)
        invalid=dict(candidate,expected_fingerprint=updated["fingerprint"])
        f.memory.write(f.handle,"invalidate",invalid,"invalidate")
        self.assertEqual(f.memory.read(f.handle,rid)["record"]["status"],"invalidated")
    def test_bad_sources_missing_admission_stale_version_and_unreviewed_merge_rejected(self):
        f=self.flow
        for key in ("sources","admission_basis","deduplication"):
            candidate=f.candidate(); candidate.pop(key)
            with self.assertRaises(Exception): f.memory.write(f.handle,"upsert",candidate,"bad-"+key)
        saved=f.memory.write(f.handle,"upsert",f.candidate(),"create")
        with self.assertRaises(Exception): f.memory.write(f.handle,"upsert",f.candidate(),"duplicate")
        candidate=f.candidate();candidate.update(record_id=saved["record_id"],expected_fingerprint="stale")
        with self.assertRaises(Exception): f.memory.write(f.handle,"upsert",candidate,"stale")

    def test_tags_and_stable_identity_are_persisted_as_server_metadata(self):
        f=self.flow
        saved=f.memory.write(f.handle,"upsert",f.candidate(),"tagged")
        row=f.api.rows[saved["record_id"]]
        self.assertEqual(row["metadata"]["tags"],["python"])
        self.assertEqual(row["metadata"]["experience_id"],"EXP-TEST-1")
