"""Answer, route, abstention, error, and latency evaluation."""
import hashlib
import json
import math
import platform
import time
from pathlib import Path
from datetime import datetime, timezone

BACKENDS = ("sql", "graph", "code", "abstain")


def source_fingerprint(engine):
    if engine.source == "synthetic":
        from .data import EMPLOYEES, REPORTS, SALES
        payload = json.dumps([EMPLOYEES, REPORTS, SALES], sort_keys=True).encode()
        return {"fixture_sha256": hashlib.sha256(payload).hexdigest()}
    hashes = {}
    for field in ("people_file", "edges_file", "database_file", "parquet_file"):
        if field not in engine.source_metadata:
            continue
        digest = hashlib.sha256()
        with Path(engine.source_metadata[field]).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes[field] = digest.hexdigest()
    return hashes


def equivalent(actual, expected):
    if type(actual) in (int, float) and type(expected) in (int, float):
        return math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-8)
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(equivalent(a, b) for a, b in zip(actual, expected))
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(equivalent(actual[k], expected[k]) for k in expected)
    return actual == expected


def answer_matches(actual, case, graph=None):
    def match(expected):
        if case.get("comparison") == "shortest_path":
            if expected is None:
                return actual is None
            if graph is None or not isinstance(actual, list) or len(actual) != len(expected) or not actual:
                return False
            if actual[0] != expected[0] or actual[-1] != expected[-1] or any(type(node) is not str or node not in graph.adjacency for node in actual):
                return False
            return all(b in graph.adjacency[a] for a, b in zip(actual, actual[1:]))
        if case.get("comparison", "ordered") == "unordered":
            if not isinstance(actual, list) or not isinstance(expected, list) or len(actual) != len(expected):
                return False
            remaining = list(actual)
            for item in expected:
                for i, candidate in enumerate(remaining):
                    if equivalent(candidate, item):
                        remaining.pop(i)
                        break
                else:
                    return False
            return True
        return equivalent(actual, expected)
    return any(match(value) for value in case.get("expected_alternatives", [case["expected"]]))


def routes_for(case):
    return case.get("backends", [case.get("backend")])


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def timing_summary(values):
    return {"samples": len(values), "mean": sum(values) / len(values) if values else None,
            "p50": percentile(values, .5), "p95": percentile(values, .95)}


def summarize(results):
    n = len(results)
    answered = [r for r in results if r["status"] == "ok"]
    abstained = [r for r in results if r["status"] == "abstained"]
    gold_abstain = [r for r in results if r["expected_backends"] == ["abstain"]]
    answerable = [r for r in results if r["expected_backends"] != ["abstain"]]
    return {
        "runs": n,
        "answer_accuracy": sum(r["answer_correct"] for r in results) / n,
        "answerable_accuracy": sum(r["answer_correct"] for r in answerable) / len(answerable) if answerable else None,
        "route_accuracy": sum(r["route_correct"] for r in results) / n,
        "joint_accuracy": sum(r["joint_correct"] for r in results) / n,
        "coverage": len(answered) / n,
        "answered_accuracy": sum(r["answer_correct"] for r in answered) / len(answered) if answered else None,
        "answered_joint_accuracy": sum(r["joint_correct"] for r in answered) / len(answered) if answered else None,
        "error_rate": sum(r["status"] == "error" for r in results) / n,
        "abstention_precision": sum(r["expected_backends"] == ["abstain"] for r in abstained) / len(abstained) if abstained else None,
        "abstention_recall": sum(r["status"] == "abstained" for r in gold_abstain) / len(gold_abstain) if gold_abstain else None,
        "latency": {field: timing_summary([r[field] for r in results if r[field] is not None])
                    for field in ("latency_ms", "planning_ms", "execution_ms")},
    }


def validate_cases(cases):
    if not isinstance(cases, list) or not cases:
        raise ValueError("Evaluation dataset must be a nonempty case list")
    identifiers = set()
    for case in cases:
        if not isinstance(case, dict) or not {"id", "question", "expected"} <= case.keys():
            raise ValueError("Every case requires id, question, expected, and backend(s)")
        identifier, routes = case["id"], routes_for(case)
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("Case IDs must be unique nonempty strings")
        identifiers.add(identifier)
        if not isinstance(case["question"], str) or not isinstance(routes, list) or not routes or any(r not in BACKENDS for r in routes):
            raise ValueError(f"Invalid question or backend(s): {identifier}")
        if "backend" in case and "backends" in case:
            raise ValueError("Use backend or backends, not both")
        if "abstain" in routes and (routes != ["abstain"] or case["expected"] is not None):
            raise ValueError("Abstention cases require only abstain and expected null")
        if case.get("comparison", "ordered") not in ("ordered", "unordered", "shortest_path"):
            raise ValueError("Comparison must be ordered, unordered, or shortest_path")
        alternatives = case.get("expected_alternatives", [case["expected"]])
        if not isinstance(alternatives, list) or not alternatives:
            raise ValueError("Expected alternatives must be a nonempty list")
        if case.get("comparison") == "unordered" and any(not isinstance(v, list) for v in alternatives):
            raise ValueError("Unordered comparison requires list answers")
        if case.get("comparison") == "shortest_path" and any(v is not None and (not isinstance(v, list) or not v or any(type(node) is not str for node in v)) for v in alternatives):
            raise ValueError("Shortest-path answers must be nonempty string lists or null")
        if not isinstance(case.get("family", "unspecified"), str):
            raise ValueError("Family must be a string")


