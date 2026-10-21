import json
import unittest

from tests.fixtures.platform_probe import ProbeClient, ProbeFailure, confirmed_ids, search_filter


class ProbeSafetyTests(unittest.TestCase):
    def test_filter_evidence_rejects_post_top_k_filtering(self):
        from tests.fixtures.platform_probe import validate_filter_evidence
        baseline = ["control-1", "experience-1", "experience-2"]
        valid = {"experience-1", "experience-2", "experience-3"}
        with self.assertRaises(ProbeFailure):
            validate_filter_evidence(baseline, ["experience-1", "experience-2"], valid, 3)
        validate_filter_evidence(baseline, ["experience-1", "experience-2", "experience-3"], valid, 3)

    def test_probe_rejects_real_scope_or_non_https_endpoint(self):
        for app, endpoint in (("production", "https://api.mem0.ai"),
                              ("experience-memory-contract-test", "http://api.mem0.ai"),
                              ("experience-memory-contract-test", "https://evil.invalid")):
            with self.subTest(app=app), self.assertRaises(ProbeFailure):
                ProbeClient("secret", app, "contract-0123456789abcdef-a", endpoint=endpoint)

    def test_pending_is_not_confirmed_and_ids_are_not_fabricated(self):
        for result in ({"status": "PENDING", "event_id": "event"}, {},
                       {"results": [{"event": "ADD"}]}, {"status": "FAILED"}):
            with self.subTest(result=result), self.assertRaises(ProbeFailure):
                confirmed_ids(result)
        self.assertEqual(confirmed_ids({"results": [{"id": "r-1", "event": "ADD"}]}), ("r-1",))

    def test_search_filters_exclude_control_before_top_k(self):
        self.assertEqual(search_filter("experience-memory-contract-test", "contract-0123456789abcdef-a"),
                         {"AND": [{"app_id": "experience-memory-contract-test"},
                                  {"agent_id": "contract-0123456789abcdef-a"},
                                  {"metadata": {"record_kind": "project_experience"}}]})

    def test_running_event_is_polled_until_server_confirms_completion(self):
        class EventFixture(ProbeClient):
            def __init__(self):
                super().__init__("secret", "experience-memory-contract-test", "contract-0123456789abcdef-a")
                self.events = iter(({"status": "RUNNING"}, {"status": "SUCCEEDED"}))
                self.gets = 0

            def request(self, method, path, payload=None, **kwargs):
                if method == "POST":
                    return {"status": "PENDING", "event_id": "e-1"}
                self.gets += 1
                return next(self.events)

        fixture = EventFixture()
        self.assertEqual(fixture.add("synthetic extraction", "synthetic_extraction", infer=True), ())
        self.assertEqual(fixture.gets, 2)
