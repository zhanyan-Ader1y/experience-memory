import json
import unittest
from tests.fixtures.workflow import Workflow
from experience_memory.application.services import MemoryApplicationService,RetentionApplicationService
from experience_memory.interfaces.mcp_server import create_server
from experience_memory.infrastructure.host_context import request_host,current_host


class MCPToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_returns_real_dtos_for_write_read_use_and_empty_search(self):
        f=Workflow();self.addCleanup(f.close)
        memory=MemoryApplicationService(f.experiences,f.controls,None,binding=f.binding,host_source=lambda:f.host)
        retention=RetentionApplicationService(f.experiences,f.controls,None,binding=f.binding,host_source=lambda:f.host)
        server=create_server(memory,retention)
        async def call(name,args):
            result=await server.call_tool(name,dict(session_handle=f.handle,**args))
            self.assertFalse(result.is_error,result.content[0].text)
            return json.loads(result.content[0].text)
        self.assertEqual((await call('memory_search',{'query':'nothing'}))['records'],[])
        saved=await call('memory_write',{'action':'upsert','candidate':f.candidate(),'operation_id':'create'})
        receipt=await call('memory_read',{'record_id':saved['record_id']})
        adopted=await call('memory_use',{'receipt_id':receipt['receipt_id'],'priority':'P1','reason':'supports test','operation_id':'use'})
        self.assertEqual(adopted['entry']['last_used_seq'],1)
        self.assertEqual((await call('memory_status',{}))['u_cursor'],1)
        tools=await server.list_tools()
        for tool in tools:self.assertNotIn('ctx',tool.input_schema.get('properties',{}))

    async def test_transport_metadata_is_per_call_and_missing_or_other_session_is_rejected(self):
        f=Workflow();self.addCleanup(f.close)
        memory=MemoryApplicationService(f.experiences,f.controls,None,binding=f.binding,host_source=current_host)
        # Direct context test ensures metadata isn't a process-global cached identity.
        with request_host({'experience_memory_host':{'cwd':str(f.host.cwd),'session_id':f.host.session_id}},'host',verified_contract=True):
            self.assertTrue(memory.status(f.handle)['succeeded'])
        with self.assertRaises(Exception):memory.status(f.handle)
        with request_host({'experience_memory_host':{'cwd':str(f.host.cwd),'session_id':'other'}},'host',verified_contract=True):
            with self.assertRaises(Exception):memory.status(f.handle)
