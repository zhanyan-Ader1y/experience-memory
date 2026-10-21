"""Effective adoption: one confirmed operation, one U, one immutable window."""
from dataclasses import asdict, replace
import time
from ..domain.errors import MemoryError,ErrorCode
from ..domain.integrity import fingerprint
from ..domain.models import Priority,MemoryStatus,WindowEntry


class Registry:
    def __init__(self,experiences,controls,binding,host_source):
        self.experiences,self.controls,self.binding,self.host_source=experiences,controls,binding,host_source

    def use(self,handle,receipt_id,priority,reason,operation_id):
        ctx=self.binding.verify(handle,self.host_source())
        try:
            level=Priority(priority)
            if not isinstance(reason,str) or not reason.strip() or len(reason)>8192: raise ValueError()
            from ..domain.models import require_identity
            require_identity(operation_id)
        except ValueError: raise MemoryError(ErrorCode.INVALID_INPUT) from None
        request_hash=fingerprint([receipt_id,priority,reason,operation_id])
        key='use-'+fingerprint([handle,operation_id])
        pointers=self.binding.pointers
        with pointers.lock(ctx.scope.project_key,None),pointers.lock(ctx.scope.project_key,ctx.session_id):
            pointer=pointers.read(ctx.scope.project_key,ctx.session_id)
            if pointer.checkpoint_state in ('pending','ready'):
                raise MemoryError(ErrorCode.CHECKPOINT_UNAVAILABLE)
            if pointer.state=='pending' and pointer.operation_id!='use:'+operation_id:
                raise MemoryError(ErrorCode.OPERATION_UNKNOWN)
            event=self.controls.find(ctx,'use_event',key)
            if event:
                _,value=event
                if value['request_fingerprint']!=request_hash: raise MemoryError(ErrorCode.CONFLICT)
                self.controls.write(ctx,'use_event',key,value,'event-'+key)
                # Publish only while this exact pending parent is still current; old retries don't roll back newer windows.
                if pointer.state=='pending':
                    if pointer.window_id!=value['parent_window_id']: raise MemoryError(ErrorCode.CONFLICT)
                    pointers.publish_locked(replace(pointer,state='ready',operation_id=None,window_id=value['window_id']))
                return value['result']
            receipt=self.controls.read(ctx,receipt_id,'read_receipt')
            if receipt['expires_at']<=time.time(): raise MemoryError(ErrorCode.RECEIPT_INVALID)
            record=self.experiences.read(ctx.scope,receipt['record_id'])
            if record.status!=MemoryStatus.ACTIVE or record.fingerprint!=receipt['fingerprint']:
                raise MemoryError(ErrorCode.RECEIPT_INVALID)
            window=self.controls.read(ctx,pointer.window_id,'active_window')
            u=window['u_cursor']+1
            entry=asdict(WindowEntry(record.record_id,record.experience_id,level,u,record.fingerprint))
            entries=[e for e in window['entries'] if e['experience_id']!=record.experience_id]+[entry]
            pointers.publish_locked(replace(pointer,state='pending',operation_id='use:'+operation_id))
            next_window=dict(window,entries=entries,u_cursor=u,parent_window_id=pointer.window_id)
            window_id=self.controls.write(ctx,'active_window',key,next_window,'window-'+key)
            result={'succeeded':True,'status':'confirmed','operation_id':operation_id,'window_id':window_id,'entry':entry}
            self.controls.write(ctx,'use_event',key,{'request_fingerprint':request_hash,'window_id':window_id,
                'parent_window_id':pointer.window_id,'result':result,'reason':reason},'event-'+key)
            pointers.publish_locked(replace(pointer,state='ready',operation_id=None,window_id=window_id))
            return result
