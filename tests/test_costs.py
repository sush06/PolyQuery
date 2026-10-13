import json
import unittest
from pathlib import Path

from polyquery.costs import CostPlanner, calibrate, verify_split_directory
from polyquery.evaluation import evaluate, source_fingerprint
from polyquery.planner import Plan


class StubBackend:
    def __init__(self, value):
        self.value = value

    def execute(self, plan):
        return {'value': self.value}


class StubPlanner:
    def plan(self, question):
        return Plan('abstain', 'unsupported') if question == 'unknown' else Plan('sql', question)


class StubEngine:
    source = 'synthetic'
    source_metadata = {'name': 'synthetic'}

    def __init__(self):
        self.backends = {'sql': StubBackend(6), 'code': StubBackend(7)}

    def query(self, question):
        return {'status': 'ok', 'plan': {'backend': 'sql'}, 'result': {'value': 6},
                'planning_ms': 1, 'execution_ms': 3, 'latency_ms': 4}


class CostTests(unittest.TestCase):
    def dataset(self, engine, split='train'):
        return {'metadata': {'source': 'synthetic', 'split': split, 'source_hashes': source_fingerprint(engine)},
                'cases': [{'id': 'a', 'question': 'count', 'expected': 6, 'backends': ['sql', 'code'],
                           'reference_plan': {'operation': 'count'}}]}

    def test_training_only_and_wrong_answer_exclusion(self):
        engine = StubEngine()
        with self.assertRaises(ValueError):
            calibrate(engine, self.dataset(engine, 'test'))
        profile = calibrate(engine, self.dataset(engine), repeats=2, warmup=1)
        self.assertEqual(set(profile['operation_costs_ms']['count']), {'sql'})
        self.assertEqual(len(profile['measurements'][0]['samples_ms']), 2)

    def test_lookup_fallback_abstention_and_hash_guard(self):
        engine = StubEngine()
        profile = {'profile_version': 1, 'source': 'synthetic', 'source_hashes': source_fingerprint(engine),
                   'operation_costs_ms': {'count': {'sql': 2, 'code': 1}}, 'fallback_costs_ms': {'sql': 1, 'code': 3}}
        planner = CostPlanner(StubPlanner(), profile, 'synthetic', source_fingerprint(engine), engine.backends)
        self.assertEqual(planner.plan('count').backend, 'code')
        self.assertEqual(planner.plan('new_operation').backend, 'sql')
        self.assertEqual(planner.plan('unknown').backend, 'abstain')
        engine.backends['code'].operations = {'other'}
        self.assertEqual(planner.plan('count').backend, 'sql')
        with self.assertRaises(ValueError):
            CostPlanner(StubPlanner(), profile, 'taxi', {}, engine.backends)

    def test_oracle_wrong_strategy_and_missing_answers_not_rewarded(self):
        engine = StubEngine()
        report = evaluate(engine, self.dataset(engine), oracle_repeats=2)
        self.assertEqual(set(report['results'][0]['oracle_costs_ms']), {'sql'})
        self.assertEqual(report['regret']['samples'], 1)
        engine.query = lambda q: {'status': 'abstained', 'plan': {'backend': 'abstain'}, 'result': None,
                                  'planning_ms': 1, 'execution_ms': 0, 'latency_ms': 1}
        report = evaluate(engine, self.dataset(engine), oracle_repeats=1)
        self.assertEqual(report['regret']['samples'], 0)
        self.assertEqual(report['answerable_accuracy'], 0)

    def test_frozen_challenge_hashes_and_families(self):
        root = Path(__file__).parents[1] / 'benchmarks/challenge-v1'
        manifest = verify_split_directory(root)
        self.assertEqual(len(manifest['sources']), 3)
        for source, entries in manifest['sources'].items():
            for entry in entries:
                validate_data = json.loads((root / entry['file']).read_text())
                from polyquery.evaluation import validate_cases
                validate_cases(validate_data['cases'])


if __name__ == '__main__':
    unittest.main()
