from ..domain.errors import ErrorCode, MemoryError


def _missing():
    raise MemoryError(ErrorCode.NOT_CONFIGURED)


class UnconfiguredExperienceRepository:
    def search(self,scope,query,limit):_missing()
    def read(self,scope,record_id):_missing()
    def exact(self,scope,experience_id):_missing()
    def write(self,scope,draft,record_id,expected_fingerprint,operation_id):_missing()
    def retry(self,scope,operation_id,candidate,request_hash):_missing()
    def verify_write_scope(self,scope,record_id):_missing()


class UnconfiguredControlStateRepository:
    def read(self,context,record_id,kind):_missing()
    def save(self,scope,kind,key,value,operation_id):_missing()
    def write(self,context,kind,key,value,operation_id):_missing()
    def find(self,context,kind,key):_missing()


class UnconfiguredSessionRuntime:
    def resolve_scope(self,cwd):_missing()
    def bind_session(self,scope,session_id):_missing()
