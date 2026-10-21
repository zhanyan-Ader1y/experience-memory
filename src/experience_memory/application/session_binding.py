"""Host identity is supplied by the transport adapter, never by MCP tool arguments."""
from dataclasses import dataclass, replace
from pathlib import Path
import secrets
from ..domain.errors import MemoryError, ErrorCode
from ..domain.models import SessionContext, ControlPointer, require_identity
from ..domain.integrity import fingerprint


from ..domain.models import HostContext
from ..domain.ports import PointerStorePort,ControlStateRepository


class SessionBinding:
    def __init__(self,pointers: PointerStorePort,controls: ControlStateRepository,resolve_scope,host_id="host"):
        self.host_id=host_id
        self.pointers,self.controls,self.resolve_scope=pointers,controls,resolve_scope

    def bind(self,host,*,allow_resume=False):
        if not isinstance(host,HostContext): raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
        scope=self.resolve_scope(host.cwd)
        with self.pointers.lock(scope.project_key,host.session_id):
            pointer=self.pointers.read(scope.project_key,host.session_id)
            if pointer and pointer.host_id!=host.host_id: raise MemoryError(ErrorCode.SESSION_INVALID)
            if pointer and pointer.state=='ready': return self._verify(pointer,host,scope)
            if (pointer and pointer.state=='pending' and allow_resume
                    and (pointer.operation_id or '').startswith('resume-')):
                return self._verify(pointer,host,scope)
            if pointer and pointer.state=='pending' and pointer.operation_id!='bind':
                raise MemoryError(ErrorCode.OPERATION_UNKNOWN)
            if not pointer or not pointer.session_handle:
                pointer=ControlPointer(scope.project_key,host.session_id,host.host_id,
                    session_handle=secrets.token_urlsafe(32),state='pending',operation_id='bind')
                self.pointers.publish_locked(pointer)
            ctx=SessionContext(scope,host.session_id,pointer.session_handle,host.host_id)
            key='binding-'+fingerprint([scope.project_key,host.session_id,host.host_id])
            value={'session_handle':ctx.session_handle,'session_id':ctx.session_id,'host_id':ctx.host_id,
                   'project_key':scope.project_key,'schema_version':1}
            binding_id=self.controls.save(scope,'session_binding',key,value,key)
            window_id=self.controls.write(ctx,'active_window','initial-'+ctx.session_handle,
                {'generation':0,'u_cursor':0,'entries':[],'parent_window_id':None},'initial-'+ctx.session_handle)
            self.pointers.publish_locked(replace(pointer,state='ready',operation_id=None,
                                           result_id=binding_id,window_id=window_id))
            return ctx

    def _verify(self,pointer,host,scope):
        if (pointer.session_handle is None or pointer.result_id is None or pointer.host_id!=host.host_id
                or pointer.session_id!=host.session_id): raise MemoryError(ErrorCode.SESSION_INVALID)
        ctx=SessionContext(scope,host.session_id,pointer.session_handle,host.host_id)
        binding=self.controls.read(ctx,pointer.result_id,'session_binding')
        if (binding.get('session_id')!=host.session_id or binding.get('host_id')!=host.host_id
                or binding.get('project_key')!=scope.project_key): raise MemoryError(ErrorCode.SESSION_INVALID)
        return ctx

    def verify(self,handle,host):
        if not isinstance(host,HostContext): raise MemoryError(ErrorCode.HOST_SOURCE_UNVERIFIED)
        scope=self.resolve_scope(host.cwd)
        pointer=self.pointers.read(scope.project_key,host.session_id)
        if not pointer or not secrets.compare_digest(pointer.session_handle or '',handle):
            raise MemoryError(ErrorCode.SESSION_INVALID)
        return self._verify(pointer,host,scope)
