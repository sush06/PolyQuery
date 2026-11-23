"""Evaluate semantic evidence against reviewed plans without model calls."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
from time import perf_counter

from benchmarks.intelligent_routing import read, write
from polyquery import Engine
from polyquery.evaluation import timing_summary

ROOT = Path("benchmarks")


def step(key, operation, *, backend="sql", args=(), bindings=(), order=()):
    return {"id": key, "backend": backend, "operation": operation, "depends_on": list(order),
            "parameters": [{"name": n, "value": v} for n, v in args], "bindings": list(bindings)}


def dag(*nodes, outputs):
    return {"kind": "dag_plan", "nodes": list(nodes), "outputs": list(outputs)}


def cases():
    base = dag(step("total", "revenue"), step("songs", "count_tracks", order=["total"]), outputs=["total", "songs"])
    question = "Put two figures on my checklist: the combined amount charged across all invoices, followed by how many tracks?"
    yield "ordered-full-aggregation", "chinook", question, base, True
    yield "equivalent-ordered-form", "chinook", "Return in order: total invoice revenue, followed by how many tracks?", base, True
    yield "filtered-not-full", "chinook", question.replace("across all invoices", "across all invoices over ten dollars"), base, False
    count = deepcopy(base)
    count["nodes"][0]["operation"] = "count_invoices"
    yield "count-not-sum", "chinook", question, count, False
    missing = deepcopy(base)
    missing["outputs"] = ["total"]
    yield "omitted-requested-output", "chinook", question, missing, False
    reordered = deepcopy(base)
    reordered["outputs"].reverse()
    yield "wrong-output-order", "chinook", question, reordered, False
    unordered = deepcopy(base)
    unordered["nodes"][1]["depends_on"] = []
    yield "omitted-order-edge", "chinook", question, unordered, False
    projected = dag(step("roster", "list", args=[("department", "Engineering")]),
                    step("result", "direct_reports", backend="graph", bindings=[
                        {"name": "source", "node": "roster", "path": [0, "name"]}]), outputs=["result"])
    question = "List Engineering employees by name in ascending order; then direct reports of the first listed employee."
    yield "ascending-first", "synthetic", question, projected, True
    yield "ascending-equivalent", "synthetic", question.replace(" in ascending order", ""), projected, True
    yield "descending-is-different", "synthetic", question.replace("ascending", "descending"), projected, False
    yield "bottom-vs-first", "synthetic", question.replace("first", "last"), projected, False
    yield "unspecified-ranking", "synthetic", question.replace("first", "top"), projected, False
    wrong_index = deepcopy(projected)
    wrong_index["nodes"][1]["bindings"][0]["path"][0] = 1
    yield "wrong-row", "synthetic", question, wrong_index, False
    whole = deepcopy(projected)
    whole["nodes"][1]["bindings"][0]["path"] = []
    yield "whole-vs-field", "synthetic", question, whole, False
    field = deepcopy(projected)
    field["nodes"][1]["bindings"][0]["path"][1] = "department"
    yield "wrong-identity-field", "synthetic", question, field, False
    yield "extra-field-request", "synthetic", question.replace("employee.", "employee, including each report's salary."), projected, False


def review(planner, question, payload):
    from polyquery.constraints import unsafe_request_findings
    from polyquery.llm_contract import PlanContractError

    if unsafe_request_findings(question):
        return False, "authorization_rejection"
    planner._reset_metadata()
    try:
        decision = planner._decision(question, payload)
    except PlanContractError as error:
        return False, error.category
    evidence = planner.last_metadata["plan_diagnostics"].get("semantic_evidence")
    return decision.kind != "abstain", evidence


def run():
    rows = []
    for case, source, question, payload, expected in cases():
        config = {"data_path": ROOT/"Chinook_Sqlite.sqlite"} if source == "chinook" else {}
        with Engine(source=source, **config, planner_kind="llm-dag", model="offline", llm_client=object()) as engine:
            planner = engine.dag_planner
            planner.enable_typed_bindings()
            start = perf_counter()
            accepted, evidence = review(planner, question, payload)
            elapsed = (perf_counter()-start)*1000
        rows.append({"case": case, "question": question, "proposal": payload, "expected_match": expected,
                     "accepted": accepted, "evidence": evidence, "verification_ms": elapsed})
    counts = Counter(
        ("true_acceptance" if r["expected_match"] else "false_acceptance") if r["accepted"] else
        ("false_rejection" if r["expected_match"] else "correct_rejection") for r in rows)
    return {"rows": rows, "counts": dict(counts), "unverifiable": sum(
        r["evidence"] == "insufficient_evidence" for r in rows),
        "timing_ms": timing_summary([r["verification_ms"] for r in rows])}


if __name__ == "__main__":
    write(Path("logs/verifier-reliability-reviewed.json"), run())
