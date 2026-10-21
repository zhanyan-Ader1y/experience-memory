"""Test-only HTTPS probe. Never imported by the plugin runtime."""

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid


class ProbeFailure(Exception):
    pass


def load_credentials():
    key = os.environ.get("MEM0_API_KEY")
    if not key and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as env:
                key = winreg.QueryValueEx(env, "MEM0_API_KEY")[0]
        except OSError:
            pass
    return key


def confirmed_ids(result):
    if not isinstance(result, dict) or result.get("status") in ("PENDING", "FAILED"):
        raise ProbeFailure("NOT_CONFIRMED")
    rows = result.get("results")
    if not isinstance(rows, list) or not rows:
        raise ProbeFailure("MISSING_RECORD_IDS")
    if any(not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] for row in rows):
        raise ProbeFailure("MISSING_RECORD_IDS")
    return tuple(row["id"] for row in rows)


def search_filter(app, agent):
    return {"AND": [{"app_id": app}, {"agent_id": agent}, {"metadata": {"record_kind": "project_experience"}}]}


def validate_filter_evidence(unfiltered_ids, filtered_ids, valid_ids, limit):
    # If the unfiltered top-k contains a control, post-top-k filtering cannot fill k.
    if (len(unfiltered_ids) != limit or not (set(unfiltered_ids) - valid_ids)
            or len(filtered_ids) != limit or len(set(filtered_ids)) != limit
            or not set(filtered_ids).issubset(valid_ids)):
        raise ProbeFailure("FILTER_ORDER_NOT_ESTABLISHED")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ProbeClient:
    def __init__(self, key, app, agent, *, endpoint="https://api.mem0.ai"):
        if (app != "experience-memory-contract-test" or endpoint != "https://api.mem0.ai"
                or not re.fullmatch(r"contract-[0-9a-f]{16,32}-[ab]", agent) or not key):
            raise ProbeFailure("INVALID_TEST_SCOPE")
        self._key, self.app, self.agent, self.endpoint = key, app, agent, endpoint
        self._http = urllib.request.build_opener(_NoRedirect())

    def request(self, method, path, payload=None, *, deadline=None):
        if not path.startswith(("/v3/memories/", "/v1/memories/", "/v1/event/", "/v2/memories/")):
            raise ProbeFailure("INVALID_TEST_ENDPOINT")
        timeout = min(10, deadline - time.monotonic()) if deadline is not None else 10
        if timeout <= 0:
            raise ProbeFailure("OPERATION_UNKNOWN")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.endpoint + path, data=body, method=method,
                                     headers={"Authorization": "Token " + self._key, "Content-Type": "application/json"})
        try:
            with self._http.open(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise ProbeFailure("HTTP_" + str(error.code)) from None
        except (OSError, ValueError):
            raise ProbeFailure("REQUEST_UNKNOWN") from None

    def add(self, text, kind, *, immutable=False, infer=False):
        operation_id = uuid.uuid4().hex
        result = self.request("POST", "/v3/memories/add/", {
            "messages": [{"role": "user", "content": text}], "app_id": self.app, "agent_id": self.agent,
            "infer": infer, "immutable": immutable,
            "metadata": {"record_kind": kind, "probe_operation_id": operation_id},
        })
        event_id = result.get("event_id") if isinstance(result, dict) else None
        if event_id:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
                raise ProbeFailure("INVALID_EVENT_ID")
            deadline = time.monotonic() + 30
            while True:
                event = self.request("GET", "/v1/event/" + event_id + "/", deadline=deadline)
                status = event.get("status")
                if status == "SUCCEEDED":
                    break
                if status == "FAILED":
                    raise ProbeFailure("EVENT_FAILED")
                if status not in ("PENDING", "RUNNING") or time.monotonic() >= deadline:
                    raise ProbeFailure("OPERATION_UNKNOWN")
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            # Real extraction may return no new records; the caller only needs completion.
            if infer:
                return ()
        elif infer:
            raise ProbeFailure("MISSING_EVENT_ID")
        try:
            ids = confirmed_ids(result)
        except ProbeFailure:
            if not event_id:
                raise
            rows = self.request("POST", "/v3/memories/?page=1&page_size=10", {"filters": {"AND": [
                {"app_id": self.app}, {"agent_id": self.agent}, {"metadata": {"probe_operation_id": operation_id}},
            ]}})
            rows = rows.get("results", []) if isinstance(rows, dict) else rows
            ids = confirmed_ids({"results": rows})
        # HTTP success/IDs alone never establish scope or exact persisted text.
        for record_id in ids:
            saved = self.read(record_id)
            if saved.get("memory") != text or (saved.get("metadata") or {}).get("probe_operation_id") != operation_id:
                raise ProbeFailure("READBACK_MISMATCH")
        return ids

    def read(self, record_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", record_id):
            raise ProbeFailure("INVALID_RECORD_ID")
        saved = self.request("GET", "/v1/memories/" + record_id + "/")
        if saved.get("app_id") != self.app or saved.get("agent_id") != self.agent:
            raise ProbeFailure("SCOPE_MISMATCH")
        return saved

    def search(self, query, limit, *, experiences_only=True):
        filters = search_filter(self.app, self.agent) if experiences_only else {
            "AND": [{"app_id": self.app}, {"agent_id": self.agent}],
        }
        result = self.request("POST", "/v3/memories/search/", {
            "query": query, "filters": filters, "top_k": limit,
        })
        rows = result.get("results") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise ProbeFailure("INVALID_SEARCH_RESPONSE")
        return rows
