import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from polyquery.label_oracle import LabelOracle
from polyquery.router_comparison import compare_routers, task_input
from polyquery.engine import Engine
from test_llm import client_for, payload


class RouterComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parents[1]/'benchmarks/constraint-labels-v2'
        cls.pool=json.loads((root/'labels.json').read_text())
        cls.splits=json.loads((root/'splits.json').read_text())

    def family(self,name):
        return copy.deepcopy(next(c for c in self.pool['cases'] if c['family_id']==name))

    def test_policy_input_excludes_annotations(self):
        case=self.family('country-having')
        case['context']['unexpected']='secret gold'
        text=task_input(case)
        self.assertNotIn('intended_semantics',text)
        self.assertNotIn('secret gold',text)
        self.assertIn('group_policy',text)
        self.assertIn('date_literal_boundary',task_input(self.family('pickup-before')))

    def test_oracle_null_and_boolean_reference(self):
        oracle=LabelOracle('synthetic',{})
        try:
            oracle.db.execute('DELETE FROM employees')
            oracle.db.executemany('INSERT INTO employees VALUES (?,?,?,?)',
                                  [('A',None,120000,None),('B','Sales',90000,6)])
            case=self.family('not-both')
            self.assertTrue(oracle.score(case,{'status':'ok','result':{'value':90000}}))
            self.assertFalse(oracle.score(case,{'status':'abstained'}))
            case=self.family('employee-limit-only')
            rows=[dict(row) for row in oracle.db.execute('SELECT * FROM employees')]
            self.assertTrue(oracle.score(case,{'status':'ok','result':{'value':rows[::-1]}}))
        finally:
            oracle.close()

    def test_reference_group_filter_omits_empty_and_preserves_null(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.sqlite'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE Invoice(Total REAL,BillingCountry TEXT,InvoiceDate TEXT)')
            db.executemany('INSERT INTO Invoice VALUES (?,?,?)',[(3,'USA','2009-01-01'),(8,'UK','2010-01-01'),(None,None,'2009-01-01')])
            db.commit(); db.close()
            oracle=LabelOracle('chinook',{'data_path':path})
            try:
                case=self.family('invoice-row-filter-before-group')
                self.assertTrue(oracle.score(case,{'status':'ok','result':{'value':[{'country':'UK','revenue':8}]}}))
                self.assertFalse(oracle.score(case,{'status':'ok','result':{'value':[{'country':'UK','revenue':8},{'country':'USA','revenue':0}]}}))
                case=self.family('country-group')
                self.assertTrue(oracle.score(case,{'status':'ok','result':{'value':[{'country':None,'revenue':None},{'country':'USA','revenue':3},{'country':'UK','revenue':8}]}}))
            finally:
                oracle.close()

    def test_taxi_reference_uses_full_rows_and_midnight_boundaries(self):
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest('Optional Arrow dependency unavailable')
        from datetime import datetime
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'trips.parquet'
            pq.write_table(pa.table({'fare_amount':[-2.,0.,10.,None],
                                    'trip_distance':[1.,2.,3.,4.],
                                    'tpep_pickup_datetime':[datetime(2026,1,1),datetime(2026,1,31,23,59,59),datetime(2026,2,1),None]}),path)
            oracle=LabelOracle('taxi',{'data_path':path})
            try:
                def correct(family,value):
                    return oracle.score(self.family(family),{'status':'ok','result':{'value':value}})
                self.assertTrue(correct('fare-exclude-negative',5.))
                self.assertTrue(correct('pickup-before',2))
                self.assertTrue(correct('pickup-on-or-after',1))
                self.assertTrue(correct('pickup-month-interval',2))
                self.assertTrue(correct('fare-descending',[10.,0.,-2.]))
                self.assertFalse(correct('fare-descending',[10.,0.]))
            finally:
                oracle.close()

    def test_supported_llm_plan_can_pass_both_gold_and_answer_scoring(self):
        cases=[c for c in self.pool['cases'] if c['family_id']=='country-group']
        splits=copy.deepcopy(self.splits)
        splits['case_assignments']={c['id']:self.splits['case_assignments'][c['id']] for c in cases}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'chinook.sqlite'
            db=sqlite3.connect(path)
            db.executescript('CREATE TABLE Invoice(Total REAL,BillingCountry TEXT); CREATE TABLE Track(GenreId INTEGER); CREATE TABLE Genre(GenreId INTEGER,Name TEXT); CREATE TABLE Customer(CustomerId INTEGER); INSERT INTO Invoice VALUES (10,"USA"),(5,"UK");')
            db.commit(); db.close()
            def factory(**kwargs):
                if kwargs.get('planner_kind')=='llm':
                    kwargs['llm_client']=client_for(payload('sql','revenue_by_country'))
                return Engine(**kwargs)
            report=compare_routers({'cases':cases},splits,{'chinook':{'data_path':path}},model='mock',engine_factory=factory)
            summary=report['summaries']['llm']
            self.assertEqual(summary['proposed_constraint_preservation'],1)
            self.assertGreater(summary['answer_accuracy'],0)

    def test_paired_run_uses_identical_inputs_and_keeps_errors_in_denominator(self):
        cases=[c for c in self.pool['cases'] if c['family_id']=='salary-strict']
        pool={'cases':cases}
        splits=copy.deepcopy(self.splits)
        splits['case_assignments']={c['id']:self.splits['case_assignments'][c['id']] for c in cases}
        requests=[]
        def factory(**kwargs):
            if kwargs.get('planner_kind')=='llm':
                kwargs['llm_client']=client_for(error=RuntimeError('private provider message'))
            engine=Engine(**kwargs)
            original=engine.query
            def query(question,**context):
                requests.append((kwargs.get('planner_kind','keyword'),json.dumps([question,context],sort_keys=True)))
                return original(question,**context)
            engine.query=query
            return engine
        report=compare_routers(pool,splits,{'synthetic':{}},model='mock-model',engine_factory=factory)
        self.assertTrue(report['complete_comparison'])
        self.assertEqual(report['summaries']['llm']['error_rate'],1)
        self.assertEqual(report['summaries']['llm']['answer_accuracy'],0)
        self.assertIsNone(report['summaries']['llm']['estimated_api_usd'])
        self.assertEqual(report['paired_answers']['samples'],4)
        self.assertEqual(sorted(q for name,q in requests if name=='keyword'),sorted(q for name,q in requests if name=='llm'))
        self.assertNotIn('private provider message',json.dumps(report))

    def test_llm_guard_does_not_substitute_for_gold_scoring(self):
        cases=[c for c in self.pool['cases'] if c['family_id']=='salary-strict']
        splits=copy.deepcopy(self.splits)
        splits['case_assignments']={c['id']:self.splits['case_assignments'][c['id']] for c in cases}
        def factory(**kwargs):
            if kwargs.get('planner_kind')=='llm':
                kwargs['llm_client']=client_for(payload('code','mean'))
            return Engine(**kwargs)
        report=compare_routers({'cases':cases},splits,{'synthetic':{}},model='mock',engine_factory=factory,input_rate=1,output_rate=2)
        summary=report['summaries']['llm']
        self.assertEqual(summary['proposed_constraint_preservation'],0)
        self.assertEqual(summary['api_calls'],4)
        self.assertAlmostEqual(summary['estimated_api_usd'],4*(100+40)/1_000_000)


if __name__=='__main__':
    unittest.main()
