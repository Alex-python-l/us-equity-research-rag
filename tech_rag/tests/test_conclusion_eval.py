import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class ConclusionEvalTests(unittest.TestCase):
    def test_comparison_uses_final_conclusions_and_flags_unsupported_gap(self):
        from evaluation.conclusion_eval import compare_pair
        a={'final_conclusion':'部署仍待合同确认','expectation_gap':'未知'}
        b={'final_conclusion':'方向待核','expectation_gap':'无法确认严格事前预期，但可确认的预期差仅为方向性'}
        p=compare_pair('NVDA','2026-10-22T10:00:00Z',a,b)
        self.assertEqual(p['winner'],'not_determined')
        self.assertIn('unsupported_expectation_gap',p['B']['issues'])
        self.assertEqual(p['A']['final_conclusion'],a['final_conclusion'])

    def test_missing_arm_never_counts_as_a_completed_pair(self):
        from evaluation.conclusion_eval import compare_pair
        p=compare_pair('MU','2026-10-22T10:00:00Z',{'final_conclusion':'未知'},None)
        self.assertEqual(p['status'],'incomplete_pair');self.assertIsNone(p['B'])

    def test_historical_replay_refuses_later_acquisition_and_mismatched_memory(self):
        from evaluation.conclusion_eval import replay_cell
        c={'ticker':'MU','analysis_time':'2026-10-20T10:00:00Z','A':{'final_conclusion':'未知'},'B':{'final_conclusion':'未知'},
           'evidence':[{'published_at':'2026-10-19T10:00:00Z','acquired_at':'2026-10-21T10:00:00Z'}],
           'memory_A':[],'memory_B':[]}
        self.assertEqual(replay_cell(c)['status'],'rejected_temporal_leakage')
        c['evidence'][0]['acquired_at']='2026-10-19T11:00:00Z';c['memory_B']=[{'lesson':'later'}]
        self.assertEqual(replay_cell(c)['status'],'rejected_memory_mismatch')

    def test_price_outcome_requires_exact_shared_window_and_program_arithmetic(self):
        from evaluation.conclusion_eval import outcome_metrics
        o={'entry_date':'2026-10-20','exit_date':'2026-10-22','stock':[100,110],'benchmark':[200,210]}
        got=outcome_metrics(o)
        self.assertAlmostEqual(got['relative_return_pct'],5)
        self.assertEqual(got['causal_attribution'],'unknown')
        self.assertIsNone(outcome_metrics({'stock':[100,110]})['relative_return_pct'])

    def test_live_ab_freezes_independent_memory_copy_for_each_arm(self):
        from evaluation.run_us_ab import run_pairs
        class Memory:
            def context(self,*a,**kw):return [{'lesson':'shared before experiment'}]
        seen=[]
        def runner(ticker,**kw):
            seen.append(json.loads(json.dumps(kw['memory_snapshot'])))
            kw['memory_snapshot'].append({'lesson':'must not leak to other arm'})
            return {'final':{'final_conclusion':'条件性研究'}}
        with tempfile.TemporaryDirectory() as td:
            result=run_pairs(['MU'],td,paid_confirmed=True,runner=runner,memory=Memory())
            self.assertEqual(seen[0],seen[1]);self.assertEqual(result['complete_pairs'],1)

    def test_valid_archived_replay_is_not_mislabeled_as_new_backtest_generation(self):
        from evaluation.conclusion_eval import replay_cell
        cell={'ticker':'MU','analysis_time':'2026-10-20T10:00:00Z','A':{'final_conclusion':'条件A'},'B':{'final_conclusion':'条件B'},
            'evidence':[{'version_id':'immutable-1','published_at':'2026-10-19T10:00:00Z','acquired_at':'2026-10-19T11:00:00Z'}]}
        result=replay_cell(cell)
        self.assertEqual(result['status'],'archived_conclusions_replayed');self.assertEqual(result['model_calls'],0)
        self.assertIsNone(result['win_rate'])

    def test_future_prior_expectations_cannot_bypass_historical_gate(self):
        from evaluation.conclusion_eval import replay_cell
        cell={'ticker':'MU','analysis_time':'2026-10-20T10:00:00Z','first_disclosed_at':'2026-10-20T09:00:00Z',
            'A':{'final_conclusion':'未知'},'B':{'final_conclusion':'未知','expectation_gap':'可确认的预期差仅为方向性'},
            'evidence':[{'version_id':'v','published_at':'2026-10-19T10:00:00Z','acquired_at':'2026-10-19T11:00:00Z'}],
            'prior_expectations':[{'version_id':'future','published_at':'2026-10-21T10:00:00Z','acquired_at':'2026-10-21T11:00:00Z'}]}
        self.assertEqual(replay_cell(cell)['status'],'rejected_prior_expectations_vintage')

if __name__=='__main__':unittest.main()
