"""JSON envelopes on Mem0, exact paginated keys, crash-safe local ID-only operation journal."""
from dataclasses import replace
import hashlib
import json
from ..domain.models import ControlPointer, require_identity
from ..domain.ports import OperationStatus, WriteOutcome
from ..domain.errors import MemoryError, ErrorCode
from .mem0_client import api_id


from ..domain.integrity import canonical, fingerprint


class Mem0Store:
    def __init__(self, client, pointers):
        self.client, self.pointers = client, pointers

    def filters(self, scope, kind=None, **metadata):
        if kind is not None: metadata["record_kind"]=kind
        terms=[{"app_id":scope.app_id},{"agent_id":scope.project_id}]
        terms.extend({"metadata":{key:value}} for key,value in metadata.items())
        return {"AND":terms}

    def rows(self, scope, kind, **metadata):
        rows=[]
        for page in range(1,101):
            result=self.client.request("POST",f"/v3/memories/?page={page}&page_size=100",
                                       {"filters":self.filters(scope,kind,**metadata)})
            batch=result.get("results") if isinstance(result,dict) else result
            if not isinstance(batch,list): raise MemoryError(ErrorCode.REMOTE_FAILURE)
            for row in batch: self._scope(scope,row)
            rows.extend(batch)
            if isinstance(result,dict) and "next" in result:
                if not result["next"]: return rows
            elif len(batch)<100: return rows
        raise MemoryError(ErrorCode.REMOTE_FAILURE)

    def _scope(self, scope, row):
        if not isinstance(row,dict) or row.get("app_id")!=scope.app_id or row.get("agent_id")!=scope.project_id:
            raise MemoryError(ErrorCode.SCOPE_MISMATCH)

    def raw(self, scope, record_id):
        row=self.client.request("GET","/v1/memories/"+api_id(record_id)+"/")
        self._scope(scope,row)
        return row

    def get(self, scope, record_id, kind):
        row=self.raw(scope,record_id)
        if (row.get("metadata") or {}).get("record_kind")!=kind:
            raise MemoryError(ErrorCode.NEEDS_REVIEW)
        try:
            value=json.loads(row["memory"])
            if type(value) is not dict: raise ValueError()
            return value
        except (KeyError,ValueError,TypeError): raise MemoryError(ErrorCode.NEEDS_REVIEW) from None

    def find(self, scope, kind, key):
        rows=self.rows(scope,kind,logical_key=key)
        if len(rows)>1: raise MemoryError(ErrorCode.CONFLICT)
        return rows[0] if rows else None

    def metadata_for(self,kind,key,value,operation_id,request_hash):
        metadata={"record_kind":kind,"logical_key":key,"operation_id":operation_id,"request_fingerprint":request_hash}
        if kind=="project_experience":
            metadata.update(experience_id=value["experience_id"],status=value["status"],
                            tags=value["metadata"].get("tags",[]),sources=value["metadata"].get("sources",[]))
        return metadata

    def operation(self, scope, operation_id):
        require_identity(operation_id)
        return self.pointers.read(scope.project_key,"operation-"+fingerprint([scope.project_key,operation_id]))

    def event_outcome(self,operation_id,event_id):
        event=self.client.request("GET","/v1/event/"+api_id(event_id)+"/")
        status=event.get("status") if isinstance(event,dict) else None
        if status in ("PENDING","RUNNING"):
            return WriteOutcome(OperationStatus.PENDING,operation_id,event_id=event_id)
        if status=="FAILED":return WriteOutcome(OperationStatus.FAILED,operation_id,event_id=event_id)
        if status=="SUCCEEDED":return WriteOutcome(OperationStatus.CONFIRMED,operation_id,event_id=event_id)
        return WriteOutcome(OperationStatus.UNKNOWN,operation_id,event_id=event_id)

    def finish_failed(self,scope,operation_id,kind,key):
        logical_guard="logical-"+fingerprint([scope.project_key,kind,key])
        journal_key="operation-"+fingerprint([scope.project_key,operation_id])
        with self.pointers.lock(scope.project_key,logical_guard),self.pointers.lock(scope.project_key,journal_key):
            journal=self.operation(scope,operation_id)
            if journal is None or journal.operation_status!="failed":raise MemoryError(ErrorCode.CONFLICT)
            guard=self.pointers.read(scope.project_key,logical_guard)
            return self.mark_failed(journal,guard,journal.error_code or ErrorCode.REMOTE_FAILURE.value)

    def mark_failed(self,journal,guard,error_code):
        journal=replace(journal,operation_status="failed",error_code=error_code)
        self.pointers._publish(journal)
        if guard and guard.operation_id==journal.operation_id:
            self.pointers._publish(replace(guard,operation_status="failed",error_code=error_code))
        return WriteOutcome(OperationStatus.FAILED,journal.operation_id,event_id=journal.event_id,error_code=error_code)

    def save(self, scope, kind, key, value, operation_id, *, record_id=None, expected_fingerprint=None):
        require_identity(operation_id)
        request_hash=fingerprint([kind,key,value,record_id,expected_fingerprint])
        journal_key="operation-"+fingerprint([scope.project_key,operation_id])
        logical_guard="logical-"+fingerprint([scope.project_key,kind,key])
        with self.pointers.lock(scope.project_key,logical_guard),self.pointers.lock(scope.project_key,journal_key):
            guard=self.pointers.read(scope.project_key,logical_guard)
            if guard and guard.operation_id!=operation_id and guard.operation_status not in ('confirmed','failed'):
                raise MemoryError(ErrorCode.OPERATION_UNKNOWN)
            journal=self.pointers.read(scope.project_key,journal_key)
            if journal and journal.request_fingerprint!=request_hash:
                raise MemoryError(ErrorCode.CONFLICT)
            if journal is None:
                existing=self.find(scope,kind,key)
                if existing and record_id is None: raise MemoryError(ErrorCode.CONFLICT)
                if record_id is not None:
                    old=self.raw(scope,record_id)
                    if not existing or existing["id"]!=record_id or fingerprint(json.loads(old["memory"]))!=expected_fingerprint:
                        raise MemoryError(ErrorCode.CONFLICT)
                journal=ControlPointer(scope.project_key,journal_key,"operation-journal",state="pending",
                    operation_id=operation_id,request_fingerprint=request_hash,operation_status="pending",
                    client_fingerprint=value.get("operation_request"))
                self.pointers._publish(ControlPointer(scope.project_key,logical_guard,"operation-journal",
                    state="pending",operation_id=operation_id,operation_status="pending",request_fingerprint=request_hash))
                self.pointers._publish(journal)  # HTTP dispatch only after both control records exist
                first=True
            else: first=False
            if journal.operation_status=="failed":
                return self.mark_failed(journal,guard,journal.error_code or ErrorCode.REMOTE_FAILURE.value)
            if journal.operation_status=="confirmed":
                row=self.raw(scope,journal.result_id)
                expected_metadata=self.metadata_for(kind,key,value,operation_id,request_hash)
                if row.get("memory")!=canonical(value) or row.get("metadata")!=expected_metadata:
                    raise MemoryError(ErrorCode.CONFLICT)
                if guard and guard.operation_id==operation_id and guard.operation_status!="confirmed":
                    self.pointers._publish(replace(guard,operation_status="confirmed",result_id=journal.result_id))
                return WriteOutcome(OperationStatus.CONFIRMED,operation_id,(journal.result_id,))
            metadata=self.metadata_for(kind,key,value,operation_id,request_hash)
            if not first:
                # A missing search result is never evidence the old write didn't happen.
                matches=self.rows(scope,kind,operation_id=operation_id)
                if len(matches)>1: raise MemoryError(ErrorCode.CONFLICT)
                if journal.event_id:
                    outcome=self.event_outcome(operation_id,journal.event_id)
                    if outcome.status==OperationStatus.FAILED:
                        return self.mark_failed(journal,guard,ErrorCode.REMOTE_FAILURE.value)
                    if outcome.status!=OperationStatus.CONFIRMED:return outcome
                if not matches:
                    return WriteOutcome(OperationStatus.UNKNOWN,operation_id,event_id=journal.event_id)
                record_id=matches[0]["id"]
            else:
                try:
                    if record_id is None:
                        response=self.client.request("POST","/v3/memories/add/",{
                            "messages":[{"role":"user","content":canonical(value)}],"infer":False,
                            "immutable":kind!="project_experience","app_id":scope.app_id,"agent_id":scope.project_id,
                            "metadata":metadata})
                        event=response.get("event_id") if isinstance(response,dict) else None
                        ids=[r["id"] for r in response.get("results",[]) if isinstance(r,dict) and r.get("id")] if isinstance(response,dict) else []
                        journal=replace(journal,event_id=api_id(event) if event else None)
                        self.pointers._publish(journal)
                        if event or len(ids)!=1:
                            return WriteOutcome(OperationStatus.PENDING if event else OperationStatus.UNKNOWN,operation_id,event_id=event)
                        record_id=ids[0]
                    else:
                        self.client.request("PUT","/v1/memories/"+api_id(record_id)+"/",{"text":canonical(value),"metadata":metadata})
                except MemoryError as error:
                    unknown=error.code==ErrorCode.OPERATION_UNKNOWN
                    if not unknown:
                        guard=self.pointers.read(scope.project_key,logical_guard)
                        return self.mark_failed(journal,guard,error.code.value)
                    self.pointers._publish(replace(journal,operation_status="unknown",error_code=error.code.value))
                    return WriteOutcome(OperationStatus.UNKNOWN,operation_id,error_code=error.code.value)
            try:
                row=self.raw(scope,record_id)
                if (row.get("memory")!=canonical(value) or row.get("metadata")!=metadata):
                    raise MemoryError(ErrorCode.CONFLICT)
            except MemoryError as error:
                if error.code==ErrorCode.CONFLICT: raise
                return WriteOutcome(OperationStatus.UNKNOWN,operation_id,(record_id,),journal.event_id,error.code.value)
            self.pointers._publish(replace(journal,result_id=record_id,operation_status="confirmed"))
            guard=self.pointers.read(scope.project_key,logical_guard)
            if guard and guard.operation_id==operation_id:
                self.pointers._publish(replace(guard,operation_status="confirmed",result_id=record_id))
            return WriteOutcome(OperationStatus.CONFIRMED,operation_id,(record_id,))
