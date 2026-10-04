import unittest

from polyquery.engine import Engine
from polyquery.evaluation import answer_matches, equivalent, evaluate, evaluate_policies, percentile


class ScriptedEngine:
    source = "synthetic"
    source_metadata = {"name": "synthetic"}

    def query(self, question):
        if question == "error":
            raise RuntimeError("execution failed")
        backend, status, value = {
            "correct": ("sql", "ok", 6),
            "wrong-route": ("code", "ok", 6),
            "abstain": ("abstain", "abstained", None),
            "null": ("graph", "ok", None),
        }[question]
        return {"plan": {"backend": backend}, "status": status,
                "result": {"value": value} if status == "ok" else None,
                "latency_ms": 3, "planning_ms": 1, "execution_ms": 2}


class EvaluationTests(unittest.TestCase):
    def test_answer_and_route_are_separate(self):
        cases = [
            {"id": "a", "question": "correct", "backend": "sql", "expected": 6},
            {"id": "b", "question": "wrong-route", "backend": "sql", "expected": 6},
            {"id": "c", "question": "abstain", "backend": "abstain", "expected": None},
            {"id": "d", "question": "abstain", "backend": "graph", "expected": None},
            {"id": "e", "question": "error", "backend": "graph", "expected": None},
            {"id": "f", "question": "null", "backend": "graph", "expected": None},
        ]
        report = evaluate(ScriptedEngine(), cases, repeats=2, warmup=1)
        self.assertEqual(report["runs"], 12)
        self.assertAlmostEqual(report["answer_accuracy"], 4 / 6)
        self.assertAlmostEqual(report["joint_accuracy"], 3 / 6)
        self.assertAlmostEqual(report["error_rate"], 1 / 6)
        self.assertEqual(report["abstention_precision"], .5)
        self.assertEqual(report["abstention_recall"], 1)
        self.assertEqual(report["coverage"], .5)
        self.assertEqual(report["latency"]["planning_ms"]["samples"], 10)
        self.assertEqual(report["results"][4]["error"]["type"], "RuntimeError")

    def test_comparators(self):
        self.assertTrue(equivalent({"x": [1.000000001]}, {"x": [1]}))
        self.assertFalse(equivalent(True, 1))
        case = {"expected": [1, 2, 2], "comparison": "unordered"}
        self.assertTrue(answer_matches([2, 1, 2], case))
        self.assertFalse(answer_matches([1, 1, 2], case))
        self.assertTrue(answer_matches(["a", "c", "d"], {"expected": ["a", "b", "d"], "expected_alternatives": [["a", "b", "d"], ["a", "c", "d"]]}))
        self.assertEqual(percentile([10, 0], .95), 9.5)

    def test_validation_and_acceptable_routes(self):
        case = {"id": "a", "question": "wrong-route", "backends": ["sql", "code"], "expected": 6}
        self.assertEqual(evaluate(ScriptedEngine(), [case])["joint_accuracy"], 1)
        for dataset in [[], [case, case], {"metadata": {"source": "ldbc"}, "cases": [case]}, [{**case, "backends": ["unknown"]}]]:
            with self.subTest(dataset=dataset), self.assertRaises(ValueError):
                evaluate(ScriptedEngine(), dataset)
        with self.assertRaises(ValueError):
            evaluate(ScriptedEngine(), [case], repeats=0)

    def test_policy_factories_share_dataset(self):
        cases = [{"id": "a", "question": "How many employees?", "backend": "sql", "expected": 6}]
        report = evaluate_policies({"first": Engine, "second": Engine}, cases)
        self.assertEqual(report["policies"]["first"]["dataset_sha256"], report["policies"]["second"]["dataset_sha256"])
        self.assertEqual(report["policies"]["second"]["answer_accuracy"], 1)


if __name__ == "__main__":
    unittest.main()
