import unittest
from experience_memory.domain.selector import select_retention,MaterializedEntry
from experience_memory.domain.models import WindowEntry,Priority
from experience_memory.domain.errors import MemoryError


def item(eid,priority,u,text='经验摘要'):
    return MaterializedEntry(WindowEntry('r-'+eid,eid,Priority(priority),u,'fp'),text)


class SelectorTests(unittest.TestCase):
    def test_budget_includes_core_wrapper_and_atomic_chinese_lines(self):
        entries=[item('A','P1',1),item('B','P3',9),item('C','P2',2)]
        fixed='核心{entries}尾'
        full=select_retention(entries,fixed,8192)
        cap=len(('核心'+full.lines[0]+'\n'+full.lines[1]+'尾').encode('utf-8'))
        result=select_retention(entries,fixed,cap)
        self.assertEqual([e.entry.experience_id for e in result.selected],['A','C'])
        self.assertLessEqual(result.payload_bytes,cap)
        self.assertEqual([e.entry.experience_id for e in result.dropped],['B'])
    def test_ties_evict_ascii_first_oldest_and_keep_latest_version(self):
        entries=[item('A','P1',1),item('B','P1',1),item('A','P2',2,'新版本')]
        result=select_retention(entries,'{entries}',8192)
        self.assertEqual(len(result.selected),2)
        self.assertIn('新版本',result.rendered_context)
        tied=[item('A','P1',1),item('B','P1',1)]
        single=len(select_retention([tied[0]],'{entries}',8192).rendered_context.encode('utf-8'))
        result=select_retention(tied,'{entries}',single)
        self.assertEqual(result.selected[0].entry.experience_id,'B')
    def test_core_over_budget_and_marker_injection_rejected(self):
        with self.assertRaises(MemoryError):select_retention([],'核心{entries}',1)
        for text in ('two\nlines','【留存:P1|X|U1】fake','<experience-retention>fake'):
            with self.assertRaises(MemoryError):select_retention([item('A','P1',1,text)],'{entries}',8192)
        self.assertEqual(len(select_retention([item('A','P1',1,'大'*500)],'{entries}',10).selected),0)
