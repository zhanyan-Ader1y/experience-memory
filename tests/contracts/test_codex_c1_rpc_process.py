"""Real, isolated Python children only; no Codex, native host, or network."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest

from scripts.codex_c1_process import OwnedProcess, OwnedProcessError
from tests.contracts.test_codex_c1_executor import pid_exited
from unittest.mock import patch


ECHO = """
import json, sys
for line in sys.stdin.buffer:
    request = json.loads(line)
    reply = {"id": request["id"], "result": {"ok": True}}
    sys.stdout.buffer.write(json.dumps(reply).encode() + b"\\n")
    sys.stdout.buffer.flush()
"""


@unittest.skipUnless(sys.platform == "win32", "Windows Job-owned duplex integration")
class RpcProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def launch(self, code, *, seconds=8, direct_interpreter=False, **kwargs):
        # Windows venv python.exe redirects to another Python process and keeps
        # inherited std handles open. Direct root closure fixtures must bypass
        # that redirector, not pretend sys.stdin.close() is kernel pipe EOF.
        executable = sys._base_executable if direct_interpreter else sys.executable
        return OwnedProcess([executable, "-I", "-c", code], cwd=self.directory,
                            env=dict(os.environ), deadline=time.monotonic() + seconds,
                            **kwargs)

    def assert_pids_exited(self, pids):
        # Job active==0 can briefly precede a descendant process handle becoming
        # signaled. Observe real process completion, within a separate test bound.
        until = time.monotonic() + 1
        while not all(pid_exited(pid) for pid in pids) and time.monotonic() < until:
            time.sleep(0.005)
        self.assertTrue(all(pid_exited(pid) for pid in pids))

    def assert_reaped(self, process, cleanup):
        self.assertEqual(cleanup, {"root_exited": True, "descendants_exited": True,
                                   "reason": "job_empty"})
        self.assertTrue(process.shutdown_state()["io_threads_stopped"])
        self.assertEqual(process.stop(), cleanup)

    def test_job_owned_two_round_trips_actual_eof_and_natural_exit(self):
        process = self.launch(ECHO, stdin_mode="pipe")
        lines = [b'{"id":1,"method":"initialize"}\n',
                 b'{"id":2,"method":"synthetic"}\n']
        try:
            self.assertTrue(process.is_job_member())
            for number, raw in enumerate(lines, 1):
                process.send_line(raw)
                self.assertEqual(json.loads(process.read_line()),
                                 {"id": number, "result": {"ok": True}})
            process.close_input()
            process.close_input()
            self.assertIsNone(process.read_line())
            self.assertEqual(process.wait_for_exit(), 0)
            summary = process.stream_summary()
            self.assertEqual(summary["stdin"], {"bytes": 61, "sha256": hashlib.sha256(b"".join(lines)).hexdigest()})
            self.assertGreater(summary["stdout"]["bytes"], 0)
            self.assertEqual(summary["stderr"]["bytes"], 0)
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)
        state = process.shutdown_state()
        self.assertTrue(state["stdin_eof"] and state["stdout_eof"] and state["stderr_eof"])
        self.assertEqual(state["natural_exit_code"], 0)
        self.assertFalse(state["forced_termination"])
        state["natural_exit_code"] = 77
        self.assertEqual(process.shutdown_state()["natural_exit_code"], 0)

    def test_default_devnull_keeps_events_and_rejects_input_operations(self):
        process = self.launch("import sys; print(sys.stdin.buffer.read().decode() or 'empty')")
        try:
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_STDIN_UNAVAILABLE"):
                process.send_line(b'{}\n')
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_STDIN_UNAVAILABLE"):
                process.close_input()
            self.assertEqual(b"".join(process.events()).strip(), b"empty")
            self.assertEqual(process.exit_code, 0)
            self.assertIsNone(process.shutdown_state()["stdin_eof"])
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)

    def test_stdout_api_families_cannot_steal_each_others_lines(self):
        for first in ("events", "read_line"):
            with self.subTest(first=first):
                process = self.launch("print('one',flush=True); print('two',flush=True)", stdin_mode="pipe")
                try:
                    if first == "events":
                        iterator = process.events()
                        self.assertEqual(next(iterator).strip(), b"one")
                        with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_STDOUT_CONSUMER"):
                            process.read_line()
                        with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_STDOUT_CONSUMER"):
                            next(process.events())
                        self.assertEqual(next(iterator).strip(), b"two")
                    else:
                        self.assertEqual(process.read_line().strip(), b"one")
                        with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_STDOUT_CONSUMER"):
                            next(process.events())
                        self.assertEqual(process.read_line().strip(), b"two")
                finally:
                    cleanup = process.stop()
                self.assert_reaped(process, cleanup)

    def test_blocked_write_is_not_acknowledged_and_stop_reaps_parent_child(self):
        code = ("import os,subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)']); "
                "print(os.getpid(),child.pid,flush=True); time.sleep(60)")
        process = self.launch(code, seconds=3, stdin_mode="pipe")
        started = time.monotonic()
        try:
            pids = list(map(int, process.read_line().split()))
            self.assertEqual(len(pids), 2)
            self.assertTrue(all(not pid_exited(pid) for pid in pids))
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_DEADLINE"):
                process.send_line(b"x" * 262143 + b"\n")
            self.assertGreater(time.monotonic() - started, 0.5)
            self.assertIsNone(process.shutdown_state()["natural_exit_code"])
        finally:
            cleanup = process.stop()
        self.assertLess(time.monotonic() - started, 4)
        self.assert_reaped(process, cleanup)
        self.assert_pids_exited(pids)
        self.assertTrue(process.shutdown_state()["forced_termination"])
        self.assertIsNone(process.shutdown_state()["natural_exit_code"])

    def test_wait_for_exit_timeout_does_not_kill_or_invent_zero(self):
        process = self.launch("import time; print('ready',flush=True); time.sleep(60)",
                              seconds=2, stdin_mode="pipe")
        try:
            self.assertEqual(process.read_line().strip(), b"ready")
            process.close_input()
            self.assertIsNone(process.wait_for_exit())
            self.assertIsNone(process.exit_code)
            self.assertFalse(process.shutdown_state()["forced_termination"])
            self.assertIsNone(process.shutdown_state()["natural_exit_code"])
            self.assertIsNone(process.shutdown_state()["io_failure"],
                              "an unknown natural exit alone is not an I/O timeout")
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)
        self.assertIsNone(process.wait_for_exit())
        self.assertIsNone(process.shutdown_state()["natural_exit_code"])

    def test_oversized_input_rejection_does_not_allocate_an_input_sized_copy(self):
        # Allocate the caller's 32 MiB input before tracing: the measured bound is
        # rejection's extra memory, not memory legitimately owned by the caller.
        raw = b"x" * (32 * 1024 * 1024) + b"\n"
        process = self.launch(ECHO, stdin_mode="pipe")
        try:
            process.send_line(b'{"id":1}\n')
            self.assertEqual(json.loads(process.read_line())["id"], 1)
            try:
                tracemalloc.start()
                baseline, _ = tracemalloc.get_traced_memory()
                tracemalloc.reset_peak()
                with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_STDIN_LINE_LIMIT"):
                    process.send_line(raw)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            # A generous 4 MiB ceiling tolerates tracing/thread/test overhead but
            # rejects the original 32 MiB prefix copy. No exact-byte threshold.
            self.assertLess(peak - baseline, 4 * 1024 * 1024)
            self.assertEqual(process.stream_summary()["stdin"]["bytes"], 9)
            process.close_input()
            self.assertIsNone(process.read_line())
            self.assertEqual(process.wait_for_exit(), 0)
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)

    def assert_read_deadline_survives_natural_exit(self, *, api, stdin_mode, close_stdout=False):
        # Exit after this same process's operation cutoff, but inside its original
        # cleanup reserve. No monkeypatched clocks, sleeping mocks, or new budget.
        deadline = time.monotonic() + 3
        natural_exit_at = deadline - 0.4
        code = ("import os,time; print('ready',flush=True); "
                + ("os.close(1); " if close_stdout else "")
                + f"time.sleep(max(0, {natural_exit_at!r} - time.monotonic()))")
        process = OwnedProcess([sys._base_executable, "-I", "-c", code],
                               cwd=self.directory, env=dict(os.environ),
                               deadline=deadline, stdin_mode=stdin_mode)
        try:
            if stdin_mode == "pipe":
                process.close_input()  # Writer must already be gone at timeout.
            if api == "events":
                iterator = process.events()
                read = lambda: next(iterator)
            else:
                read = process.read_line
            self.assertEqual(read().strip(), b"ready")
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_DEADLINE"):
                read()
            failure_at_timeout = process.shutdown_state()["io_failure"]
            self.assertIsNone(process.wait_for_exit())  # Must not wait past cutoff.
            while time.monotonic() < deadline - 0.1:
                observed = process.shutdown_state()
                if (observed["natural_exit_code"] == 0 and observed["stdout_eof"]
                        and observed["stderr_eof"]):
                    break
                time.sleep(0.005)
            late_observation = process.shutdown_state()
        finally:
            cleanup = process.stop()
        self.assertLess(time.monotonic(), deadline)
        self.assert_reaped(process, cleanup)
        self.assertEqual(late_observation["natural_exit_code"], 0)
        state = process.shutdown_state()
        self.assertEqual(state["natural_exit_code"], 0)
        self.assertTrue(state["stdout_eof"] and state["stderr_eof"])
        self.assertEqual(state["stdin_eof"], True if stdin_mode == "pipe" else None)
        self.assertFalse(state["forced_termination"])
        # Both the immediate observation and otherwise-clean late shutdown must
        # preserve the I/O failure; actual later EOF/exit zero cannot erase it.
        self.assertEqual(failure_at_timeout, "C1_PROCESS_DEADLINE")
        self.assertEqual(state["io_failure"], "C1_PROCESS_DEADLINE")

    def test_devnull_read_deadline_remains_sticky_after_natural_exit_and_eof(self):
        self.assert_read_deadline_survives_natural_exit(api="read_line", stdin_mode="devnull")

    def test_closed_pipe_events_deadline_remains_sticky_after_natural_exit_and_eof(self):
        self.assert_read_deadline_survives_natural_exit(api="events", stdin_mode="pipe")

    def test_events_post_stdout_eof_deadline_remains_sticky_after_natural_exit(self):
        self.assert_read_deadline_survives_natural_exit(api="events", stdin_mode="pipe", close_stdout=True)

    def test_invalid_mode_is_rejected_before_suspended_child_can_run(self):
        marker = self.directory / "must-not-run"
        code = f"from pathlib import Path; Path({str(marker)!r}).touch()"
        with self.assertRaisesRegex(ValueError, "C1_STDIN_MODE_INVALID"):
            self.launch(code, stdin_mode="file")
        self.assertFalse(marker.exists())

    def test_invalid_frames_and_oversize_are_rejected_without_writing(self):
        process = self.launch(ECHO, stdin_mode="pipe")
        try:
            for raw in (b"", b"{}", b"{}\n{}\n", "{}\n", bytearray(b"{}\n")):
                with self.subTest(raw_type=type(raw).__name__):
                    with self.assertRaisesRegex(ValueError, "C1_PROCESS_STDIN_LINE_INVALID"):
                        process.send_line(raw)
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_STDIN_LINE_LIMIT"):
                process.send_line(b"x" * 262144 + b"\n")
            self.assertEqual(process.stream_summary()["stdin"]["bytes"], 0)
            process.send_line(b'{"id":1}\n')
            self.assertEqual(json.loads(process.read_line())["id"], 1)
            process.close_input()
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_STDIN_CLOSED"):
                process.send_line(b'{"id":2}\n')
            self.assertIsNone(process.read_line())
            self.assertEqual(process.wait_for_exit(), 0)
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)

    def test_stdin_cumulative_budget_is_independent_of_received_budget(self):
        process = self.launch(ECHO, stdin_mode="pipe")
        prefix, suffix = b'{"id":1,"pad":"', b'"}\n'
        raw = prefix + b"x" * (262144 - len(prefix) - len(suffix)) + suffix
        try:
            for _ in range(8):
                process.send_line(raw)
                self.assertEqual(json.loads(process.read_line())["id"], 1)
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_STDIN_TOTAL_LIMIT"):
                process.send_line(b'{"id":2}\n')
            self.assertEqual(process.stream_summary()["stdin"]["bytes"], 2097152)
            self.assertLess(process.stream_summary()["stdout"]["bytes"], 1024)
            process.close_input()
            self.assertIsNone(process.read_line())
            self.assertEqual(process.wait_for_exit(), 0)
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)

    def test_stderr_flood_is_summarized_without_starving_duplex(self):
        code = "import sys; sys.stderr.write('synthetic diagnostic\\n' * 100); sys.stderr.flush()\n" + ECHO
        process = self.launch(code, stdin_mode="pipe")
        try:
            process.send_line(b'{"id":1}\n')
            self.assertEqual(json.loads(process.read_line())["id"], 1)
            process.close_input()
            self.assertIsNone(process.read_line())
            self.assertEqual(process.wait_for_exit(), 0)
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)
        self.assertGreater(process.stream_summary()["stderr"]["bytes"], 0)
        self.assertNotIn("synthetic diagnostic", json.dumps(process.stream_summary()))

    def test_concurrent_senders_write_whole_frames_and_receive_actual_ack(self):
        process = self.launch(ECHO, stdin_mode="pipe")
        failures = []
        workers = []
        def send(number):
            try:
                process.send_line(json.dumps({"id": number}).encode() + b"\n")
            except BaseException as error:
                failures.append(error)
        try:
            for number in range(20):
                worker = threading.Thread(target=send, args=(number,))
                workers.append(worker)
                worker.start()
            for worker in workers:
                worker.join(3)
            self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(failures, [])
            self.assertEqual(sorted(json.loads(process.read_line())["id"] for _ in workers), list(range(20)))
            process.close_input()
            self.assertIsNone(process.read_line())
            self.assertEqual(process.wait_for_exit(), 0)
        finally:
            cleanup = process.stop()
            for worker in workers:
                worker.join(1)
        self.assert_reaped(process, cleanup)

    def test_same_read_api_rejects_simultaneous_consumers(self):
        process = self.launch(ECHO, stdin_mode="pipe")
        entered = threading.Event()
        received = []
        failures = []
        def read():
            entered.set()
            try:
                received.append(process.read_line())
            except BaseException as error:
                failures.append(error)
        worker = threading.Thread(target=read)
        try:
            worker.start()
            self.assertTrue(entered.wait(1))
            time.sleep(0.05)  # Child cannot reply before the later send.
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_STDOUT_CONSUMER"):
                process.read_line()
            process.send_line(b'{"id":1}\n')
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(json.loads(received[0])["id"], 1)
        finally:
            cleanup = process.stop()
            worker.join(1)
        self.assert_reaped(process, cleanup)

    def test_stop_wakes_saturated_write_queue_and_closer_without_deadlock(self):
        process = self.launch("import time; print('ready',flush=True); time.sleep(60)",
                              stdin_mode="pipe")
        workers, failures, successes = [], [], []
        def blocked_send():
            try:
                process.send_line(b"x" * 65535 + b"\n")
                successes.append(True)
            except OwnedProcessError as error:
                failures.append(error.code)
        def close():
            try:
                process.close_input()
            except OwnedProcessError as error:
                failures.append(error.code)
        try:
            self.assertEqual(process.read_line().strip(), b"ready")
            for _ in range(24):
                worker = threading.Thread(target=blocked_send)
                workers.append(worker)
                worker.start()
            time.sleep(0.1)
            closer = threading.Thread(target=close)
            workers.append(closer)
            closer.start()
            time.sleep(0.05)
            self.assertEqual(successes, [], "queue admission was incorrectly acknowledged")
        finally:
            started = time.monotonic()
            cleanup = process.stop()
            for worker in workers:
                worker.join(1)
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(successes, [])
        self.assertGreaterEqual(len(failures), 24)
        self.assertTrue(all(code in ("C1_PROCESS_STOPPED", "C1_PROCESS_STDIN_CLOSED",
                                    "C1_PROCESS_STDIN_FAILED") for code in failures))
        self.assert_reaped(process, cleanup)
        self.assertIsNone(process.shutdown_state()["natural_exit_code"])

    def test_close_during_blocked_write_uses_same_deadline_not_new_budget(self):
        process = self.launch("import time; print('ready',flush=True); time.sleep(60)",
                              seconds=2, stdin_mode="pipe")
        failures = []
        def blocked_send():
            try:
                process.send_line(b"x" * 262143 + b"\n")
            except OwnedProcessError as error:
                failures.append(error.code)
        worker = threading.Thread(target=blocked_send)
        started = time.monotonic()
        try:
            self.assertEqual(process.read_line().strip(), b"ready")
            worker.start()
            time.sleep(0.05)
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_DEADLINE"):
                process.close_input()
            self.assertIsNone(process.exit_code)
        finally:
            cleanup = process.stop()
            worker.join(1)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(failures, ["C1_PROCESS_DEADLINE"])
        self.assertFalse(worker.is_alive())
        self.assert_reaped(process, cleanup)

    def test_broken_stdin_is_static_sticky_failure_and_never_retried(self):
        code = "import os,time; os.close(0); print('ready',flush=True); time.sleep(60)"
        process = self.launch(code, stdin_mode="pipe", direct_interpreter=True)
        try:
            self.assertEqual(process.read_line().strip(), b"ready")
            for _ in range(2):
                with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_STDIN_FAILED"):
                    process.send_line(b'{"id":1}\n')
            self.assertEqual(process.shutdown_state()["io_failure"], "C1_PROCESS_STDIN_FAILED")
            self.assertIsNone(process.shutdown_state()["natural_exit_code"])
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)

    def test_stdout_eof_does_not_claim_root_exit_or_force_termination(self):
        code = "import os,time; os.close(1); time.sleep(60)"
        process = self.launch(code, seconds=2, stdin_mode="pipe", direct_interpreter=True)
        try:
            self.assertIsNone(process.read_line())
            state = process.shutdown_state()
            self.assertTrue(state["stdout_eof"])
            self.assertIsNone(state["natural_exit_code"])
            self.assertFalse(state["forced_termination"])
            self.assertFalse(state["io_threads_stopped"])
            self.assertIsNone(process.exit_code)
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)

    def test_nonzero_natural_exit_is_preserved_without_manufacturing_success(self):
        process = self.launch("import sys; sys.stdin.buffer.read(); sys.exit(7)", stdin_mode="pipe")
        try:
            process.close_input()
            self.assertIsNone(process.read_line())
            self.assertEqual(process.wait_for_exit(), 7)
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)
        self.assertEqual(process.exit_code, 7)
        self.assertEqual(process.shutdown_state()["natural_exit_code"], 7)
        self.assertFalse(process.shutdown_state()["forced_termination"])

    def test_partial_stdout_line_records_real_eof_but_not_success(self):
        process = self.launch("import sys; sys.stdout.buffer.write(b'synthetic-partial'); sys.stdout.flush()",
                              stdin_mode="pipe")
        try:
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_PARTIAL_LINE"):
                process.read_line()
            state = process.shutdown_state()
            self.assertTrue(state["stdout_eof"])
            self.assertEqual(state["io_failure"], "C1_PROCESS_PARTIAL_LINE")
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)
        self.assertNotIn("synthetic-partial", json.dumps(process.shutdown_state()))

    def test_reader_limit_failure_does_not_masquerade_as_observed_eof(self):
        code = "import sys,time; sys.stdout.buffer.write(b'x'*262145); sys.stdout.flush(); time.sleep(60)"
        process = self.launch(code, stdin_mode="pipe")
        try:
            with self.assertRaisesRegex(OwnedProcessError, "C1_PROCESS_LINE_LIMIT"):
                process.read_line()
            state = process.shutdown_state()
            self.assertFalse(state["stdout_eof"])
            self.assertEqual(state["io_failure"], "C1_PROCESS_LINE_LIMIT")
            self.assertIsNone(state["natural_exit_code"])
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)
        self.assertFalse(process.shutdown_state()["stdout_eof"])

    def test_natural_root_exit_and_forced_descendant_cleanup_are_separate_facts(self):
        code = ("import os,subprocess,sys; "
                "child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)']); "
                "print(os.getpid(),child.pid,flush=True)")
        process = self.launch(code, stdin_mode="pipe", direct_interpreter=True)
        try:
            pids = list(map(int, process.read_line().split()))
            self.assertEqual(process.wait_for_exit(), 0)
            self.assertFalse(pid_exited(pids[1]))
            self.assertEqual(process.shutdown_state()["natural_exit_code"], 0)
        finally:
            cleanup = process.stop()
        self.assert_reaped(process, cleanup)
        self.assert_pids_exited(pids)
        self.assertEqual(process.shutdown_state()["natural_exit_code"], 0)
        self.assertTrue(process.shutdown_state()["forced_termination"])

    def test_job_close_safety_net_is_forced_not_natural_exit(self):
        process = self.launch("import os,time; print(os.getpid(),flush=True); time.sleep(60)",
                              stdin_mode="pipe", direct_interpreter=True)
        try:
            pid = int(process.read_line())
        finally:
            # Real suspended/assigned/resumed child, real KILL_ON_JOB_CLOSE.
            # Inject only the explicit termination syscall failure.
            with patch.object(process._api, "TerminateJobObject", return_value=False):
                cleanup = process.stop()
        self.assert_pids_exited([pid])
        self.assertFalse(cleanup["descendants_exited"], "fallback is not proof of an empty Job")
        self.assertTrue(process.shutdown_state()["io_threads_stopped"])
        self.assertTrue(process.shutdown_state()["forced_termination"])
        self.assertIsNone(process.shutdown_state()["natural_exit_code"])

    def test_pipe_assignment_and_resume_failures_never_execute_user_code(self):
        marker = self.directory / "never-executed"
        code = f"from pathlib import Path; Path({str(marker)!r}).touch()"
        for method in ("_assign_job", "_resume_owned_process"):
            with self.subTest(method=method):
                with patch.object(OwnedProcess, method, side_effect=OSError("synthetic private detail")):
                    with self.assertRaises(OwnedProcessError) as caught:
                        self.launch(code, stdin_mode="pipe")
                self.assertFalse(marker.exists())
                self.assertEqual(str(caught.exception), "C1_PROCESS_START_FAILED")
                self.assertTrue(caught.exception.cleanup["root_exited"])
                self.assertTrue(caught.exception.cleanup["descendants_exited"])


if __name__ == "__main__":
    unittest.main()
