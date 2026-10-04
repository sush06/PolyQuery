import json
from pathlib import Path
import sqlite3
import unittest

from polyquery import Engine
from polyquery.backends import ExpressionInterpreter, UnsupportedPlan
from polyquery.evaluation import evaluate
from polyquery.planner import Plan


class PolyQueryTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()

    def tearDown(self):
        self.engine.close()

    def test_labeled_development_cases(self):
        cases = json.loads((Path(__file__).parents[1] / "benchmarks/demo.json").read_text())
        report = evaluate(self.engine, cases)
        for result in report["results"]:
            with self.subTest(case=result["id"]):
                self.assertTrue(result["joint_correct"], result)

    def test_sql_write_authorization(self):
        for statement in ["DELETE FROM employees", "DROP TABLE sales", "ATTACH DATABASE ':memory:' AS extra", "PRAGMA query_only=OFF"]:
            with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                self.engine.backends["sql"].db.execute(statement)
        self.assertEqual(self.engine.query("How many employees?")["result"]["value"], 6)

    def test_code_rejects_escape_attempts(self):
        for expression in ["__import__('os').system('id')", "salary.__class__", "median([1,2])", "median(salary, experience)", "median(salary, key=experience)", "[x for x in salary]", "median(salary * 999999999)"]:
            with self.subTest(expression=expression), self.assertRaises(UnsupportedPlan):
                ExpressionInterpreter().run(expression, {"salary": [1, 2], "experience": [2, 3]})

    def test_external_plans_are_validated(self):
        plans = [Plan("sql", "drop"), Plan("sql", "count", {"department": "HR"}), Plan("graph", "shortest_path", {"source": "Alice", "target": "Unknown"}), Plan("code", "median", {"code": "print(1)"})]
        for plan in plans:
            with self.subTest(plan=plan), self.assertRaises(UnsupportedPlan):
                self.engine.backends[plan.backend].execute(plan)

    def test_graph_direction_and_trivial_path(self):
        graph = self.engine.backends["graph"]
        self.assertEqual(graph.execute(Plan("graph", "descendants", {"source": "Dan"}))["value"], [])
        self.assertEqual(graph.execute(Plan("graph", "shortest_path", {"source": "Alice", "target": "Alice"}))["value"], ["Alice"])

    def test_empty_evaluation(self):
        with self.assertRaises(ValueError):
            evaluate(self.engine, [])

    def test_planner_extension(self):
        class CustomPlanner:
            def plan(self, question):
                return Plan("sql", "count", {"department": "Research"})
        with Engine(CustomPlanner()) as engine:
            self.assertEqual(engine.query("custom input")["result"]["value"], 2)


if __name__ == "__main__":
    unittest.main()

