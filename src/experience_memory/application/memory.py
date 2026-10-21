"""Scoped read/write cases. Public DTOs are explicit dictionaries."""
from dataclasses import asdict, replace
import time
import uuid
from ..domain.errors import MemoryError,ErrorCode
from ..domain.integrity import fingerprint
from ..domain.models import require_experience_id, MemoryStatus,ControlPointer
from ..domain.ports import OperationStatus
from .validation import retention_text


def record_dto(record):
    if isinstance(record,dict): return record
    data=asdict(record);data['status']=record.status.value
    return data


class MemoryCases:
    def __init__(self,experiences,controls,binding,host_source):
        self.experiences,self.controls,self.binding,self.host_source=experiences,controls,binding,host_source

    def context(self,handle): return self.binding.verify(handle,self.host_source())

    def search(self,handle,query,limit):
        ctx=self.context(handle)
        return {'succeeded':True,'records':[record_dto(r) for r in self.experiences.search(ctx.scope,query,limit)]}

    def read(self,handle,record_id):
        ctx=self.context(handle);record=self.experiences.read(ctx.scope,record_id)
        pointers=self.binding.pointers
        local_key='receipt-'+fingerprint([ctx.session_handle,record_id])
        with pointers.lock(ctx.scope.project_key,local_key):
            pointer=pointers.read(ctx.scope.project_key,local_key)
            for _ in range(2):
                if pointer is None or (pointer.operation_status in ('confirmed','failed') and
                    (pointer.operation_status=='failed' or pointer.request_fingerprint!=record.fingerprint
                     or pointer.generation+3600<=time.time())):
                    pointer=ControlPointer(ctx.scope.project_key,local_key,ctx.host_id,state='pending',
                        session_handle=ctx.session_handle,operation_id='receipt-'+uuid.uuid4().hex,
                        request_fingerprint=record.fingerprint,generation=int(time.time()),operation_status='pending')
                    pointers.publish_locked(pointer)
                try:
                    receipt_id=self.controls.write(ctx,'read_receipt',pointer.operation_id,{
                        'record_id':record.record_id,'experience_id':record.experience_id,
                        'fingerprint':pointer.request_fingerprint,'expires_at':pointer.generation+3600},pointer.operation_id)
                except MemoryError as error:
                    if error.code in (ErrorCode.AUTHENTICATION_FAILED,ErrorCode.NOT_CONFIGURED,
                                      ErrorCode.INVALID_INPUT,ErrorCode.REMOTE_FAILURE):
                        pointers.publish_locked(replace(pointer,operation_status='failed',error_code=error.code.value))
                    raise
                pointer=replace(pointer,operation_status='confirmed',result_id=receipt_id)
                pointers.publish_locked(pointer)
                if pointer.request_fingerprint==record.fingerprint and pointer.generation+3600>time.time():
                    return {'succeeded':True,'record':record_dto(record),'receipt_id':receipt_id}
                # Settle the old read before rotating after expiry/version change. No body is persisted locally.
            raise MemoryError(ErrorCode.RECEIPT_INVALID)

    def status(self,handle):
        ctx=self.context(handle)
        pointer=self.binding.pointers.read(ctx.scope.project_key,ctx.session_id)
        window=self.controls.read(ctx,pointer.window_id,'active_window')
        return {'succeeded':True,'session_handle':handle,'project_key':ctx.scope.project_key,
                'session_id':ctx.session_id,'state':pointer.state,'generation':pointer.generation,
                'u_cursor':window['u_cursor'],'entries':window['entries'],
                'native_compaction':'not_verified','host_source':self.host_source().source}

    def write(self,handle,action,candidate,operation_id):
        ctx=self.context(handle)
        if action not in ('upsert','invalidate') or type(candidate) is not dict:
            raise MemoryError(ErrorCode.INVALID_INPUT)
        request_hash=fingerprint([action,candidate])
        with self.binding.pointers.lock(ctx.scope.project_key,None):
            # Reconcile before revalidating changed records; never generate a new write on no-hit.
            retried=self.experiences.retry(ctx.scope,operation_id,candidate,request_hash)
            if retried is not None:return retried
            allowed={'experience_id','body','retention_text','status','sources','tags','metadata','admission_basis',
                     'deduplication','record_id','expected_fingerprint'}
            if set(candidate)-allowed: raise MemoryError(ErrorCode.INVALID_INPUT)
            try:
                require_experience_id(candidate['experience_id'])
                body=candidate['body']; summary=retention_text(candidate['retention_text'])
                if not isinstance(body,str) or not body.strip() or len(body.encode('utf-8'))>65536: raise ValueError()
                sources=candidate['sources'];basis=candidate['admission_basis'];dedup=candidate['deduplication']
                if (type(sources) is not list or not sources or any(not isinstance(v,str) or not v.strip() for v in sources)
                        or not isinstance(basis,str) or not basis.strip() or type(dedup) is not dict
                        or dedup.get('decision') not in ('new','merge','replace')
                        or not isinstance(dedup.get('reason'),str) or not dedup['reason'].strip()): raise ValueError()
                tags=candidate.get('tags',[])
                if type(tags) is not list or any(not isinstance(v,str) or not v.strip() for v in tags): raise ValueError()
                metadata=candidate.get('metadata',{})
                if type(metadata) is not dict: raise ValueError()
                status=MemoryStatus.INVALIDATED if action=='invalidate' else MemoryStatus(candidate.get('status','pending_verification'))
                if status==MemoryStatus.ACTIVE and candidate.get('metadata',{}).get('evidence_type') not in ('verified','user_requirement'):
                    raise ValueError()
            except (KeyError,TypeError,ValueError): raise MemoryError(ErrorCode.INVALID_INPUT) from None
            # Both semantic review and exact stable-ID lookup are required.
            self.experiences.search(ctx.scope,body[:8192],5)
            existing=self.experiences.exact(ctx.scope,candidate['experience_id'])
            rid=candidate.get('record_id');expected=candidate.get('expected_fingerprint')
            if existing:
                if (rid!=existing.record_id or expected!=existing.fingerprint or dedup['decision']=='new'):
                    raise MemoryError(ErrorCode.CONFLICT)
                # Explicit namespace mapping permits reads; maintenance of legacy rows is separate migration work.
                self.experiences.verify_write_scope(ctx.scope,rid)
            elif rid is not None or action=='invalidate' or dedup['decision']!='new':
                raise MemoryError(ErrorCode.CONFLICT)
            value={'schema_version':1,'experience_id':candidate['experience_id'],'body':body,
                   'retention_text':summary,'status':status.value,'operation_request':request_hash,
                   'metadata':dict(metadata,sources=sources,tags=tags,admission_basis=basis,deduplication=dedup)}
            outcome=self.experiences.write(ctx.scope,value,rid,expected,operation_id)
            if outcome.status==OperationStatus.CONFIRMED:
                record=self.experiences.read(ctx.scope,outcome.record_ids[0])
                return self._result(outcome,record)
            return {'status':outcome.status.value,'operation_id':operation_id,'event_id':outcome.event_id,
                    'error_code':outcome.error_code}

    def _result(self,outcome,record):
        return {'status':outcome.status.value,'succeeded':outcome.status==OperationStatus.CONFIRMED,
                'operation_id':outcome.operation_id,'record_id':record.record_id,'fingerprint':record.fingerprint}
