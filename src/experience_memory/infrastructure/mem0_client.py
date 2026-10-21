"""Bounded public HTTPS API. No retries on writes, redirects, or private plugin imports."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import re
import time
import urllib.error
import urllib.request
from ..domain.errors import MemoryError, ErrorCode

_deadline = ContextVar("memory_deadline", default=None)


@contextmanager
def deadline(seconds=30):
    current = _deadline.get()
    token = _deadline.set(min(current, time.monotonic()+seconds) if current else time.monotonic()+seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def credentials():
    key = os.environ.get("MEM0_API_KEY", "").strip()
    if not key and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as entry:
                key = winreg.QueryValueEx(entry, "MEM0_API_KEY")[0].strip()
        except OSError:
            pass
    return key


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def api_id(value):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",value):
        raise MemoryError(ErrorCode.INVALID_INPUT)
    return value


class Mem0Client:
    def __init__(self, key, endpoint="https://api.mem0.ai", timeout=10):
        # Credentials must never be sent to a project-selected arbitrary host.
        if endpoint.rstrip("/") != "https://api.mem0.ai":
            raise MemoryError(ErrorCode.INVALID_CONFIG)
        self._key, self.timeout = key, min(timeout,10)
        self.http = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, payload=None):
        if not self._key:
            raise MemoryError(ErrorCode.NOT_CONFIGURED)
        if not re.fullmatch(r"/(?:v3/memories/(?:add/|search/|\?page=\d+&page_size=\d+)|v1/(?:memories|event)/[A-Za-z0-9_-]{1,128}/)",path):
            raise MemoryError(ErrorCode.INVALID_INPUT)
        remaining = (_deadline.get()-time.monotonic()) if _deadline.get() else self.timeout
        if remaining <= 0:
            raise MemoryError(ErrorCode.OPERATION_UNKNOWN)
        raw=None if payload is None else json.dumps(payload,ensure_ascii=False).encode("utf-8")
        request=urllib.request.Request("https://api.mem0.ai"+path,data=raw,method=method,
            headers={"Authorization":"Token "+self._key,"Content-Type":"application/json"})
        try:
            with self.http.open(request,timeout=min(self.timeout,remaining)) as response:
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024: raise ValueError()
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            code=(ErrorCode.AUTHENTICATION_FAILED if error.code in (401,403) else
                  ErrorCode.NOT_FOUND if error.code==404 else
                  ErrorCode.OPERATION_UNKNOWN if error.code>=500 else ErrorCode.REMOTE_FAILURE)
            raise MemoryError(code) from None
        except (OSError,ValueError):
            raise MemoryError(ErrorCode.OPERATION_UNKNOWN) from None
