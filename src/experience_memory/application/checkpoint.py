"""Checkpoint prepare/restore uses only the current window, never full experience history."""
from dataclasses import asdict,replace
from ..domain.errors import MemoryError,ErrorCode
from ..domain.integrity import fingerprint
from ..domain.models import WindowEntry,Priority,MemoryStatus,require_identity
from ..domain.selector import select_retention,MaterializedEntry


def entry_from(value):
    return WindowEntry(value['record_id'],value['experience_id'],Priority(value['priority']),
                       value['last_used_seq'],value['fingerprint'])


class CheckpointCases:
    def __init__(self,experiences,controls,binding,host_source):
        self.experiences,self.controls,self.binding,self.host_source=experiences,controls,binding,host_source

    def materialize(self,ctx,entries):
        materialized=[];dropped=[]
        for raw in entries:
            entry=entry_from(raw)
            try:
                record=self.experiences.read(ctx.scope,entry.record_id)
                if record.status==MemoryStatus.INVALIDATED:
                    dropped.append({'experience_id':entry.experience_id,'reason':'invalidated'});continue
                if record.status!=MemoryStatus.ACTIVE or record.fingerprint!=entry.fingerprint:
                    raise MemoryError(ErrorCode.NEEDS_REVIEW)
                materialized.append(MaterializedEntry(entry,record.retention_text))
            except MemoryError as error:
                if error.code==ErrorCode.NOT_FOUND:
                    dropped.append({'experience_id':entry.experience_id,'reason':'deleted'});continue
                if entry.priority==Priority.P1:raise
                dropped.append({'experience_id':entry.experience_id,'reason':error.code.value})
        return materialized,dropped

    def prepare(self,handle,boundary_id,fixed_context,budget):
        require_identity(boundary_id)
        ctx=self.binding.verify(handle,self.host_source());pointers=self.binding.pointers
        with pointers.lock(ctx.scope.project_key,None),pointers.lock(ctx.scope.project_key,ctx.session_id):
            pointer=pointers.read(ctx.scope.project_key,ctx.session_id)
            if pointer.state=='pending' and pointer.operation_id!='prepare:'+boundary_id:
                raise MemoryError(ErrorCode.OPERATION_UNKNOWN)
            if pointer.boundary_id==boundary_id and pointer.checkpoint_state in ('ready','restored'):
                return {'status':'confirmed','checkpoint_id':pointer.checkpoint_id,'generation':pointer.generation}
            generation=pointer.generation if pointer.boundary_id==boundary_id else pointer.generation+1
            pointer=replace(pointer,state='pending',operation_id='prepare:'+boundary_id,checkpoint_id=None,
                            checkpoint_state='pending',boundary_id=boundary_id,generation=generation)
            pointers.publish_locked(pointer) # never leave old ready eligible while preparing a new boundary
            window=self.controls.read(ctx,pointer.window_id,'active_window')
            items,dropped=self.materialize(ctx,window['entries'])
            selection=select_retention(items,fixed_context,budget)
            key='checkpoint-'+fingerprint([handle,boundary_id])
            checkpoint_id=self.controls.write(ctx,'checkpoint',key,{
                'schema_version':1,'generation':generation,'boundary_id':boundary_id,
                'source_window_id':pointer.window_id,'u_cursor':window['u_cursor'],
                'entries':[asdict(item.entry) for item in selection.selected],'budget_bytes':budget,
                'dropped':dropped+[{'experience_id':item.entry.experience_id,'reason':'budget'} for item in selection.dropped]},key)
            pointers.publish_locked(replace(pointer,state='ready',operation_id=None,checkpoint_id=checkpoint_id,checkpoint_state='ready'))
            return {'status':'confirmed','checkpoint_id':checkpoint_id,'generation':generation}

    def restore(self,handle,boundary_id,fixed_context,budget):
        ctx=self.binding.verify(handle,self.host_source());pointers=self.binding.pointers
        with pointers.lock(ctx.scope.project_key,None),pointers.lock(ctx.scope.project_key,ctx.session_id):
            pointer=pointers.read(ctx.scope.project_key,ctx.session_id)
            if (pointer.boundary_id!=boundary_id or pointer.checkpoint_state not in ('ready','restored')
                    or not pointer.checkpoint_id):raise MemoryError(ErrorCode.CHECKPOINT_UNAVAILABLE)
            if pointer.state=='pending' and pointer.operation_id!='restore:'+boundary_id:
                raise MemoryError(ErrorCode.OPERATION_UNKNOWN)
            checkpoint=self.controls.read(ctx,pointer.checkpoint_id,'checkpoint')
            if checkpoint['generation']!=pointer.generation or checkpoint['boundary_id']!=boundary_id:
                raise MemoryError(ErrorCode.CHECKPOINT_UNAVAILABLE)
            key='restore-'+fingerprint([handle,boundary_id])
            event=self.controls.find(ctx,'restore_event',key)
            if event:
                _,event=event
                self.controls.write(ctx,'restore_event',key,event,'event-'+key)
                if pointer.state!='pending' and pointer.window_id!=event['window_id']:
                    raise MemoryError(ErrorCode.CHECKPOINT_UNAVAILABLE)
                window=self.controls.read(ctx,event['window_id'],'active_window')
                items,_=self.materialize(ctx,window['entries'])
                selection=select_retention(items,fixed_context,budget)
                if fingerprint(selection.rendered_context)!=event['payload_fingerprint']:
                    raise MemoryError(ErrorCode.CONFLICT)
                pointers.publish_locked(replace(pointer,state='ready',operation_id=None,
                    window_id=event['window_id'],checkpoint_state='restored'))
                return {'status':'confirmed','additional_context':selection.rendered_context,'dropped':event['dropped']}
            items,dropped=self.materialize(ctx,checkpoint['entries'])
            selection=select_retention(items,fixed_context,budget)
            dropped=checkpoint['dropped']+dropped+[{'experience_id':item.entry.experience_id,'reason':'budget'} for item in selection.dropped]
            pointers.publish_locked(replace(pointer,state='pending',operation_id='restore:'+boundary_id))
            window_id=self.controls.write(ctx,'active_window',key,{'generation':checkpoint['generation'],
                'u_cursor':checkpoint['u_cursor'],'entries':[asdict(item.entry) for item in selection.selected],
                'parent_window_id':checkpoint['source_window_id']},'window-'+key)
            self.controls.write(ctx,'restore_event',key,{'window_id':window_id,'dropped':dropped,
                'payload_fingerprint':fingerprint(selection.rendered_context)},'event-'+key)
            pointers.publish_locked(replace(pointer,state='ready',operation_id=None,window_id=window_id,checkpoint_state='restored'))
            return {'status':'confirmed','additional_context':selection.rendered_context,'dropped':dropped}

    def resume(self,handle,fixed_context,budget):
        ctx=self.binding.verify(handle,self.host_source());pointers=self.binding.pointers
        with pointers.lock(ctx.scope.project_key,None),pointers.lock(ctx.scope.project_key,ctx.session_id):
            pointer=pointers.read(ctx.scope.project_key,ctx.session_id)
            key='resume-'+fingerprint([handle,pointer.window_id,fixed_context,budget])
            if pointer.state=='pending' and pointer.operation_id!=key:
                raise MemoryError(ErrorCode.OPERATION_UNKNOWN)
            if pointer.checkpoint_state in ('pending','ready'):
                raise MemoryError(ErrorCode.CHECKPOINT_UNAVAILABLE)
            window=self.controls.read(ctx,pointer.window_id,'active_window')
            items,dropped=self.materialize(ctx,window['entries'])
            selection=select_retention(items,fixed_context,budget)
            entries=[asdict(item.entry) for item in selection.selected]
            if entries!=window['entries']:
                pointers.publish_locked(replace(pointer,state='pending',operation_id=key))
                window_id=self.controls.write(ctx,'active_window',key,dict(window,entries=entries,parent_window_id=pointer.window_id),key)
                pointers.publish_locked(replace(pointer,state='ready',operation_id=None,window_id=window_id))
            return selection.rendered_context
