import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__
from .application.contracts import DiagnosticReport
from .bootstrap import build_runtime
from .config import load_config, load_project_config
from .infrastructure.scope import resolve_project_root
from .domain.errors import ErrorCode, MemoryError


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser(prog="experience-memory")
    parser.add_argument("command", choices=("mcp", "hook", "doctor"))
    parser.add_argument("--config", type=Path, help="Explicit allowlisted project configuration")
    parser.add_argument("--plugin-root", type=Path, default=os.environ.get("PLUGIN_ROOT"))
    parser.add_argument("--project-root", type=Path, help="Explicit project for local readiness checks")
    args = parser.parse_args(arguments)
    try:
        hook_event=None
        if args.command == "hook":
            from .interfaces.hooks import MAX_INPUT_BYTES, _unique_object, decode_event
            raw=sys.stdin.read(MAX_INPUT_BYTES+1)
            try:
                if len(raw.encode("utf-8"))>MAX_INPUT_BYTES:raise ValueError()
                hook_event=json.loads(raw,object_pairs_hook=_unique_object)
                decode_event(hook_event)
            except (ValueError,UnicodeError):raise MemoryError(ErrorCode.INVALID_INPUT) from None
        if hook_event is not None:
            project_root=resolve_project_root(Path(hook_event["cwd"]))
        elif args.project_root is not None:
            project_root = resolve_project_root(args.project_root)
        else:
            try:
                project_root = resolve_project_root(Path.cwd())
            except MemoryError:
                project_root = None
        config = load_project_config(project_root, args.config) if project_root is not None else load_config(args.config)
        data_root=None
        if args.command in ("hook","mcp"):
            data_root=Path(os.environ.get("PLUGIN_DATA",str(Path.home()/".experience-memory")))/"control"
        runtime = build_runtime(config,data_root=data_root,project_root=project_root,plugin_root=args.plugin_root)
        if args.command == "mcp":
            from .interfaces.mcp_server import create_server
            from .infrastructure.host_context import request_host
            def host_context(metadata):
                return request_host(metadata,runtime.binding.host_id)
            create_server(runtime.memory,runtime.retention,search_limit=config.search_limit,
                          host_context_manager=host_context).run(transport="stdio")
            return 0
        if args.command == "hook":
            from .interfaces.hooks import handle_event
            print(json.dumps(handle_event(runtime.retention,hook_event,budget_bytes=config.retention_budget_bytes),ensure_ascii=False))
            return 0
        from .infrastructure.environment import inspect_environment
        from .interfaces.diagnostics import doctor
        report = DiagnosticReport(__version__, "functional_development_native_pending", runtime.adapter_status, len(config.core_paths),
                                  bool(os.environ.get("MEM0_API_KEY")), config.enabled,
                                  runtime_environment=inspect_environment())
        output = doctor(runtime.memory, report, args.plugin_root)
        if args.project_root is not None:
            output["local_project"] = runtime.memory.inspect_local_project(
                project_root, config.core_paths, config.retention_budget_bytes,
            )
        print(json.dumps(output, ensure_ascii=False))
        return 0 if output["local_checks_passed"] and output["runtime_checks_passed"] else 1
    except MemoryError as error:
        code = error.code
    except Exception:
        code = ErrorCode.INTERNAL_ERROR
    if args.command == "hook":
        from .interfaces.hooks import failure
        print(json.dumps(failure(code)))
        return 0
    print(json.dumps({"status": "failed", "error_code": code.value}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
