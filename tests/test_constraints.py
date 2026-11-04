import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from polyquery.engine import Engine
from polyquery.planner import Plan
from polyquery.constraints import check_constraints
from polyquery.evaluation import evaluate


class FixedPlanner:
    def __init__(self, plan):
        self.value=plan

    def plan(self, question):
        return self.value


class ConstraintTests(unittest.TestCase):
    def test_dropped_filters_and_statistics_never_execute(self):
        cases=[('Count employees excluding Sales',Plan('sql','count')),
               ('Average salary above 100000',Plan('sql','average_salary')),
               ('Mean salary in Research',Plan('sql','average_salary')),
               ('How many employees?',Plan('sql','count',{'department':'Research'})),
               ('Median salary',Plan('sql','average_salary')),
               ('Top three employees',Plan('sql','list')),
               ('Shortest path from Bob to Frank',Plan('graph','shortest_path',{'source':'Alice','target':'Frank'}))]
        for question,plan in cases:
            with self.subTest(question=question),Engine(FixedPlanner(plan)) as engine:
                backend=engine.backends[plan.backend]
                backend.execute=Mock()
                response=engine.query(question)
                self.assertEqual(response['status'],'abstained')
                self.assertFalse(response['constraint_check']['allowed'])
                self.assertEqual(response['proposed_plan']['operation'],plan.operation)
                backend.execute.assert_not_called()

    def test_supported_requests_are_preserved(self):
        with Engine() as engine:
            for question in ['Mean salary in Research','Total sales','Correlation between salary and experience',
                             'Shortest path from Bob to Frank','Who reports to Alice?']:
                with self.subTest(question=question):
                    self.assertEqual(engine.query(question)['status'],'ok')

    def test_tabular_constraints_and_valid_grouping(self):
        catalog=SimpleNamespace(source='chinook')
        self.assertTrue(check_constraints('Revenue by billing country',Plan('sql','revenue_by_country'),catalog).allowed)
        self.assertFalse(check_constraints('Average invoice total by billing country',Plan('sql','revenue_by_country'),catalog).allowed)
        self.assertFalse(check_constraints('Total invoice revenue from USA',Plan('sql','revenue'),catalog).allowed)
        taxi=SimpleNamespace(source='taxi')
        for question in ['Average fare excluding negative fares','Average fare for trips with two passengers',
                         'Total fare after 2020','Average positive fares']:
            self.assertFalse(check_constraints(question,Plan('sql','mean_fare'),taxi).allowed)
        self.assertTrue(check_constraints('How many negative fares?',Plan('sql','negative_fares'),taxi).allowed)
        self.assertFalse(check_constraints('How many positive fares?',Plan('sql','negative_fares'),taxi).allowed)
        self.assertFalse(check_constraints('How many fares?',Plan('sql','negative_fares'),taxi).allowed)

    def test_ldbc_endpoint_identity_and_extra_numbers(self):
        catalog=SimpleNamespace(source='ldbc')
        plan=Plan('graph','shortest_path',{'source':'933','target':'1129'})
        self.assertTrue(check_constraints('Shortest path from 933 to 1129',plan,catalog).allowed)
        self.assertFalse(check_constraints('Shortest path from 1129 to 933',plan,catalog).allowed)
        self.assertFalse(check_constraints('Shortest path from 933 to 1129 within 3 hops',plan,catalog).allowed)

    def test_guard_abstention_does_not_count_as_a_correct_null_answer(self):
        with Engine(FixedPlanner(Plan('sql','average_salary'))) as engine:
            report=evaluate(engine,[{'id':'a','question':'Average salary excluding all employees',
                                    'backend':'sql','expected':None}])
            self.assertEqual(report['answer_accuracy'],0)
            self.assertFalse(report['results'][0]['constraint_check']['allowed'])


if __name__=='__main__':
    unittest.main()
