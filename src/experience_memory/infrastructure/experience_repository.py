from dataclasses import asdict, replace
from ..domain.models import MemoryRecord, MemoryStatus, require_experience_id
from ..domain.integrity import fingerprint
from ..domain.errors import MemoryError, ErrorCode
from .mem0_client import api_id


class Mem0ExperienceRepository:
    def __init__(self,store): self.store=store

    def _decode(self,scope,row):
        import json
        try:
            self.store._scope(scope,row)
            if row['metadata']['record_kind']!='project_experience': raise ValueError()
            value=json.loads(row['memory'])
            if value.get('schema_version')!=1: raise ValueError()
            return MemoryRecord(row['id'],value['experience_id'],MemoryStatus(value['status']),
                value['body'],value['retention_text'],fingerprint(value),value['metadata'])
        except (KeyError,ValueError,TypeError): raise MemoryError(ErrorCode.NEEDS_REVIEW) from None

    def scopes(self,scope):
        return (scope,)+tuple(replace(scope,project_id=value,legacy_project_ids=()) for value in scope.legacy_project_ids)

    def search(self,scope,query,limit):
        if not isinstance(query,str) or not query.strip() or len(query)>8192 or type(limit) is not int or not 1<=limit<=20:
            raise MemoryError(ErrorCode.INVALID_INPUT)
        records=[]
        for target in self.scopes(scope):
            response=self.store.client.request('POST','/v3/memories/search/',{
                'query':query,'top_k':limit,'filters':self.store.filters(target,'project_experience')})
            if not isinstance(response,dict) or type(response.get('results')) is not list:
                raise MemoryError(ErrorCode.REMOTE_FAILURE)
            for row in response['results']:
                # Missing schema is visible as review-needed, never silently active.
                try:
                    records.append(self._decode(target,row))
                except MemoryError as error:
                    if error.code!=ErrorCode.NEEDS_REVIEW: raise
                    self.store._scope(target,row)
                    records.append({'record_id':row.get('id'),'status':'needs_review'})
        return tuple(records[:limit])

    def read(self,scope,record_id):
        row=self.store.client.request('GET','/v1/memories/'+api_id(record_id)+'/')
        for target in self.scopes(scope):
            if row.get('app_id')==target.app_id and row.get('agent_id')==target.project_id:
                return self._decode(target,row)
        raise MemoryError(ErrorCode.SCOPE_MISMATCH)

    def exact(self,scope,experience_id):
        require_experience_id(experience_id)
        rows=[]
        for target in self.scopes(scope):
            row=self.store.find(target,'project_experience',experience_id)
            if row: rows.append(self._decode(target,row))
        if len(rows)>1: raise MemoryError(ErrorCode.CONFLICT)
        return rows[0] if rows else None

    def write(self,scope,draft,record_id,expected_fingerprint,operation_id):
        return self.store.save(scope,'project_experience',draft['experience_id'],draft,operation_id,
                               record_id=record_id,expected_fingerprint=expected_fingerprint)

    def verify_write_scope(self,scope,record_id):
        self.store.raw(scope,record_id)

    def retry(self,scope,operation_id,candidate,request_hash):
        import json
        journal=self.store.operation(scope,operation_id)
        if journal is None:return None
        if journal.client_fingerprint!=request_hash:raise MemoryError(ErrorCode.CONFLICT)
        if journal.operation_status=='failed':
            # Ask the store to repair/finish the same guard terminal status without writing remotely.
            self.store.finish_failed(scope,operation_id,'project_experience',candidate['experience_id'])
            return {'status':'failed','operation_id':operation_id,'error_code':journal.error_code}
        rows=self.store.rows(scope,'project_experience',operation_id=operation_id)
        if len(rows)>1:raise MemoryError(ErrorCode.CONFLICT)
        if not rows:
            if journal.event_id:
                outcome=self.store.event_outcome(operation_id,journal.event_id)
                return {'status':outcome.status.value if outcome.status.value!='confirmed' else 'unknown',
                        'operation_id':operation_id,'event_id':journal.event_id}
            return {'status':'unknown','operation_id':operation_id}
        record=self._decode(scope,rows[0]);value=json.loads(rows[0]['memory'])
        if value.get('operation_request')!=request_hash:raise MemoryError(ErrorCode.CONFLICT)
        outcome=self.store.save(scope,'project_experience',record.experience_id,value,operation_id,
            record_id=candidate.get('record_id'),expected_fingerprint=candidate.get('expected_fingerprint'))
        return {'status':outcome.status.value,'succeeded':outcome.status.value=='confirmed','operation_id':operation_id,
                'record_id':record.record_id if outcome.status.value=='confirmed' else None,
                'fingerprint':record.fingerprint if outcome.status.value=='confirmed' else None,'event_id':outcome.event_id}
