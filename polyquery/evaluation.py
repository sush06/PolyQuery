"""Joint route + answer evaluation and a fixed SQL routing baseline."""
import math


def equivalent(actual, expected):
    if type(actual) in (int, float) and type(expected) in (int, float):
        return math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-8)
    return actual == expected


def evaluate(engine, cases):
    if not cases:
        raise ValueError("Evaluation dataset is empty")
    results = []
    for case in cases:
        response = engine.query(case["question"])
        backend = response["plan"]["backend"]
        actual = response["result"]["value"] if response["result"] is not None else None
        route_correct = backend == case["backend"]
        answer_correct = equivalent(actual, case["expected"])
        results.append({"id": case["id"], "expected_backend": case["backend"], "actual_backend": backend, "route_correct": route_correct, "answer_correct": answer_correct, "joint_correct": route_correct and answer_correct, "latency_ms": response["latency_ms"]})
    n = len(results)
    answered = [r for r in results if r["actual_backend"] != "abstain"]
    abstained = [r for r in results if r["actual_backend"] == "abstain"]
    confusion = {expected: {actual: sum(r["expected_backend"] == expected and r["actual_backend"] == actual for r in results) for actual in ("sql", "graph", "code", "abstain")} for expected in ("sql", "graph", "code", "abstain")}
    return {
        "dataset_label": "Synthetic development regression set; not held-out research evidence",
        "cases": n,
        "route_accuracy": sum(r["route_correct"] for r in results) / n,
        "joint_accuracy": sum(r["joint_correct"] for r in results) / n,
        "coverage": len(answered) / n,
        "answered_joint_accuracy": sum(r["joint_correct"] for r in answered) / len(answered) if answered else None,
        "abstention_precision": sum(r["expected_backend"] == "abstain" for r in abstained) / len(abstained) if abstained else None,
        "sql_only_route_accuracy": sum(c["backend"] == "sql" for c in cases) / n,
        "baseline_note": "SQL-only route label baseline, not SQL execution/expressivity comparison.",
        "latency_note": "Per-query planning plus execution; excludes fixture initialization.",
        "confusion_matrix": confusion,
        "results": results,
    }