def evaluate(engine, dataset, *, repeats=1, warmup=0, policy_label="rule-router"):
    if type(repeats) is not int or repeats < 1 or type(warmup) is not int or warmup < 0:
        raise ValueError("Repeats must be positive and warmup nonnegative integers")
    metadata = dataset.get("metadata", {}) if isinstance(dataset, dict) else {}
    cases = dataset.get("cases") if isinstance(dataset, dict) else dataset
    validate_cases(cases)
    if not isinstance(metadata, dict):
        raise ValueError("Dataset metadata must be an object")
    if metadata.get("source", engine.source) != engine.source:
        raise ValueError("Dataset source does not match engine source")
    graph = engine.backends.get("graph") if engine.source == "ldbc" else None
    if any(case.get("comparison") == "shortest_path" for case in cases) and graph is None:
        raise ValueError("Shortest-path scoring requires the LDBC graph projection")
    fingerprint = source_fingerprint(engine)
    if metadata.get("source_hashes", fingerprint) != fingerprint:
        raise ValueError("Dataset gold answers do not match the supplied source hashes")
    results = []
    for repetition in range(warmup + repeats):
        for case in cases:
            started = time.perf_counter()
            try:
                response = engine.query(case["question"])
                status, backend = response["status"], response["plan"]["backend"]
                actual = response["result"]["value"] if status == "ok" else None
                error = None
            except Exception as exception:
                response = {"latency_ms": (time.perf_counter() - started) * 1000}
                status, backend, actual = "error", "error", None
                error = {"type": type(exception).__name__, "message": str(exception)}
            if repetition < warmup:
                continue
            routes = routes_for(case)
            route_correct = backend in routes
            # Null answers cannot make an unintended abstention or error correct.
            answer_correct = (status == "abstained" if routes == ["abstain"] else status == "ok" and answer_matches(actual, case, graph))
            results.append({"id": case["id"], "repeat": repetition - warmup,
                            "family": case.get("family", "unspecified"),
                            "expected_backend": routes[0], "expected_backends": routes,
                            "actual_backend": backend, "status": status, "actual": actual,
                            "error": error, "route_correct": route_correct,
                            "answer_correct": answer_correct, "joint_correct": route_correct and answer_correct,
                            **{field: response.get(field) for field in ("latency_ms", "planning_ms", "execution_ms")}})
    return {
        "report_version": 2, "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_label": metadata.get("label", "Development regression set; not held-out research evidence"),
        "dataset_metadata": metadata,
        "dataset_sha256": hashlib.sha256(json.dumps(dataset, sort_keys=True).encode()).hexdigest(),
        "source": engine.source_metadata, "policy": policy_label, "python": platform.python_version(),
        "source_hashes": fingerprint,
        "initialization_ms": getattr(engine, "initialization_ms", None),
        "platform": platform.platform(), "cases": len(cases), "repeats": repeats, "warmup": warmup,
        **summarize(results),
        "by_family": {family: summarize([r for r in results if r["family"] == family]) for family in sorted({r["family"] for r in results})},
        "by_expected_backend": {backend: summarize([r for r in results if backend in r["expected_backends"]])
                                for backend in BACKENDS if any(backend in r["expected_backends"] for r in results)},
        "confusion_matrix": {expected: {actual: sum(r["expected_backend"] == expected and r["actual_backend"] == actual for r in results) for actual in (*BACKENDS, "error")} for expected in BACKENDS},
        "confusion_note": "Multiple acceptable routes use the first route as the matrix row; accuracy accepts any listed route.",
        "sql_only_route_accuracy": sum("sql" in routes_for(c) for c in cases) / len(cases),
        "baseline_note": "SQL-only route-label diagnostic, not a capable fixed-SQL execution comparison.",
        "latency_note": "Dataset order; warmup excluded; initialization excluded. Total includes errors; error stage times unavailable. Percentiles use linear interpolation.",
        "cost": {"usd": None, "tokens": None, "note": "Unmeasured; no monetary cost instrumentation."},
        "results": results,
    }


def evaluate_policies(factories, dataset, *, repeats=1, warmup=0):
    """Evaluate named engine factories on identical cases; each owns its resources.

    A factory returns an Engine-compatible context manager. Implement fixed
    policies with equivalent capabilities; route-label overrides are insufficient.
    """
    if not factories:
        raise ValueError("Provide at least one policy factory")
    reports = {}
    for label, factory in factories.items():
        with factory() as engine:
            reports[label] = evaluate(engine, dataset, repeats=repeats, warmup=warmup, policy_label=label)
        if len(reports) > 1:
            reference = next(iter(reports.values()))
            if reports[label]["source_hashes"] != reference["source_hashes"]:
                raise ValueError("Policy comparisons require identical source data")
    return {"policies": reports,
            "note": "Sequential policy runs in insertion order. Repeat with reversed order to check order effects; no causal routing claim follows from this report alone."}
