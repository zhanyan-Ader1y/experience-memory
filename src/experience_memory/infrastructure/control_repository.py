"""Immutable remote control envelopes, scoped to a confirmed root session."""
from ..domain.errors import MemoryError, ErrorCode
from ..domain.ports import OperationStatus


class Mem0ControlRepository:
    def __init__(self, store): self.store=store

    def save(self, scope, kind, key, value, operation_id):
        outcome=self.store.save(scope,kind,key,value,operation_id)
        if outcome.status==OperationStatus.FAILED:
            raise MemoryError(ErrorCode(outcome.error_code) if outcome.error_code else ErrorCode.REMOTE_FAILURE)
        if outcome.status!=OperationStatus.CONFIRMED:
            raise MemoryError(ErrorCode.OPERATION_PENDING if outcome.status==OperationStatus.PENDING else
                              ErrorCode.OPERATION_UNKNOWN if outcome.status==OperationStatus.UNKNOWN else ErrorCode.REMOTE_FAILURE)
        return outcome.record_ids[0]

    def read(self, ctx, record_id, kind):
        value=self.store.get(ctx.scope,record_id,kind)
        if value.get('session_handle')!=ctx.session_handle:
            raise MemoryError(ErrorCode.SESSION_INVALID)
        return value

    def write(self, ctx, kind, key, value, operation_id):
        return self.save(ctx.scope,kind,key,dict(value,session_handle=ctx.session_handle),operation_id)

    def find(self, ctx, kind, key):
        row=self.store.find(ctx.scope,kind,key)
        return None if row is None else (row['id'],self.read(ctx,row['id'],kind))
