import unittest
from tests.fixtures.workflow import Workflow

class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.f=Workflow();self.addCleanup(self.f.close)
    def test_read_is_not_use_and_retry_does_not_increment_u(self):
        f=self.f; row=f.memory.write(f.handle,"upsert",f.candidate(),"new")
        receipt=f.memory.read(f.handle,row["record_id"])["receipt_id"]
        self.assertEqual(f.memory.status(f.handle)["u_cursor"],0)
        first=f.retention.use(f.handle,receipt,"P1","used for implementation","use1")
        second=f.retention.use(f.handle,receipt,"P1","used for implementation","use1")
        self.assertEqual(first,second);self.assertEqual(first["entry"]["last_used_seq"],1)
        third=f.retention.use(f.handle,receipt,"P2","used for verification","use2")
        self.assertEqual(third["entry"]["last_used_seq"],2)
        self.assertEqual(len(f.memory.status(f.handle)["entries"]),1)
    def test_invalid_or_expired_or_cross_session_receipt_does_not_increment(self):
        f=self.f;row=f.memory.write(f.handle,"upsert",f.candidate(),"new")
        receipt=f.memory.read(f.handle,row["record_id"])["receipt_id"]
        for priority,reason in (("P0","reason"),("P1"," ")):
            with self.assertRaises(Exception): f.retention.use(f.handle,receipt,priority,reason,"bad")
        other=f.other_session("s2")
        with self.assertRaises(Exception): other.retention.use(other.handle,receipt,"P1","reason","cross")
        candidate=f.candidate();candidate["deduplication"]["decision"]="replace";candidate.update(record_id=row["record_id"],expected_fingerprint=row["fingerprint"])
        f.memory.write(f.handle,"invalidate",candidate,"invalid")
        with self.assertRaises(Exception): f.retention.use(f.handle,receipt,"P1","reason","use")
        self.assertEqual(f.memory.status(f.handle)["u_cursor"],0)
