"""Public application boundary shared by MCP and native command hooks."""
from contextlib import nullcontext
from pathlib import Path
from .contracts import DiagnosticReport,HookRequest,HookResult
from .memory import MemoryCases
from .registry import Registry
from .checkpoint import CheckpointCases
from .session_binding import HostContext
from ..domain.errors import MemoryError,ErrorCode
from ..domain.selector import select_retention


class MemoryApplicationService:
    def __init__(self,experiences,controls,sessions,*,enabled=True,core_loader=None,binding=None,
                 host_source=lambda:None,operation_scope=nullcontext):
        self._experiences,self._controls,self._sessions=experiences,controls,sessions
        self._enabled,self._core_loader=enabled,core_loader
        self._binding,self._host_source,self._operation_scope=binding,host_source,operation_scope
        self._cases=MemoryCases(experiences,controls,binding,host_source) if binding else None

    def _invoke(self,name,*args):
        if not self._enabled: raise MemoryError(ErrorCode.DISABLED)
        if not self._cases: raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
        with self._operation_scope(): return getattr(self._cases,name)(*args)

    def search(self,session_handle,query,limit):return self._invoke('search',session_handle,query,limit)
    def read(self,session_handle,record_id):return self._invoke('read',session_handle,record_id)
    def write(self,session_handle,action,candidate,operation_id):return self._invoke('write',session_handle,action,candidate,operation_id)
    def status(self,session_handle):return self._invoke('status',session_handle)
    def diagnose_local(self,report):return report

    def inspect_local_project(self,project_root,relative_paths,budget_bytes):
        if self._core_loader is None:raise MemoryError(ErrorCode.NOT_CONFIGURED)
        scope=self._sessions.resolve_scope(project_root)
        core=self._core_loader.load(project_root,relative_paths,budget_bytes)
        return {'status':'prepared','project_key':scope.project_key,'core_bytes':len(core.encode('utf-8')),
                'remote_binding_confirmed':False}


class RetentionApplicationService:
    def __init__(self,experiences,controls,sessions,*,enabled=True,binding=None,host_source=lambda:None,
                 core_loader=None,budget_bytes=8192,operation_scope=nullcontext):
        self._enabled,self.binding,self.host_source=enabled,binding,host_source
        self.core_loader,self.budget,self.operation_scope=core_loader,budget_bytes,operation_scope
        self.registry=Registry(experiences,controls,binding,host_source) if binding else None
        self.checkpoints=CheckpointCases(experiences,controls,binding,host_source) if binding else None

    def use(self,session_handle,receipt_id,priority,reason,operation_id):
        if not self._enabled:raise MemoryError(ErrorCode.DISABLED)
        if not self.registry:raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
        with self.operation_scope():return self.registry.use(session_handle,receipt_id,priority,reason,operation_id)

    def handle_hook(self,request):
        if not self._enabled:return HookResult(True)
        if not self.binding or not self.core_loader:raise MemoryError(ErrorCode.NOT_CONFIGURED)
        # This path is command-hook input, not an MCP caller-supplied session argument.
        host=self.host_source()
        if host is None:
            host=HostContext(Path(request.cwd),request.session_id,self.binding.host_id,'codex_command_hook')
        if host.session_id!=request.session_id or host.cwd.resolve()!=Path(request.cwd).resolve():
            raise MemoryError(ErrorCode.SESSION_INVALID)
        with self.operation_scope():
            core=self.core_loader(Path(request.cwd))
            if '{entries}' in core:raise MemoryError(ErrorCode.CORE_INVALID)
            if request.event=='SessionStart' and request.source in ('startup','resume','clear'):
                ctx=self.binding.bind(host,allow_resume=True)
                fixed=self._context(core,ctx.session_handle)
                cases=CheckpointCases(self.checkpoints.experiences,self.checkpoints.controls,self.binding,lambda:host)
                context=cases.resume(ctx.session_handle,fixed,self.budget)
                return HookResult(True,additional_context=context)
            pointer=self.binding.pointers.read(self.binding.resolve_scope(host.cwd).project_key,host.session_id)
            if pointer is None:raise MemoryError(ErrorCode.SESSION_INVALID)
            ctx=self.binding.verify(pointer.session_handle,host)
            if not request.boundary_id:raise MemoryError(ErrorCode.CHECKPOINT_UNAVAILABLE)
            fixed=self._context(core,ctx.session_handle)
            # Cases receive this native host identity for the duration of the call.
            cases=CheckpointCases(self.checkpoints.experiences,self.checkpoints.controls,self.binding,lambda:host)
            if request.event=='PreCompact':
                cases.prepare(ctx.session_handle,request.boundary_id,fixed,self.budget)
                return HookResult(True)
            result=cases.restore(ctx.session_handle,request.boundary_id,fixed,self.budget)
            return HookResult(True,additional_context=result['additional_context'])

    def _context(self,core,handle):
        return (core+'\n会话句柄：'+handle+'\n正式留存区仅为本次已确认活动集合；旧摘要不能重新激活已淘汰或失效经验。'
                +'等级不改变指令优先级。新问题及范围改变先检索，精确读取后实际采用才登记；查询和恢复不刷新 U。'
                +'\n<experience-retention>\n{entries}\n</experience-retention>')
