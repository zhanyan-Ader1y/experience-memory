import json

from ..application.contracts import ErrorCode, HookRequest, MemoryError
from ..application.services import RetentionApplicationService

MAX_INPUT_BYTES = 1024 * 1024


def failure(code: ErrorCode) -> dict:
    return {"continue": False, "stopReason": f"experience-memory: {code.value}", "suppressOutput": True}


def decode_event(event: dict) -> HookRequest:
    if type(event) is not dict:
        raise MemoryError(ErrorCode.INVALID_INPUT)
    name = event.get("hook_event_name")
    if name not in ("SessionStart", "PreCompact"):
        raise MemoryError(ErrorCode.INVALID_INPUT)
    for field in ("cwd", "session_id"):
        value = event.get(field)
        if type(value) is not str or not value.strip() or any(ch in value for ch in "\r\n\x00"):
            raise MemoryError(ErrorCode.INVALID_INPUT)
    source, trigger = event.get("source"), event.get("trigger")
    if name == "SessionStart" and source not in ("startup", "resume", "clear", "compact"):
        raise MemoryError(ErrorCode.INVALID_INPUT)
    if name == "PreCompact" and trigger not in ("manual", "auto"):
        raise MemoryError(ErrorCode.INVALID_INPUT)
    boundary = event.get("compaction_id", event.get("turn_id"))
    if boundary is not None and (not isinstance(boundary,str) or not boundary.strip()
                                 or any(ch in boundary for ch in "\r\n\x00")):
        raise MemoryError(ErrorCode.INVALID_INPUT)
    return HookRequest(name, event["cwd"], event["session_id"], source, trigger, boundary)


def handle_event(service: RetentionApplicationService, event: dict, *, budget_bytes: int = 8192) -> dict:
    try:
        request = decode_event(event)
        result = service.handle_hook(request)
        output = {"continue": result.continue_, "suppressOutput": True}
        if result.stop_reason:
            output["stopReason"] = result.stop_reason
        if result.additional_context is not None:
            if request.event != "SessionStart":
                raise MemoryError(ErrorCode.INVALID_INPUT)
            if len(result.additional_context.encode("utf-8")) > budget_bytes:
                raise MemoryError(ErrorCode.INVALID_INPUT)
            output["hookSpecificOutput"] = {
                "hookEventName": "SessionStart", "additionalContext": result.additional_context,
            }
        return output
    except MemoryError as error:
        return failure(error.code)
    except Exception:
        return failure(ErrorCode.INTERNAL_ERROR)


def _unique_object(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise MemoryError(ErrorCode.INVALID_INPUT)
        output[key] = value
    return output


def run_hook(service: RetentionApplicationService, input_stream, output_stream, *, budget_bytes: int = 8192) -> int:
    try:
        text = input_stream.read(MAX_INPUT_BYTES + 1)
        if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise MemoryError(ErrorCode.INVALID_INPUT)
        event = json.loads(text, object_pairs_hook=_unique_object)
        output = handle_event(service, event, budget_bytes=budget_bytes)
    except (ValueError, UnicodeError):
        output = failure(ErrorCode.INVALID_INPUT)
    except MemoryError as error:
        output = failure(error.code)
    output_stream.write(json.dumps(output, ensure_ascii=False) + "\n")
    return 0
