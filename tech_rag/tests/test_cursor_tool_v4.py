"""A cursor-only model call retains the host's exact query and time binding."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


class CursorToolTests(unittest.TestCase):
    def test_actual_registered_tool_accepts_cursor_without_a_new_question(self):
        from integration.runtime import EvidenceRuntime
        from integration.governance import new_shared_state
        rt=EvidenceRuntime('MU','HBM4','2026-10-18T15:38:11+00:00',False,use_dense=False)
        tool=next(t for t in rt.build_tools() if t.name=='company_research')
        state=new_shared_state(rt.analysis_time,False)
        state.update(tool_trace=[],evidence_packets=[])
        first=tool.invoke({'type':'tool_call','name':tool.name,'id':'first',
                           'args':{'state':state,'question':'HBM4 36GB 48GB samples'}})
        state.update(first.update)
        cursor=state['evidence_packets'][0]['pagination']['next_cursor']
        self.assertTrue(cursor)
        second=tool.invoke({'type':'tool_call','name':tool.name,'id':'second',
                            'args':{'state':state,'cursor':cursor}})
        trace=second.update['tool_trace'][-1]
        self.assertEqual(trace['question'],'HBM4 36GB 48GB samples')
        self.assertNotEqual(trace.get('reason'),'cursor_not_from_same_verified_query')
        self.assertEqual(trace['analysis_time'],rt.analysis_time)


if __name__=='__main__':unittest.main()
