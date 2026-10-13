"""Measured execution-cost lookup, fitted only on a declared training split."""
import hashlib
import json
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from .evaluation import answer_matches, source_fingerprint, validate_cases
from .planner import Plan


def calibrate(engine, dataset, *, repeats=5, warmup=1):
    metadata = dataset.get('metadata', {})
    if metadata.get('split') != 'train':
        raise ValueError('Cost calibration requires metadata.split=train')
    if type(repeats) is not int or repeats < 1 or type(warmup) is not int or warmup < 0:
        raise ValueError('Invalid calibration repeats/warmup')
    cases = dataset.get('cases')
    validate_cases(cases)
    fingerprint = source_fingerprint(engine)
    if metadata.get('source') != engine.source or metadata.get('source_hashes') != fingerprint:
        raise ValueError('Training data/source hashes do not match')
    measurements, estimates = [], {}
    graph = engine.backends.get('graph')
    for case in cases:
        reference = case.get('reference_plan')
        if not reference:
            raise ValueError('Calibration cases require reference_plan')
        operation = reference['operation']
        parameters = reference.get('parameters', {})
        for backend in case['backends']:
            samples, correct, errors = [], True, []
            for repetition in range(warmup + repeats):
                started = time.perf_counter()
                try:
                    value = engine.backends[backend].execute(Plan(backend, operation, parameters))['value']
                    elapsed = (time.perf_counter() - started) * 1000
                    success = answer_matches(value, case, graph)
                except Exception as error:
                    elapsed, success = (time.perf_counter() - started) * 1000, False
                    errors.append({'type': type(error).__name__, 'message': str(error)})
                correct = correct and success
                if repetition >= warmup:
                    samples.append(elapsed)
            measurements.append({'id': case['id'], 'operation': operation, 'backend': backend,
                                 'correct': correct, 'samples_ms': samples, 'errors': errors})
            if correct:
                estimates.setdefault(operation, {}).setdefault(backend, []).extend(samples)
    costs = {op: {backend: statistics.median(values) for backend, values in backends.items()}
             for op, backends in estimates.items()}
    defaults = {backend: statistics.median([v[backend] for v in costs.values() if backend in v])
                for backend in engine.backends if any(backend in v for v in costs.values())}
    if not defaults:
        raise ValueError('No correct strategy available for calibration')
    return {'profile_version': 1, 'source': engine.source, 'source_hashes': fingerprint,
            'train_dataset_sha256': hashlib.sha256(json.dumps(dataset, sort_keys=True).encode()).hexdigest(),
            'created_at': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
            'platform': platform.platform(), 'repeats': repeats, 'warmup': warmup,
            'operation_costs_ms': costs, 'fallback_costs_ms': defaults, 'measurements': measurements,
            'note': 'Execution-only medians from training reference plans; no validation/test answers or timings used. Unseen operations use backend-wide training medians; this can be inaccurate.'}


class CostPlanner:
    def __init__(self, planner, profile, source, fingerprint, backends, catalog=None):
        if profile.get('profile_version') != 1 or profile.get('source') != source or profile.get('source_hashes') != fingerprint:
            raise ValueError('Cost profile/source mismatch')
        defaults = profile.get('fallback_costs_ms', {})
        operations = profile.get('operation_costs_ms', {})
        import math
        for costs in [defaults, *operations.values()]:
            if not isinstance(costs, dict) or not costs or any(backend not in backends or type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0 for backend, cost in costs.items()):
                raise ValueError('Invalid cost profile estimates')
        if not defaults:
            raise ValueError('Cost profile requires fallback estimates')
        self.planner, self.profile = planner, profile
        self.backends = backends
        self.catalog = catalog

    def plan(self, question):
        plan = self.planner.plan(question)
        if plan.backend == 'abstain':
            return plan
        known = plan.operation in self.profile['operation_costs_ms']
        costs = self.profile['operation_costs_ms'].get(plan.operation, self.profile['fallback_costs_ms'])
        costs = {b: c for b, c in costs.items() if plan.operation in getattr(self.backends[b], 'operations', {plan.operation})}
        if self.catalog is not None:
            costs = {b:c for b,c in costs.items() if b in self.catalog.capable_backends(plan.operation)}
        if not costs:
            return Plan(plan.backend, plan.operation, plan.parameters, 'No measured cost for a capable strategy; retaining the original route.')
        backend = min(costs, key=lambda b: (costs[b], b))
        return Plan(backend, plan.operation, plan.parameters,
                    f"Measured training cost: {'operation lookup' if known else 'unseen-operation fallback'}; estimated execution {costs[backend]:.6f} ms.",
                    {f'estimated_{b}_ms': value for b, value in costs.items()})


def load_profile(path):
    path = Path(path)
    manifest_path = path.parent / 'profile-manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        expected = manifest['profiles'].get(path.name)
        if expected is None or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen cost profile changed or is unregistered')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def verify_split_directory(path):
    """Check frozen file hashes and operation-family separation before a run."""
    root = Path(path)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8-sig'))
    for source, entries in manifest['sources'].items():
        seen = set()
        for entry in entries:
            data_path = root / entry['file']
            if data_path.resolve().parent != root.resolve():
                raise ValueError('Manifest file must stay inside split directory')
            if hashlib.sha256(data_path.read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError(f"Frozen split changed: {entry['file']}")
            data = json.loads(data_path.read_text(encoding='utf-8-sig'))
            expected_split = entry['file'].removeprefix(source + '-').removesuffix('.json')
            if data['metadata']['split'] != expected_split or len(data['cases']) != entry['cases']:
                raise ValueError('Inconsistent split label or case count')
            families = {case['family'] for case in data['cases']}
            if families & seen or data['metadata']['source'] != source or sorted(families) != entry['families']:
                raise ValueError('Overlapping or inconsistent split families')
            seen.update(families)
    return manifest
