"""Offline lifecycle, returned-policy, and fail-closed ownership tests."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import codex_c1_bundle as bundle_api
from scripts import prepare_codex_c1_review as exporter
from scripts.codex_c1_appserver_rpc import AppServerPeer, RpcMessage, RpcProtocolError, classify_message


SAMPLES = Path(__file__).resolve().parents[1] / "fixtures/codex_c1_appserver_samples.json"


def message(value):
    return classify_message(json.dumps(value).encode("utf-8") + b"\n")


def notification(method, params):
    return message({"method": method, "params": params})


class AppServerSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.bundle = Path(cls.temp.name) / "synthetic-session-review"
        exporter.prepare(cls.bundle, "c1-session-test")
        cls.snapshot = bundle_api.load_snapshot(cls.bundle)
        cls.root = Path(cls.snapshot.manifest["projects"]["a"]["root"])

    def setUp(self):
        try:
            self.module = importlib.import_module("scripts.codex_c1_appserver_session")
        except ModuleNotFoundError as error:
            if error.name != "scripts.codex_c1_appserver_session":
                raise
            self.fail("Task3 session implementation is missing")
        self.samples = json.loads(SAMPLES.read_text(encoding="utf-8").replace(
            "__SYNTHETIC_ROOT__", self.root.as_posix()))
        self.data = self.samples["session"]
        self.valid = self.data["thread_start_response"]
        self.reservation = {"schema_version": 1, "project": "a", "attempt": 1,
                            "state": "reserved", "run_nonce": self.snapshot.run_nonce,
                            "manifest_sha256": self.snapshot.manifest_sha256}
        self.session = self.new_session()

    def new_session(self):
        return self.module.AppServerSession(self.snapshot, "a", model="synthetic-model",
                                            provider="synthetic-provider")

    def thread_sent(self, session=None):
        session = self.session if session is None else session
        session.bind_reservation(copy.deepcopy(self.reservation))
        return session.thread_start_params()

    def thread_ready(self, session=None):
        session = self.session if session is None else session
        self.thread_sent(session)
        session.accept_thread_response(copy.deepcopy(self.valid))
        session.observe(notification("thread/started", {"thread": copy.deepcopy(self.valid["thread"])}))
        return session

    def turn_sent(self, session=None):
        session = self.thread_ready(session)
        session.turn_start_params()
        return session

    def turn_ready(self, session=None):
        session = self.turn_sent(session)
        session.observe(message({"id": 37, "result": copy.deepcopy(self.data["turn_start_response"])}))
        session.observe(notification("turn/started", copy.deepcopy(self.data["turn_started"])))
        return session

    def assert_frozen(self, session, state="failed"):
        self.assertEqual(session.summary()["state"], state)
        for operation in (session.thread_start_params, session.turn_start_params):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(session.summary()["state"], state)

    def test_fixed_params_require_strict_reservation_and_are_one_shot(self):
        with self.assertRaises(ValueError):
            self.session.thread_start_params()
        self.assert_frozen(self.session)
        session = self.new_session()
        params = self.thread_sent(session)
        self.assertEqual(params, {
            "cwd": str(self.root), "model": "synthetic-model", "modelProvider": "synthetic-provider",
            "approvalPolicy": "on-request", "approvalsReviewer": "user", "sandbox": "read-only",
            "ephemeral": True, "allowProviderModelFallback": False, "experimentalRawEvents": False,
        })
        self.assertEqual(session.summary()["state"], "thread_start_sent")
        with self.assertRaises(ValueError):
            session.thread_start_params()
        self.assert_frozen(session)

    def test_reservation_checks_all_binding_fields_and_exact_types(self):
        variants = [(key, value) for key, value in (
            ("schema_version", True), ("attempt", True), ("attempt", 2), ("project", "b"),
            ("run_nonce", "c1-foreign"), ("manifest_sha256", "0" * 64), ("state", "verified"))]
        for key, value in variants:
            with self.subTest(field=key, value=value):
                session = self.new_session()
                reservation = dict(self.reservation, **{key: value})
                with self.assertRaises(ValueError):
                    session.bind_reservation(reservation)
                self.assert_frozen(session)
        for field in self.reservation:
            session = self.new_session()
            wrong = dict(self.reservation)
            del wrong[field]
            with self.assertRaises(ValueError):
                session.bind_reservation(wrong)
        session = self.new_session()
        with self.assertRaises(ValueError):
            session.bind_reservation(dict(self.reservation, invented=True))
        session = self.new_session()
        session.bind_reservation(dict(self.reservation))
        with self.assertRaises(ValueError):
            session.bind_reservation(dict(self.reservation))

    def test_reservation_dto_does_not_claim_preflight_or_persisted_authority(self):
        self.session.bind_reservation(self.reservation)
        summary = self.session.summary()
        self.assertEqual(summary["state"], "attempt_reserved")
        self.assertTrue(summary["reservation_bound"])
        self.assertNotIn("preflight_checked", summary)
        self.assertFalse((self.bundle / "attempts/a-reservation.json").exists())
        self.reservation["attempt"] = 2
        self.assertEqual(self.session.thread_start_params()["approvalPolicy"], "on-request")

    def test_invalid_constructor_inputs_have_static_errors(self):
        for snapshot, project, model, provider in (
            (None, "a", "synthetic-model", "synthetic-provider"),
            (self.snapshot, "c", "synthetic-model", "synthetic-provider"),
            (self.snapshot, "a", "", "synthetic-provider"),
            (self.snapshot, "a", "synthetic-model", None),
        ):
            with self.subTest(project=project, model=model):
                with self.assertRaisesRegex(ValueError, "^C1_APPSERVER_INPUT_INVALID$"):
                    self.module.AppServerSession(snapshot, project, model=model, provider=provider)

    def test_rejects_returned_policy_reviewer_root_sandbox_and_model_drift(self):
        for field, value in (
            ("approvalPolicy", "never"), ("approvalsReviewer", "auto_review"),
            ("cwd", str(self.bundle)), ("model", "other-model"), ("modelProvider", "other-provider"),
            ("sandbox", {"type": "workspaceWrite", "networkAccess": False}),
            ("sandbox", {"type": "readOnly", "networkAccess": True}),
            ("sandbox", {"type": "readOnly", "networkAccess": None}),
            ("sandbox", {"type": "readOnly", "networkAccess": 0}),
            ("sandbox", {"type": "readOnly", "writableRoots": [str(self.root)]}),
        ):
            with self.subTest(field=field, value=value):
                session = self.new_session()
                self.thread_sent(session)
                with self.assertRaises(ValueError):
                    session.accept_thread_response(dict(copy.deepcopy(self.valid), **{field: value}))
                self.assert_frozen(session)

    def test_missing_response_evidence_is_unknown_never_request_echo(self):
        for field in ("approvalPolicy", "approvalsReviewer", "cwd", "model", "modelProvider", "sandbox", "thread"):
            with self.subTest(field=field):
                session = self.new_session()
                self.thread_sent(session)
                wrong = copy.deepcopy(self.valid)
                del wrong[field]
                with self.assertRaises(ValueError):
                    session.accept_thread_response(wrong)
                self.assert_frozen(session, "unknown")
                self.assertFalse(session.summary()["thread_response_verified"])

    def test_independent_failure_dominates_missing_in_any_field_order(self):
        for missing, field, value in (
            ("approvalPolicy", "model", "foreign"), ("model", "approvalPolicy", "never"),
            ("thread", "approvalsReviewer", "auto_review"),
            ("cwd", "sandbox", {"networkAccess": True}),
        ):
            session = self.new_session()
            self.thread_sent(session)
            wrong = copy.deepcopy(self.valid)
            del wrong[missing]
            wrong[field] = value
            with self.assertRaises(ValueError):
                session.accept_thread_response(wrong)
            self.assert_frozen(session)

    def test_readonly_network_default_is_schema_false_not_null_or_zero(self):
        del self.valid["sandbox"]["networkAccess"]
        self.thread_ready()
        policy = self.session.summary()["policy"]
        self.assertIs(policy["network_access"], False)
        self.assertEqual(policy["network_access_source"], "schema_default")
        self.assertEqual(policy["approval_policy"], "on-request")
        self.assertEqual(policy["approvals_reviewer"], "user")
        self.assertEqual(policy["root_sha256"], hashlib.sha256(str(self.root).encode()).hexdigest())
        self.assertEqual(policy["model"], "synthetic-model")
        self.assertEqual(policy["model_provider"], "synthetic-provider")

    def test_final_w1_present_known_response_optionals_reject_malformed_values_before_turn(self):
        mutations = (
            ("disabledPluginIds", False), ("disabledPluginIds", None), ("disabledPluginIds", [1]),
            ("multiAgentMode", []), ("multiAgentMode", None), ("multiAgentMode", "future"),
            ("multiAgentMode", {"custom": None}), ("multiAgentMode", {"custom": "x", "extra": True}),
            ("serviceTier", 0), ("serviceTier", False),
            ("reasoningEffort", {}), ("reasoningEffort", ""), ("reasoningEffort", False),
            ("instructionSources", 42), ("instructionSources", None), ("instructionSources", [False]),
            ("runtimeWorkspaceRoots", [False]), ("runtimeWorkspaceRoots", None), ("runtimeWorkspaceRoots", "root"),
            ("activePermissionProfile", False), ("activePermissionProfile", {"id": 1}),
            ("activePermissionProfile", {"id": "synthetic", "extends": []}),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                session = self.new_session()
                self.thread_sent(session)
                response = dict(copy.deepcopy(self.valid), **{field: value})
                with self.assertRaisesRegex(ValueError, "^C1_APPSERVER_SCHEMA_INVALID$"):
                    session.accept_thread_response(response)
                self.assertFalse(session.summary()["thread_response_verified"])
                self.assertFalse(session.summary()["turn_start_intent"])
                self.assert_frozen(session)

    def test_final_w1_response_optional_contradictions_dominate_missing_required_evidence(self):
        for field, value in (("disabledPluginIds", False), ("multiAgentMode", []), ("serviceTier", 0),
                             ("reasoningEffort", {}), ("instructionSources", 42),
                             ("runtimeWorkspaceRoots", [False])):
            for missing in ("approvalPolicy", "thread"):
                with self.subTest(field=field, missing=missing):
                    session = self.new_session()
                    self.thread_sent(session)
                    response = dict(copy.deepcopy(self.valid), **{field: value})
                    del response[missing]
                    with self.assertRaisesRegex(ValueError, "^C1_APPSERVER_SCHEMA_INVALID$"):
                        session.accept_thread_response(response)
                    self.assert_frozen(session)
                    self.assertIn("C1_APPSERVER_EVIDENCE_MISSING", session.summary()["failure_codes"])
                    self.assertIn("C1_APPSERVER_SCHEMA_INVALID", session.summary()["failure_codes"])
                    self.assertFalse(session.summary()["turn_start_intent"])

    def test_final_w1_response_optional_omissions_nulls_defaults_unions_and_auxiliary_extras(self):
        optional = ("disabledPluginIds", "multiAgentMode", "serviceTier", "reasoningEffort",
                    "instructionSources", "runtimeWorkspaceRoots", "activePermissionProfile")
        variants = [
            {},
            {"disabledPluginIds": [], "multiAgentMode": "explicitRequestOnly", "serviceTier": None,
             "reasoningEffort": None, "instructionSources": [], "runtimeWorkspaceRoots": [],
             "activePermissionProfile": None},
            {"disabledPluginIds": ["", "synthetic-plugin"], "multiAgentMode": "proactive", "serviceTier": "",
             "reasoningEffort": "synthetic-effort", "instructionSources": ["", "PRIVATE_INSTRUCTION_SOURCE"],
             "runtimeWorkspaceRoots": [str(self.root)],
             "activePermissionProfile": {"id": "", "extends": None, "schemaPermittedExtra": {}}},
            {"multiAgentMode": {"custom": ""}, "serviceTier": "synthetic-tier",
             "activePermissionProfile": {"id": "synthetic", "extends": ""}},
        ]
        for index, values in enumerate(variants):
            with self.subTest(index=index):
                response = copy.deepcopy(self.valid)
                for key in optional:
                    response.pop(key, None)
                response.update(values)
                # Settings-only fields are auxiliary here, not response schema fields.
                response.update(schemaPermittedExtra={"opaque": True}, effort=42, personality=[], summary=False)
                if index == 0:
                    del response["sandbox"]["networkAccess"]
                session = self.new_session()
                self.thread_sent(session)
                session.accept_thread_response(response)
                session.observe(notification("thread/started", {"thread": response["thread"]}))
                session.turn_start_params()
                session.observe(message({"id": 37, "result": self.data["turn_start_response"]}))
                session.observe(notification("turn/started", self.data["turn_started"]))
                session.observe(notification("turn/completed", self.data["turn_completed"]))
                self.assertEqual(session.summary()["state"], "protocol_complete")
                self.assertEqual(session.summary()["failure_codes"], [])
                self.assertEqual(session.summary()["policy"]["network_access_source"],
                                 "schema_default" if index == 0 else "explicit")
                self.assertNotIn("PRIVATE_INSTRUCTION_SOURCE", json.dumps(session.summary()))

    def test_final_w1_missing_response_profile_id_is_unknown_but_malformed_optional_is_failed(self):
        for malformed in (False, True):
            session = self.new_session()
            self.thread_sent(session)
            response = dict(copy.deepcopy(self.valid), activePermissionProfile={"extends": None})
            if malformed:
                response["reasoningEffort"] = {}
            with self.assertRaises(ValueError):
                session.accept_thread_response(response)
            self.assert_frozen(session, "failed" if malformed else "unknown")
            self.assertFalse(session.summary()["turn_start_intent"])

    def test_final_w2_exact_thread_notice_is_idempotent_while_collecting_and_after_completion(self):
        session = self.turn_ready()
        notice = notification("thread/started", {"thread": copy.deepcopy(self.valid["thread"])})
        before = session.summary()
        session.observe(notice)
        self.assertEqual(session.summary(), before)
        session.observe(notification("turn/completed", self.data["turn_completed"]))
        before = session.summary()
        session.observe(notice)
        self.assertEqual(session.summary(), before)
        self.assertEqual(before["state"], "protocol_complete")

    def test_new_thread_must_be_idle_empty_ephemeral_and_bound(self):
        for field, value in (
            ("status", {"type": "active", "activeFlags": []}), ("status", {"type": "systemError"}),
            ("status", {"type": "future"}), ("turns", [{"id": "old"}]), ("id", ""),
            ("ephemeral", False), ("cwd", str(self.bundle)), ("modelProvider", "foreign"),
            ("model", "foreign"), ("parentThreadId", "parent"), ("forkedFromId", "old"),
        ):
            session = self.new_session()
            self.thread_sent(session)
            wrong = copy.deepcopy(self.valid)
            wrong["thread"][field] = value
            with self.assertRaises(ValueError):
                session.accept_thread_response(wrong)
            self.assert_frozen(session)

    def test_thread_notification_response_interleaving_and_exact_duplicates(self):
        for notification_first in (True, False):
            session = self.new_session()
            self.thread_sent(session)
            started = notification("thread/started", {"thread": copy.deepcopy(self.valid["thread"])})
            if notification_first:
                session.observe(started)
                self.assertFalse(session.summary()["thread_response_verified"])
                self.assertEqual(session.summary()["state"], "thread_start_sent")
            session.accept_thread_response(copy.deepcopy(self.valid))
            session.observe(started)
            session.observe(started)
            self.assertEqual(session.summary()["state"], "thread_verified")
            self.assertTrue(session.summary()["thread_started_observed"])
            changed = copy.deepcopy(self.valid["thread"])
            changed["updatedAt"] += 1
            with self.assertRaises(ValueError):
                session.observe(notification("thread/started", {"thread": changed}))
            self.assert_frozen(session)

    def test_conflicting_pending_thread_and_foreign_notifications_freeze(self):
        for method in ("thread/started", "thread/settings/updated", "thread/status/changed"):
            session = self.thread_ready(self.new_session())
            params = {"thread": dict(copy.deepcopy(self.valid["thread"]), id="foreign")} if method == "thread/started" else (
                dict(copy.deepcopy(self.data["thread_settings_updated"]), threadId="foreign")
                if method == "thread/settings/updated" else {"threadId": "foreign", "status": {"type": "idle"}})
            with self.assertRaises(ValueError):
                session.observe(notification(method, params))
            self.assert_frozen(session)
        session = self.new_session()
        self.thread_sent(session)
        session.observe(notification("thread/started", {"thread": self.valid["thread"]}))
        wrong = copy.deepcopy(self.valid)
        wrong["thread"]["id"] = "foreign"
        with self.assertRaises(ValueError):
            session.accept_thread_response(wrong)
        self.assert_frozen(session)

    def test_turn_params_fixed_prompt_and_no_second_turn_or_steering(self):
        self.thread_ready()
        params = self.session.turn_start_params()
        self.assertEqual(params, {
            "threadId": "synthetic-thread",
            "input": [{"type": "text", "text": self.snapshot.manifest["projects"]["a"]["prompt"]}],
            "approvalPolicy": "on-request", "approvalsReviewer": "user",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        })
        self.assertEqual(self.session.summary()["state"], "turn_sent")
        with self.assertRaises(ValueError):
            self.session.turn_start_params()
        self.assert_frozen(self.session)

    def test_turn_requires_unique_matching_response_and_started_in_either_order(self):
        for started_first in (True, False):
            session = self.turn_sent(self.new_session())
            response = message({"id": 999, "result": self.data["turn_start_response"]})
            started = notification("turn/started", self.data["turn_started"])
            first, second = (started, response) if started_first else (response, started)
            session.observe(first)
            self.assertFalse(session.summary()["new_turn_verified"])
            self.assertEqual(session.summary()["state"], "turn_sent")
            session.observe(second)
            session.observe(started)
            self.assertEqual(session.summary()["state"], "collecting")
            self.assertTrue(session.summary()["new_turn_verified"])
            self.assertEqual(session.summary()["turn_id"], "synthetic-turn")
            session.observe(notification("turn/completed", self.data["turn_completed"]))
            self.assertEqual(session.summary()["state"], "protocol_complete")
            self.assertEqual(session.summary()["turn_status"], "completed")
            self.assertNotIn("started", session.summary())

    def test_turn_response_mismatch_extra_turn_and_prior_active_are_rejected(self):
        for stage in ("before_turn", "response", "notification", "prior_active"):
            session = self.thread_ready(self.new_session())
            if stage == "before_turn":
                event = notification("turn/started", self.data["turn_started"])
            elif stage == "prior_active":
                event = notification("thread/status/changed", {"threadId": "synthetic-thread", "status": {"type": "active", "activeFlags": []}})
            else:
                session.turn_start_params()
                session.observe(message({"id": 18, "result": self.data["turn_start_response"]}))
                wrong = copy.deepcopy(self.data["turn_started"])
                wrong["turn"]["id"] = "extra-turn"
                event = notification("turn/started", wrong) if stage == "notification" else message({"id": 19, "result": {"turn": wrong["turn"]}})
            with self.assertRaises(ValueError):
                session.observe(event)
            self.assert_frozen(session)

    def test_turn_schema_has_no_started_flag_and_rpc_ids_are_not_session_inventions(self):
        self.turn_sent()
        response = copy.deepcopy(self.data["turn_start_response"])
        self.assertEqual(set(response), {"turn"})
        self.session.observe(message({"id": "peer-correlated-id", "result": response}))
        self.assertFalse(self.session.summary()["new_turn_verified"])
        self.session.observe(notification("turn/started", self.data["turn_started"]))
        self.assertTrue(self.session.summary()["new_turn_verified"])
        self.assertNotIn("pending_client_id", self.session.summary())

    def test_completion_before_response_is_bounded_and_never_premature_success(self):
        self.turn_sent()
        self.session.observe(notification("turn/started", self.data["turn_started"]))
        self.session.observe(notification("turn/completed", self.data["turn_completed"]))
        self.assertEqual(self.session.summary()["state"], "turn_sent")
        self.session.observe(message({"id": 85, "result": self.data["turn_start_response"]}))
        self.assertEqual(self.session.summary()["state"], "protocol_complete")
        self.session.observe(notification("turn/completed", self.data["turn_completed"]))
        self.assertEqual(self.session.summary()["state"], "protocol_complete")

    def test_malformed_and_failed_turns_freeze_without_model_body(self):
        for turn in ({"id": "synthetic-turn", "status": "inProgress"},
                     dict(self.data["turn_start_response"]["turn"], status="future"),
                     dict(self.data["turn_start_response"]["turn"], error={"message": "SECRET"}),
                     dict(self.data["turn_start_response"]["turn"], items=[{"id": "old"}])):
            session = self.turn_sent(self.new_session())
            with self.assertRaises(ValueError) as caught:
                session.observe(message({"id": 8, "result": {"turn": turn}}))
            self.assertNotIn("SECRET", str(caught.exception))
            self.assertIn(session.summary()["state"], ("failed", "unknown"))
        for status in ("failed", "interrupted"):
            session = self.turn_ready(self.new_session())
            completed = copy.deepcopy(self.data["turn_completed"])
            completed["turn"]["status"] = status
            with self.assertRaises(ValueError):
                session.observe(notification("turn/completed", completed))
            self.assert_frozen(session)

    def test_settings_validate_full_returned_structure_and_never_merge_missing(self):
        self.thread_ready()
        self.session.observe(notification("thread/settings/updated", self.data["thread_settings_updated"]))
        self.assertEqual(self.session.summary()["settings_updates_verified"], 1)
        for field, value in (("approvalPolicy", "never"), ("approvalsReviewer", "auto_review"),
                             ("model", "other"), ("modelProvider", "other"),
                             ("cwd", str(self.bundle)), ("sandboxPolicy", {"type": "readOnly", "networkAccess": 0})):
            session = self.thread_ready(self.new_session())
            wrong = copy.deepcopy(self.data["thread_settings_updated"])
            wrong["threadSettings"][field] = value
            with self.assertRaises(ValueError):
                session.observe(notification("thread/settings/updated", wrong))
            self.assert_frozen(session)
        for missing in ("sandboxPolicy", "collaborationMode", "approvalsReviewer"):
            session = self.thread_ready(self.new_session())
            wrong = copy.deepcopy(self.data["thread_settings_updated"])
            del wrong["threadSettings"][missing]
            with self.assertRaises(ValueError):
                session.observe(notification("thread/settings/updated", wrong))
            self.assert_frozen(session, "unknown")

    def test_empty_settings_update_response_cannot_attest_thread_or_turn(self):
        self.thread_sent()
        with self.assertRaises(ValueError):
            self.session.accept_thread_response({})
        self.assert_frozen(self.session, "unknown")
        session = self.turn_sent(self.new_session())
        with self.assertRaises(ValueError):
            session.observe(message({"id": 4, "result": {}}))
        self.assert_frozen(session, "unknown")

    def test_notification_params_and_identity_are_checked_not_just_names(self):
        valid_events = {
            "thread/status/changed": {"threadId": "synthetic-thread", "status": {"type": "idle"}},
            "item/agentMessage/delta": {"threadId": "synthetic-thread", "turnId": "synthetic-turn", "itemId": "agent", "delta": "SECRET"},
            "item/reasoning/summaryTextDelta": {"threadId": "synthetic-thread", "turnId": "synthetic-turn", "itemId": "reason", "delta": "SECRET", "summaryIndex": 0},
            "item/reasoning/summaryPartAdded": {"threadId": "synthetic-thread", "turnId": "synthetic-turn", "itemId": "reason", "summaryIndex": 0},
            "item/reasoning/textDelta": {"threadId": "synthetic-thread", "turnId": "synthetic-turn", "itemId": "reason", "delta": "SECRET", "contentIndex": 0},
            "item/started": {"threadId": "synthetic-thread", "turnId": "synthetic-turn", "startedAtMs": 1, "item": {"id": "agent", "type": "agentMessage", "text": "SECRET"}},
            "item/completed": {"threadId": "synthetic-thread", "turnId": "synthetic-turn", "completedAtMs": 2, "item": {"id": "agent", "type": "agentMessage", "text": "SECRET"}},
            "thread/tokenUsage/updated": {"threadId": "synthetic-thread", "turnId": "synthetic-turn", "tokenUsage": {"last": {"cachedInputTokens": 0, "inputTokens": 1, "outputTokens": 1, "reasoningOutputTokens": 0, "totalTokens": 2}, "total": {"cachedInputTokens": 0, "inputTokens": 1, "outputTokens": 1, "reasoningOutputTokens": 0, "totalTokens": 2}, "modelContextWindow": None}},
        }
        for method, params in valid_events.items():
            with self.subTest(method=method):
                session = self.turn_ready(self.new_session())
                session.observe(notification(method, params))
                self.assertEqual(session.summary()["state"], "collecting")
                self.assertNotIn("SECRET", json.dumps(session.summary()))
                for wrong in (None, {}, dict(params, threadId="foreign")):
                    other = self.turn_ready(self.new_session())
                    with self.assertRaises(ValueError):
                        other.observe(notification(method, wrong))
                    self.assertIn(other.summary()["state"], ("failed", "unknown"))
        for method, field, value in (("item/reasoning/textDelta", "contentIndex", True),
                                      ("item/agentMessage/delta", "delta", 42),
                                      ("item/started", "startedAtMs", True),
                                      ("thread/status/changed", "status", {"type": "future"})):
            session = self.turn_ready(self.new_session())
            with self.assertRaises(ValueError):
                session.observe(notification(method, dict(valid_events[method], **{field: value})))
            self.assert_frozen(session)

    def test_unknown_startup_interaction_stops_and_success_cannot_reopen(self):
        self.thread_sent()
        request = message({"id": "attestation", "method": "account/attestation/request", "params": {}})
        self.assertEqual(self.session.on_server_request(request), "stop")
        self.assert_frozen(self.session, "unknown")
        with self.assertRaises(ValueError):
            self.session.accept_thread_response(self.valid)
        self.assert_frozen(self.session, "unknown")
        session = self.turn_ready(self.new_session())
        self.assertEqual(session.on_server_request(request), "stop")
        with self.assertRaises(ValueError):
            session.observe(notification("turn/completed", self.data["turn_completed"]))
        self.assert_frozen(session, "unknown")

    def test_known_approvals_validate_then_freeze_and_return_exact_cancel_action(self):
        for example in self.samples["examples"]:
            session = self.turn_ready(self.new_session())
            request = message(example["request"])
            action = session.on_server_request(request)
            self.assertEqual(action, "deny_permissions" if "permissions" in request.method else "cancel")
            summary = session.summary()
            self.assertEqual(summary["state"], "failed")
            self.assertTrue(summary["approval_observed"])
            self.assertEqual(summary["approval_action"], action)
            self.assertNotIn("approval_cancel_sent", summary)
            self.assertEqual(session.interrupt_params(), {"threadId": "synthetic-thread", "turnId": "synthetic-turn"})
            self.assertIsNone(session.interrupt_params())
            with self.assertRaises(ValueError):
                session.observe(notification("turn/completed", self.data["turn_completed"]))
            self.assert_frozen(session)

    def test_bad_optional_and_nested_approval_schema_never_get_cancel_action(self):
        mutations = [(0, "commandActions", [{"type": "read", "command": "x"}]),
                     (0, "additionalPermissions", {"network": {"enabled": "false"}}),
                     (1, "grantRoot", 42),
                     (2, "permissions", {"fileSystem": {"entries": [{"access": "write", "path": {"type": "path", "path": 42}}]}}),
                     (2, "permissions", {"unknownProfile": {}})]
        for index, field, value in mutations:
            session = self.turn_ready(self.new_session())
            wrong = copy.deepcopy(self.samples["examples"][index]["request"])
            wrong["params"][field] = value
            with self.assertRaisesRegex(ValueError, "^C1_APPSERVER_APPROVAL_SCHEMA_INVALID$"):
                session.on_server_request(message(wrong))
            self.assert_frozen(session)
            self.assertIsNone(session.summary()["approval_action"])

    def test_approval_wrong_direction_id_foreign_owner_or_replay_stops(self):
        base = self.samples["examples"][0]["request"]
        for field, value in (("threadId", "foreign"), ("turnId", "foreign"), ("itemId", "")):
            session = self.turn_ready(self.new_session())
            wrong = copy.deepcopy(base)
            wrong["params"][field] = value
            with self.assertRaises(ValueError):
                session.on_server_request(message(wrong))
            self.assert_frozen(session)
        for request in (notification(base["method"], base["params"]),
                        RpcMessage("server_request", base["method"], True, dict(base, id=True)),
                        RpcMessage("server_request", base["method"], 1, dict(base, id=2))):
            session = self.turn_ready(self.new_session())
            with self.assertRaises(ValueError):
                session.on_server_request(request)
            self.assert_frozen(session)
        session = self.turn_ready(self.new_session())
        request = message(base)
        session.on_server_request(request)
        with self.assertRaises(ValueError):
            session.on_server_request(request)
        session = self.new_session()
        self.thread_sent(session)
        with self.assertRaises(ValueError):
            session.on_server_request(message(base))
        self.assertIsNone(session.interrupt_params())

    def test_known_cancel_preserves_peer_pending_client_id_then_driver_closes(self):
        class Process:
            def __init__(inner):
                inner.incoming = []
                inner.writes = []
            def send_line(inner, raw):
                inner.writes.append(json.loads(raw))
            def read_line(inner):
                return inner.incoming.pop(0)
        process = Process()
        peer = AppServerPeer(process)
        self.turn_ready()
        pending = peer.send_request("turn/start", {})
        request = copy.deepcopy(self.samples["examples"][0]["request"])
        request["id"] = pending
        process.incoming.append(json.dumps(request).encode() + b"\n")
        incoming = peer.next_message()
        self.assertEqual(self.session.on_server_request(incoming), "cancel")
        peer.reply_cancel(incoming)
        self.assertEqual(peer.pending_client_id, pending)
        self.assertEqual(process.writes[-1], {"id": pending, "result": {"decision": "cancel"}})
        # A driver must close rather than send interrupt while the peer is pending.
        with self.assertRaises(RpcProtocolError) as caught:
            peer.send_request("turn/interrupt", self.session.interrupt_params())
        self.assertEqual(str(caught.exception), "C1_RPC_CLIENT_REQUEST_PENDING")

    def test_public_approval_predicate_reuses_peer_validation_safely(self):
        rpc = importlib.import_module("scripts.codex_c1_appserver_rpc")
        predicate = getattr(rpc, "is_valid_approval_request", None)
        self.assertTrue(callable(predicate), "Task3 needs the approved shared approval predicate")
        for invalid in (None, {}, notification("item/fileChange/requestApproval", {}),
                        RpcMessage("server_request", "item/fileChange/requestApproval", 1, None)):
            self.assertIs(predicate(invalid), False)
        for example in self.samples["examples"]:
            self.assertIs(predicate(message(example["request"])), True)

    def test_pending_turn_start_approval_cancels_without_inventing_verified_turn(self):
        class Process:
            def __init__(inner):
                inner.incoming = []
                inner.writes = []
            def send_line(inner, raw):
                inner.writes.append(json.loads(raw))
            def read_line(inner):
                return inner.incoming.pop(0)
        process = Process()
        peer = AppServerPeer(process)
        self.thread_ready()
        params = self.session.turn_start_params()
        pending = peer.send_request("turn/start", params)
        request = copy.deepcopy(self.samples["examples"][0]["request"])
        request["id"] = pending
        process.incoming.extend((json.dumps({"method": "turn/started", "params": self.data["turn_started"]}).encode() + b"\n",
                                 json.dumps(request).encode() + b"\n"))
        self.session.observe(peer.next_message())
        self.assertFalse(self.session.summary()["new_turn_verified"])
        received = peer.next_message()
        self.assertEqual(self.session.on_server_request(received), "cancel")
        peer.reply_cancel(received)
        self.assertEqual(peer.pending_client_id, pending)
        self.assertIsNone(self.session.interrupt_params())
        self.assertEqual(process.writes[-1], {"id": pending, "result": {"decision": "cancel"}})
        self.assert_frozen(self.session)

    def test_missing_active_flags_is_unknown_not_a_fabricated_observed_failure(self):
        self.turn_sent()
        with self.assertRaises(ValueError):
            self.session.observe(notification("thread/status/changed", {"threadId": "synthetic-thread", "status": {"type": "active"}}))
        self.assert_frozen(self.session, "unknown")

    def test_pinned_turn_items_view_rejects_invented_partial_variant(self):
        self.turn_sent()
        response = copy.deepcopy(self.data["turn_start_response"])
        response["turn"]["itemsView"] = "partial"
        with self.assertRaisesRegex(ValueError, "^C1_APPSERVER_SCHEMA_INVALID$"):
            self.session.observe(message({"id": 8, "result": response}))
        self.assert_frozen(self.session)

    def test_known_item_required_and_discarded_text_fields_are_validated(self):
        for item, expected in (({"id": "agent", "type": "agentMessage"}, "unknown"),
                               ({"id": "agent", "type": "agentMessage", "text": 42}, "failed"),
                               ({"id": "reason", "type": "reasoning", "summary": [42]}, "failed"),
                               ({"id": "mcp", "type": "mcpToolCall", "arguments": {}, "server": "s", "tool": "t", "status": "future"}, "failed")):
            session = self.turn_ready(self.new_session())
            with self.assertRaises(ValueError):
                session.observe(notification("item/started", {"threadId": "synthetic-thread", "turnId": "synthetic-turn", "startedAtMs": 1, "item": item}))
            self.assert_frozen(session, expected)

    def test_bad_interaction_envelope_is_static_failure_not_unknown_action(self):
        self.turn_ready()
        malformed = RpcMessage("server_request", "attestation/request", 1,
                               {"id": 1, "method": "attestation/request", "result": {}})
        with self.assertRaisesRegex(ValueError, "^C1_APPSERVER_APPROVAL_SCHEMA_INVALID$"):
            self.session.on_server_request(malformed)
        self.assert_frozen(self.session)

    def test_interrupt_only_after_both_ids_verified_only_on_exception_path(self):
        self.assertIsNone(self.session.interrupt_params())
        self.turn_sent()
        self.session.observe(notification("turn/started", self.data["turn_started"]))
        self.assertIsNone(self.session.interrupt_params())
        self.session.observe(message({"id": 12, "result": self.data["turn_start_response"]}))
        self.assertIsNone(self.session.interrupt_params())
        self.session.on_server_request(message({"id": "unknown", "method": "elicitation/create", "params": {}}))
        self.assertEqual(self.session.interrupt_params(), {"threadId": "synthetic-thread", "turnId": "synthetic-turn"})
        self.assertIsNone(self.session.interrupt_params())

    def test_unknown_freeze_can_promote_independent_failure_never_success(self):
        self.thread_sent()
        missing = copy.deepcopy(self.valid)
        del missing["approvalsReviewer"]
        with self.assertRaises(ValueError):
            self.session.accept_thread_response(missing)
        self.assertEqual(self.session.summary()["state"], "unknown")
        contradiction = dict(copy.deepcopy(self.valid), approvalPolicy="never")
        with self.assertRaises(ValueError):
            self.session.accept_thread_response(contradiction)
        self.assert_frozen(self.session)
        with self.assertRaises(ValueError):
            self.session.accept_thread_response(self.valid)
        self.assert_frozen(self.session)

    def test_summary_is_detached_body_free_precise_evidence_not_final_acceptance(self):
        self.turn_ready()
        summary = self.session.summary()
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["project"], "a")
        self.assertEqual(summary["run_nonce"], self.snapshot.run_nonce)
        self.assertEqual(summary["manifest_sha256"], self.snapshot.manifest_sha256)
        self.assertTrue(summary["thread_response_verified"])
        self.assertTrue(summary["turn_response_observed"])
        self.assertTrue(summary["turn_started_observed"])
        self.assertFalse(summary["turn_completed_observed"])
        self.assertEqual(summary["failure_codes"], [])
        summary["policy"]["approval_policy"] = "never"
        summary["failure_codes"].append("invented")
        actual = self.session.summary()
        self.assertEqual(actual["policy"]["approval_policy"], "on-request")
        self.assertEqual(actual["failure_codes"], [])
        serialized = json.dumps(actual)
        for raw in (str(self.root), self.snapshot.manifest["projects"]["a"]["prompt"], "session_handle", "synthetic-session"):
            self.assertNotIn(raw, serialized)
        self.assertNotIn("verified", actual["state"])

    def test_fix2_source_only_unknown_stays_unknown_in_both_orders_without_reopening(self):
        for response_first in (True, False):
            with self.subTest(response_first=response_first):
                session = self.new_session()
                self.thread_sent(session)
                if response_first:
                    session.accept_thread_response(copy.deepcopy(self.valid))
                else:
                    session.observe(notification("thread/started", {"thread": copy.deepcopy(self.valid["thread"])}))
                unknown = copy.deepcopy(self.valid)
                unknown["thread"]["source"] = "unknown"
                with self.assertRaises(ValueError):
                    if response_first:
                        session.observe(notification("thread/started", {"thread": unknown["thread"]}))
                    else:
                        session.accept_thread_response(unknown)
                self.assert_frozen(session, "unknown")
                self.assertIn("C1_APPSERVER_SOURCE_UNKNOWN", session.summary()["failure_codes"])
                self.assertNotIn("C1_APPSERVER_THREAD_MISMATCH", session.summary()["failure_codes"])
                with self.assertRaises(ValueError):
                    if response_first:
                        session.observe(notification("thread/started", {"thread": copy.deepcopy(self.valid["thread"])}))
                    else:
                        session.accept_thread_response(copy.deepcopy(self.valid))
                self.assert_frozen(session, "unknown")

    def test_fix2_unknown_source_never_hides_other_thread_fact_conflicts_in_both_orders(self):
        for response_first in (True, False):
            for field, value in (("sessionId", "synthetic-foreign-session"),
                                 ("projectId", "synthetic-foreign-project"),
                                 ("extra", {"synthetic_auxiliary_fact": True})):
                with self.subTest(response_first=response_first, field=field):
                    session = self.new_session()
                    self.thread_sent(session)
                    if response_first:
                        session.accept_thread_response(copy.deepcopy(self.valid))
                    else:
                        session.observe(notification("thread/started", {"thread": copy.deepcopy(self.valid["thread"])}))
                    conflict = copy.deepcopy(self.valid)
                    conflict["thread"]["source"] = "unknown"
                    conflict["thread"][field] = value
                    with self.assertRaises(ValueError):
                        if response_first:
                            session.observe(notification("thread/started", {"thread": conflict["thread"]}))
                        else:
                            session.accept_thread_response(conflict)
                    self.assert_frozen(session)
                    self.assertIn("C1_APPSERVER_THREAD_MISMATCH", session.summary()["failure_codes"])
                    self.assertIn("C1_APPSERVER_SOURCE_UNKNOWN", session.summary()["failure_codes"])
                    with self.assertRaises(ValueError):
                        if response_first:
                            session.observe(notification("thread/started", {"thread": copy.deepcopy(self.valid["thread"])}))
                        else:
                            session.accept_thread_response(copy.deepcopy(self.valid))
                    self.assert_frozen(session)

    def test_fix2_later_conflict_promotes_prior_source_unknown_without_reopening(self):
        for response_first in (True, False):
            with self.subTest(response_first=response_first):
                session = self.new_session()
                self.thread_sent(session)
                if response_first:
                    session.accept_thread_response(copy.deepcopy(self.valid))
                else:
                    session.observe(notification("thread/started", {"thread": copy.deepcopy(self.valid["thread"])}))
                unknown = copy.deepcopy(self.valid)
                unknown["thread"]["source"] = "unknown"
                with self.assertRaises(ValueError):
                    if response_first:
                        session.observe(notification("thread/started", {"thread": unknown["thread"]}))
                    else:
                        session.accept_thread_response(unknown)
                self.assertEqual(session.summary()["state"], "unknown")
                conflict = copy.deepcopy(unknown)
                conflict["thread"]["sessionId"] = "synthetic-foreign-session"
                with self.assertRaises(ValueError):
                    if response_first:
                        session.observe(notification("thread/started", {"thread": conflict["thread"]}))
                    else:
                        session.accept_thread_response(conflict)
                self.assert_frozen(session)
                self.assertIn("C1_APPSERVER_THREAD_MISMATCH", session.summary()["failure_codes"])
                self.assertFalse(session.summary()["turn_start_intent"])

    def test_fix2_exact_known_thread_duplicates_preserve_strict_other_fact_comparison(self):
        for response_first in (True, False):
            with self.subTest(response_first=response_first):
                session = self.new_session()
                self.thread_sent(session)
                started = notification("thread/started", {"thread": copy.deepcopy(self.valid["thread"])})
                if response_first:
                    session.accept_thread_response(copy.deepcopy(self.valid))
                session.observe(started)
                session.observe(started)
                if not response_first:
                    session.accept_thread_response(copy.deepcopy(self.valid))
                session.observe(started)
                self.assertEqual(session.summary()["state"], "thread_verified")
                conflict = copy.deepcopy(self.valid["thread"])
                conflict["sessionId"] = "synthetic-foreign-session"
                with self.assertRaises(ValueError):
                    session.observe(notification("thread/started", {"thread": conflict}))
                self.assert_frozen(session)

    def test_fix1_unknown_thread_source_never_authorizes_or_reopens_in_either_order(self):
        for order in ("unknown_response_first", "unknown_notification_first", "good_response_then_unknown_notification", "good_notification_then_unknown_response"):
            with self.subTest(order=order):
                session = self.new_session()
                self.thread_sent(session)
                unknown = copy.deepcopy(self.valid)
                unknown["thread"]["source"] = "unknown"
                if order == "good_response_then_unknown_notification":
                    session.accept_thread_response(copy.deepcopy(self.valid))
                elif order == "good_notification_then_unknown_response":
                    session.observe(notification("thread/started", {"thread": copy.deepcopy(self.valid["thread"])}))
                with self.assertRaises(ValueError):
                    if order in ("unknown_response_first", "good_notification_then_unknown_response"):
                        session.accept_thread_response(unknown)
                    else:
                        session.observe(notification("thread/started", {"thread": unknown["thread"]}))
                self.assert_frozen(session, "unknown")
                for later in (lambda: session.accept_thread_response(copy.deepcopy(self.valid)),
                              lambda: session.observe(notification("thread/started", {"thread": copy.deepcopy(self.valid["thread"])}))):
                    with self.assertRaises(ValueError):
                        later()
                    self.assert_frozen(session, "unknown")

    def test_fix1_supported_known_thread_sources_remain_valid_and_failure_dominates_unknown(self):
        for source in ("cli", "vscode", "exec", "appServer"):
            session = self.new_session()
            self.thread_sent(session)
            valid = copy.deepcopy(self.valid)
            valid["thread"]["source"] = source
            session.accept_thread_response(valid)
            session.observe(notification("thread/started", {"thread": valid["thread"]}))
            self.assertEqual(session.turn_start_params()["threadId"], "synthetic-thread")
        session = self.new_session()
        self.thread_sent(session)
        invalid = copy.deepcopy(self.valid)
        invalid["thread"]["source"] = "unknown"
        invalid["approvalPolicy"] = "never"
        with self.assertRaises(ValueError):
            session.accept_thread_response(invalid)
        self.assert_frozen(session)

    def test_fix1_known_optional_settings_fields_reject_present_invalid_values(self):
        mutations = (
            (("disabledPluginIds",), 1), (("disabledPluginIds",), None), (("disabledPluginIds",), [1]),
            (("effort",), 1), (("effort",), ""),
            (("personality",), "future"), (("personality",), False),
            (("serviceTier",), 1), (("summary",), "future"), (("summary",), False),
            (("multiAgentMode",), "future"), (("multiAgentMode",), None),
            (("multiAgentMode",), {"custom": None}), (("multiAgentMode",), {"custom": "x", "extra": True}),
            (("activePermissionProfile",), {"id": 1}),
            (("activePermissionProfile",), {"id": "synthetic", "extends": 1}),
            (("collaborationMode", "mode"), "future"),
            (("collaborationMode", "settings", "developer_instructions"), 1),
            (("collaborationMode", "settings", "reasoning_effort"), 1),
            (("collaborationMode", "settings", "reasoning_effort"), ""),
            (("collaborationMode", "settings", "model"), "foreign-model"),
        )
        for path, value in mutations:
            with self.subTest(path=path, value=value):
                session = self.thread_ready(self.new_session())
                params = copy.deepcopy(self.data["thread_settings_updated"])
                target = params["threadSettings"]
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = value
                with self.assertRaises(ValueError):
                    session.observe(notification("thread/settings/updated", params))
                self.assert_frozen(session)
                self.assertEqual(session.summary()["settings_updates_verified"], 0)

    def test_fix1_valid_optional_settings_nullability_defaults_enums_and_extras(self):
        variants = [
            {"disabledPluginIds": [], "effort": None, "personality": None,
             "serviceTier": None, "summary": None, "activePermissionProfile": None},
            {"disabledPluginIds": ["synthetic-plugin"], "effort": "synthetic-effort", "personality": "friendly",
             "serviceTier": "", "summary": "auto", "multiAgentMode": "explicitRequestOnly"},
            {"personality": "pragmatic", "summary": "concise", "multiAgentMode": "proactive"},
            {"personality": "none", "summary": "detailed", "multiAgentMode": {"custom": ""}},
            {"summary": "none", "activePermissionProfile": {"id": "", "extends": "", "schemaPermittedExtra": {}}},
            {"activePermissionProfile": {"id": "synthetic", "extends": None}},
        ]
        for index, updates in enumerate(variants):
            with self.subTest(index=index):
                session = self.thread_ready(self.new_session())
                params = copy.deepcopy(self.data["thread_settings_updated"])
                settings = params["threadSettings"]
                settings.update(updates)
                settings["schemaPermittedExtra"] = {"opaque": True}
                settings["collaborationMode"]["schemaPermittedExtra"] = 1
                nested = settings["collaborationMode"]["settings"]
                nested["schemaPermittedExtra"] = []
                nested["developer_instructions"] = None if index % 2 else "PRIVATE_SYNTHETIC_INSTRUCTIONS"
                nested["reasoning_effort"] = None if index % 2 else "synthetic-effort"
                if index == 0:
                    # Missing optional defaults remain valid; network source is
                    # separately recorded, not guessed from null/truthiness.
                    del settings["sandboxPolicy"]["networkAccess"]
                try:
                    session.observe(notification("thread/settings/updated", params))
                except ValueError as error:
                    self.fail("Schema-valid auxiliary settings must remain accepted: " + str(error))
                self.assertEqual(session.summary()["settings_updates_verified"], 1)
                self.assertEqual(session.summary()["state"], "thread_verified")
                self.assertNotIn("PRIVATE_SYNTHETIC_INSTRUCTIONS", json.dumps(session.summary()))
                self.assertEqual(session.summary()["settings_network_access_sources"],
                                 ["schema_default" if index == 0 else "explicit"])

    def test_fix1_missing_nested_required_settings_remains_unknown(self):
        for missing in ("profile_id", "collaboration_model"):
            with self.subTest(missing=missing):
                session = self.thread_ready(self.new_session())
                params = copy.deepcopy(self.data["thread_settings_updated"])
                settings = params["threadSettings"]
                if missing == "profile_id":
                    settings["activePermissionProfile"] = {"extends": None, "schemaPermittedExtra": True}
                else:
                    del settings["collaborationMode"]["settings"]["model"]
                with self.assertRaises(ValueError):
                    session.observe(notification("thread/settings/updated", params))
                self.assert_frozen(session, "unknown")
                self.assertEqual(session.summary()["settings_updates_verified"], 0)

    def test_fix1_optional_settings_failure_dominates_an_unrelated_required_absence(self):
        for missing in ("approvalPolicy", "sandboxPolicy", "collaborationMode"):
            with self.subTest(missing=missing):
                session = self.thread_ready(self.new_session())
                params = copy.deepcopy(self.data["thread_settings_updated"])
                settings = params["threadSettings"]
                del settings[missing]
                settings["disabledPluginIds"] = 1
                with self.assertRaises(ValueError):
                    session.observe(notification("thread/settings/updated", params))
                self.assert_frozen(session)
                self.assertEqual(session.summary()["settings_updates_verified"], 0)

    def test_fix1_first_completion_before_first_start_fails_with_response_in_either_order(self):
        for response_first in (True, False):
            with self.subTest(response_first=response_first):
                session = self.turn_sent(self.new_session())
                response = message({"id": 27, "result": self.data["turn_start_response"]})
                if response_first:
                    session.observe(response)
                with self.assertRaises(ValueError):
                    session.observe(notification("turn/completed", self.data["turn_completed"]))
                self.assert_frozen(session)
                self.assertFalse(session.summary()["turn_completed_observed"])
                self.assertFalse(session.summary()["turn_started_observed"])
                with self.assertRaises(ValueError):
                    session.observe(notification("turn/started", self.data["turn_started"]))
                if not response_first:
                    with self.assertRaises(ValueError):
                        session.observe(response)
                self.assert_frozen(session)
                self.assertFalse(session.summary()["new_turn_verified"])

    def test_fix1_started_then_completed_before_response_and_late_exact_start_duplicate_remain_valid(self):
        for response_position in ("before", "middle", "after"):
            with self.subTest(response_position=response_position):
                session = self.turn_sent(self.new_session())
                response = message({"id": 28, "result": self.data["turn_start_response"]})
                started = notification("turn/started", self.data["turn_started"])
                if response_position == "before":
                    session.observe(response)
                session.observe(started)
                if response_position == "middle":
                    session.observe(response)
                session.observe(notification("turn/completed", self.data["turn_completed"]))
                if response_position == "after":
                    self.assertEqual(session.summary()["state"], "turn_sent")
                    session.observe(response)
                session.observe(started)
                self.assertEqual(session.summary()["state"], "protocol_complete")
                self.assertTrue(session.summary()["new_turn_verified"])

    def test_fix1_prior_unknown_then_first_known_approval_promotes_failed_and_cancels(self):
        for response_seen in (True, False):
            for example in self.samples["examples"]:
                with self.subTest(response_seen=response_seen, method=example["request"]["method"]):
                    session = self.turn_ready(self.new_session()) if response_seen else self.turn_sent(self.new_session())
                    if not response_seen:
                        session.observe(notification("turn/started", self.data["turn_started"]))
                    with self.assertRaises(ValueError):
                        session.observe(notification("thread/status/changed", {"threadId": "synthetic-thread", "status": {"type": "active"}}))
                    self.assertEqual(session.summary()["state"], "unknown")
                    request = message(example["request"])
                    action = "deny_permissions" if "permissions" in request.method else "cancel"
                    try:
                        selected_action = session.on_server_request(request)
                    except ValueError as error:
                        self.fail("First schema-valid owned approval must return its shutdown action after unknown: " + str(error))
                    self.assertEqual(selected_action, action)
                    summary = session.summary()
                    self.assertEqual(summary["state"], "failed")
                    self.assertTrue(summary["approval_observed"])
                    self.assertEqual(summary["approval_action"], action)
                    self.assertIn("C1_APPSERVER_EVIDENCE_MISSING", summary["failure_codes"])
                    self.assertIn("C1_APPSERVER_APPROVAL_REQUIRED", summary["failure_codes"])
                    if response_seen:
                        self.assertEqual(session.interrupt_params(), {"threadId": "synthetic-thread", "turnId": "synthetic-turn"})
                    else:
                        self.assertFalse(summary["new_turn_verified"])
                        self.assertIsNone(session.interrupt_params())
                        with self.assertRaises(ValueError):
                            session.observe(message({"id": 29, "result": self.data["turn_start_response"]}))
                    with self.assertRaises(ValueError):
                        session.observe(notification("turn/completed", self.data["turn_completed"]))
                    with self.assertRaisesRegex(ValueError, "^C1_APPSERVER_APPROVAL_ID_REUSED$"):
                        session.on_server_request(request)
                    second = copy.deepcopy(example["request"])
                    second["id"] = "second-known-approval"
                    with self.assertRaises(ValueError):
                        session.on_server_request(message(second))
                    self.assert_frozen(session)

    def test_fix1_unknown_pending_turn_approval_uses_peer_cancel_without_clearing_client_id(self):
        class Process:
            def __init__(inner):
                inner.incoming = []
                inner.writes = []
            def send_line(inner, raw):
                inner.writes.append(json.loads(raw))
            def read_line(inner):
                return inner.incoming.pop(0)
        process = Process()
        peer = AppServerPeer(process)
        self.thread_ready()
        pending = peer.send_request("turn/start", self.session.turn_start_params())
        self.session.observe(notification("turn/started", self.data["turn_started"]))
        with self.assertRaises(ValueError):
            self.session.observe(notification("thread/status/changed", {"threadId": "synthetic-thread", "status": {"type": "active"}}))
        request = copy.deepcopy(self.samples["examples"][2]["request"])
        request["id"] = pending
        process.incoming.append(json.dumps(request).encode() + b"\n")
        received = peer.next_message()
        try:
            selected_action = self.session.on_server_request(received)
        except ValueError as error:
            self.fail("Pending-client shutdown cancellation must remain possible after unknown: " + str(error))
        self.assertEqual(selected_action, "deny_permissions")
        peer.reply_cancel(received)
        self.assertEqual(peer.pending_client_id, pending)
        self.assertEqual(process.writes[-1], {"id": pending, "result": {"permissions": {}, "scope": "turn"}})
        self.assertIsNone(self.session.interrupt_params())
        self.assert_frozen(self.session)

    def test_fix1_prior_unknown_does_not_relax_approval_schema_or_foreign_ownership(self):
        for field, value in (("threadId", "foreign"), ("turnId", "foreign"), ("itemId", ""), ("grantRoot", 1)):
            with self.subTest(field=field):
                session = self.turn_ready(self.new_session())
                with self.assertRaises(ValueError):
                    session.observe(notification("thread/status/changed", {"threadId": "synthetic-thread", "status": {"type": "active"}}))
                request = copy.deepcopy(self.samples["examples"][1]["request"])
                request["params"][field] = value
                with self.assertRaises(ValueError):
                    session.on_server_request(message(request))
                self.assertFalse(session.summary()["approval_observed"])
                self.assertIsNone(session.summary()["approval_action"])
                self.assert_frozen(session)

    def test_unknown_notification_and_remote_error_fail_closed(self):
        for event, expected in ((notification("unknown/notification", {}), "unknown"),
                                (message({"id": 77, "error": {"code": -1, "message": "SECRET"}}), "failed"),
                                (notification("error", {"error": {"message": "SECRET"}, "threadId": "synthetic-thread", "turnId": "synthetic-turn", "willRetry": False}), "failed")):
            session = self.turn_ready(self.new_session())
            with self.assertRaises(ValueError) as caught:
                session.observe(event)
            self.assertNotIn("SECRET", str(caught.exception))
            self.assert_frozen(session, expected)


class SharedApprovalValidationTests(unittest.TestCase):
    def test_shared_predicate_and_peer_reject_malformed_envelopes_and_nested_params(self):
        rpc = importlib.import_module("scripts.codex_c1_appserver_rpc")
        predicate = getattr(rpc, "is_valid_approval_request", None)
        self.assertTrue(callable(predicate), "approved shared approval predicate is missing")
        sample = json.loads(SAMPLES.read_text(encoding="utf-8"))["examples"][2]["request"]
        self.assertTrue(predicate(message(sample)))
        for invalid in (None, {}, RpcMessage("server_request", sample["method"], 2, None),
                        RpcMessage("server_request", sample["method"], True, dict(sample, id=True)),
                        RpcMessage("server_request", sample["method"], 2, dict(sample, result={})),
                        RpcMessage("server_request", sample["method"], 2, dict(sample, method="foreign"))):
            self.assertFalse(predicate(invalid))
        wrong = copy.deepcopy(sample)
        wrong["params"]["permissions"]["network"]["enabled"] = 0
        self.assertFalse(predicate(message(wrong)))
        class Process:
            def __init__(inner):
                inner.writes = []
            def read_line(inner):
                return json.dumps(wrong).encode() + b"\n"
            def send_line(inner, raw):
                inner.writes.append(raw)
        process = Process()
        peer = AppServerPeer(process)
        received = peer.next_message()
        with self.assertRaisesRegex(rpc.RpcProtocolError, "^C1_RPC_CANCEL_SCHEMA_INVALID$"):
            peer.reply_cancel(received)
        self.assertEqual(process.writes, [])


if __name__ == "__main__":
    unittest.main()
