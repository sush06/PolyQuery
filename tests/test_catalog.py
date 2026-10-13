import json
import unittest

from polyquery.backends import UnsupportedPlan
from polyquery.engine import Engine
from polyquery.planner import Plan


class CatalogTests(unittest.TestCase):
    def test_serialized_plan_roundtrip_and_bad_field_types(self):
        plan = Plan('sql','count',{'department':'Research'},'count research',{'sql':1})
        self.assertEqual(Plan.from_dict(json.loads(json.dumps(plan.to_dict()))),plan)
        for value in [{'backend':'sql'}, {'backend':[], 'operation':'count'},
                      {'backend':'sql','operation':'count','scores':{'sql':True}},
                      {'backend':'sql','operation':'count','code':'unsafe'}]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                Plan.from_dict(value)

    def test_source_capabilities_parameters_and_domains(self):
        with Engine() as engine:
            self.assertEqual(engine.catalog.capable_backends('median'),('code',))
            for plan in [Plan('sql','median'),Plan('graph','shortest_path',{'source':'Alice'}),
                         Plan('sql','count',{'department':12}),Plan('sql','count',{'department':'HR'}),
                         Plan('code','correlation',{'department':'Sales'}),Plan('abstain','count')]:
                with self.subTest(plan=plan),self.assertRaises(UnsupportedPlan):
                    engine.catalog.validate_plan(plan)
            catalog = json.loads(json.dumps(engine.catalog.to_dict()))
            self.assertEqual(catalog['schema']['entities']['employee']['key'],['name'])
            self.assertEqual(catalog['cost_catalog']['status'],'unmeasured')

    def test_result_contract_rejects_wrong_shape_identity_and_edges(self):
        with Engine() as engine:
            for plan,value in [(Plan('sql','count'),True),(Plan('sql','count'),-1),
                               (Plan('sql','average_salary'),float('nan')),
                               (Plan('sql','list'),[{'name':'Alice'}]),
                               (Plan('graph','shortest_path',{'source':'Bob','target':'Frank'}),['Bob','Frank'])]:
                with self.subTest(value=value),self.assertRaises(UnsupportedPlan):
                    engine.catalog.validate_result(plan,{'value':value})
            engine.catalog.validate_result(Plan('sql','average_salary'),{'value':None})
            engine.catalog.validate_result(Plan('graph','shortest_path',{'source':'Alice','target':'Alice'}),{'value':['Alice']})

    def test_engine_enforces_contract_before_backend_execution(self):
        class BadPlanner:
            def plan(self,question):
                return Plan('sql','count',{'department':True})
        with Engine(BadPlanner()) as engine:
            with self.assertRaises(UnsupportedPlan):
                engine.query('irrelevant')
        with Engine() as engine:
            engine.backends['sql'].execute = lambda plan: {'value':'six'}
            with self.assertRaises(UnsupportedPlan):
                engine.query('How many employees?')


if __name__ == '__main__':
    unittest.main()
