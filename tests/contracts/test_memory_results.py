import json
import unittest
from experience_memory.interfaces.mcp_server import _invoke


class ResultTests(unittest.TestCase):
    def test_successful_empty_search_is_not_an_error(self):
        result=_invoke(lambda: {'succeeded':True,'records':[]})
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text),{'succeeded':True,'records':[]})

    def test_pending_is_not_confirmed(self):
        result=_invoke(lambda: {'status':'pending','operation_id':'same'})
        self.assertFalse(json.loads(result.content[0].text).get('succeeded',False))
