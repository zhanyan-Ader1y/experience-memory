import json
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from ..application.contracts import ErrorCode, MemoryError
from ..application.services import MemoryApplicationService, RetentionApplicationService

Identity = Annotated[str, Field(strict=True, min_length=1, max_length=512)]
Query = Annotated[str, Field(strict=True, min_length=1, max_length=8192)]
Limit = Annotated[int, Field(strict=True, ge=1, le=20)]


def _invoke(operation, *arguments) -> CallToolResult:
    try:
        body = operation(*arguments)
        if not isinstance(body, dict):
            raise MemoryError(ErrorCode.INTERNAL_ERROR)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
                              isError=body.get("status") == "failed")
    except MemoryError as error:
        code = error.code
    except Exception:
        code = ErrorCode.INTERNAL_ERROR
    body = {"status": "failed", "error_code": code.value}
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(body))], isError=True)


def create_server(memory: MemoryApplicationService, retention: RetentionApplicationService,
                  *, search_limit: int = 5, host_context_manager=None) -> MCPServer:
    def dispatch(ctx,operation,*args):
        # Fixtures use an injected source; real stdio uses per-request transport metadata.
        if host_context_manager is not None:
            try:
                with host_context_manager(ctx.request_context.meta):
                    return _invoke(operation,*args)
            except MemoryError as error:
                return _invoke(lambda: (_ for _ in ()).throw(error))
        return _invoke(operation,*args)

    server = MCPServer("experience-memory", version="0.1.0", log_level="CRITICAL",
                       instructions="只使用本会话已绑定句柄。新问题检索，候选精确读取后实际采用才登记。pending/unknown 不等于保存成功；同逻辑重试保留 operation_id。缺可信宿主身份时拒绝，原生能力待 G1 验收。")
    readonly = ToolAnnotations(readOnlyHint=True)
    writes = ToolAnnotations(readOnlyHint=False)

    @server.tool(annotations=readonly)
    def memory_search(session_handle: Identity, query: Query, ctx: Context, limit: Limit | None = None) -> CallToolResult:
        """Scoped experience search. Requires a confirmed scoped binding."""
        return dispatch(ctx, memory.search, session_handle, query, search_limit if limit is None else limit)

    @server.tool(annotations=writes)
    def memory_read(session_handle: Identity, record_id: Identity, ctx: Context) -> CallToolResult:
        """Exact record read and receipt. Requires a confirmed scoped binding."""
        return dispatch(ctx, memory.read, session_handle, record_id)

    @server.tool(annotations=writes)
    def memory_write(session_handle: Identity, action: Literal["upsert", "invalidate"],
                     candidate: dict, operation_id: Identity, ctx: Context) -> CallToolResult:
        """Explicit, deduplicated maintenance. Requires a confirmed scoped binding."""
        return dispatch(ctx, memory.write, session_handle, action, candidate, operation_id)

    @server.tool(annotations=writes)
    def memory_use(session_handle: Identity, receipt_id: Identity, priority: Literal["P1", "P2", "P3"],
                   reason: Query, operation_id: Identity, ctx: Context) -> CallToolResult:
        """Register actual adoption using a verified receipt. Requires a confirmed scoped binding."""
        return dispatch(ctx, retention.use, session_handle, receipt_id, priority, reason, operation_id)

    @server.tool(annotations=readonly)
    def memory_status(session_handle: Identity, ctx: Context) -> CallToolResult:
        """Session-scoped memory health. Use doctor for local package checks."""
        return dispatch(ctx, memory.status, session_handle)

    return server
