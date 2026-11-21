"""Paired answer-level evaluation for the keyword and DAG planners."""

import hashlib
import json
import random
import sqlite3
import time
from collections import Counter
from contextlib import ExitStack
from statistics import StatisticsError, median

from .backends import UnsupportedPlan
from .dag import DAGExecutionError, TransientBackendError
from .engine import Engine
from .evaluation import equivalent, implementation_fingerprint, percentile, source_fingerprint
from .llm import LLMPlanningError


def compare_dag_planners(cases, *, repeats=1, model_options=None):
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    if not isinstance(cases, (list, tuple)) or not cases:
        raise ValueError("Provide a nonempty list of evaluation cases")
    ids, questions = set(), set()
    for case in cases:
        if (not isinstance(case, dict) or not {"id", "group", "question", "expected_status"} <= case.keys()
                or any(type(case[key]) is not str or not case[key].strip() for key in ("id", "group", "question"))
                or case["expected_status"] not in ("ok", "abstained")
                or (case["expected_status"] == "ok") != ("expected" in case)
                or case["id"] in ids or " ".join(case["question"].casefold().split()) in questions
                or ("family" in case and (type(case["family"]) is not str or not case["family"].strip()))):
            raise ValueError("Invalid or duplicate DAG evaluation case")
        ids.add(case["id"])
        questions.add(" ".join(case["question"].casefold().split()))
    rows = []
    with ExitStack() as stack:
        engines = {kind: stack.enter_context(Engine(planner_kind=kind)) for kind in ("keyword", "dag")}
        if model_options is not None:
            engines["llm-dag"] = stack.enter_context(Engine(planner_kind="llm-dag", **model_options))
        fixture = source_fingerprint(engines["keyword"])
        for index, case in enumerate(cases):
            row = {**case, "planners": {}}
            runs = {kind: [] for kind in engines}
            for repeat in range(repeats):
                order = tuple(engines) if (index + repeat) % 2 == 0 else tuple(reversed(engines))
                for kind in order:
                    runs[kind].append(_run(engines[kind], case))
            for kind, outcomes in runs.items():
                first = outcomes[0]
                stable = all(equivalent(run["value"], first["value"]) and run["status"] == first["status"]
                             and run["decision"] == first["decision"]
                             and run.get("failure_kind") == first.get("failure_kind") for run in outcomes)
                timings = [run["latency_ms"] for run in outcomes]
                row["planners"][kind] = {
                    **first, "stable": stable, "correct": stable and all(run["correct"] for run in outcomes),
                    "latency_ms": median(timings),
                    "status_counts": dict(Counter(run["status"] for run in outcomes)),
                    "failure_counts": dict(Counter(run["failure_kind"] for run in outcomes if run["status"] == "error")),
                    "incorrect_answer_runs": sum(run["status"] == "ok" and not run["correct"] for run in outcomes),
                }
                if kind == "llm-dag":
                    row["planners"][kind]["model_usage"] = _usage(
                        [run["planner_metadata"] for run in outcomes]
                    )
            rows.append(row)
    multi_stage = [row for row in rows if row["group"] in ("multi_stage", "paraphrase")]
    ordinary = [row for row in rows if row["group"] == "single"]
    return {
        "source": "synthetic", "repeats": repeats, "overall": _summarize(rows),
        "dataset_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
        "implementation_sha256": implementation_fingerprint(), "source_hashes": fixture,
        "groups": {group: _summarize([row for row in rows if row["group"] == group])
                   for group in sorted({row["group"] for row in rows})},
        "research_question": {
            "multi_stage_and_paraphrases": _summarize(multi_stage) if multi_stage else None,
            "single_stage_incorrect_answer_delta": _paired_interval(ordinary, _incorrect_answer) if ordinary else None,
            **({
                "model_multi_stage_correctness_delta": _paired_interval(
                    multi_stage, lambda run: run["correct"], candidate="llm-dag"
                ) if multi_stage else None,
                "model_single_stage_incorrect_answer_delta": _paired_interval(
                    ordinary, _incorrect_answer, candidate="llm-dag"
                ) if ordinary else None,
            } if model_options is not None else {}),
        },
        "cases": rows,
    }


def _run(engine, case):
    started = time.perf_counter()
    try:
        response = engine.query(case["question"])
    except Exception as exc:
        if isinstance(exc, (DAGExecutionError, sqlite3.Error, StatisticsError, TransientBackendError)):
            failure_kind = "execution"
        elif isinstance(exc, UnsupportedPlan):
            failure_kind = "contract"
        elif isinstance(exc, LLMPlanningError):
            failure_kind = "planning"
        else:
            failure_kind = "other"
        return {"status": "error", "decision": "dag_plan" if isinstance(exc, DAGExecutionError) else None,
                "value": None, "correct": False, "failure_kind": failure_kind,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "error": f"{type(exc).__name__}: {exc}",
                "planner_metadata": dict(getattr(engine.dag_planner, "last_metadata", {}))}
    status = response["status"]
    value = response["result"]["value"] if status == "ok" else None
    decision = response.get("decision", {}).get("kind", "single_plan" if status == "ok" else "abstain")
    correct = status == case["expected_status"] and (status != "ok" or equivalent(value, case["expected"]))
    return {"status": status, "decision": decision, "value": value, "correct": correct,
            "latency_ms": response["latency_ms"], "planner_metadata": response.get("planner_metadata", {})}


def _summarize(rows):
    summary = {"cases": len(rows)}
    for kind in rows[0]["planners"]:
        runs = [row["planners"][kind] for row in rows]
        timings = [run["latency_ms"] for run in runs]
        summary[kind] = {
            "correct": sum(run["correct"] for run in runs),
            "answered": sum("ok" in run["status_counts"] for run in runs),
            "abstained": sum("abstained" in run["status_counts"] for run in runs),
            "incorrect_answers": sum(_incorrect_answer(run) for run in runs),
            "errors": sum("error" in run["status_counts"] for run in runs),
            "execution_failures": sum("execution" in run["failure_counts"] for run in runs),
            "contract_failures": sum("contract" in run["failure_counts"] for run in runs),
            "planning_failures": sum("planning" in run["failure_counts"] for run in runs),
            "other_failures": sum("other" in run["failure_counts"] for run in runs),
            "unstable_cases": sum(not run["stable"] for run in runs),
            "median_latency_ms": median(timings),
            "p95_latency_ms": percentile(timings, 0.95),
        }
        if kind == "llm-dag":
            summary[kind]["model_usage"] = _usage([run["model_usage"] for run in runs])
    for label, winner, loser in (("improved", "dag", "keyword"), ("regressed", "keyword", "dag")):
        summary[label] = sum(row["planners"][winner]["correct"] and not row["planners"][loser]["correct"]
                             for row in rows)
    summary["paired_correctness_delta"] = _paired_interval(rows, lambda run: run["correct"])
    if "llm-dag" in summary:
        summary["model_paired_correctness_delta"] = _paired_interval(rows, lambda run: run["correct"], candidate="llm-dag")
    return summary


def _incorrect_answer(run):
    return run["incorrect_answer_runs"] > 0


def _usage(records):
    totals = {}
    for key in ("api_calls", "input_tokens", "output_tokens", "total_tokens"):
        values = [record.get(key) for record in records if record.get("api_calls")]
        totals[key] = sum(values) if all(type(value) is int and value >= 0 for value in values) else None
    return totals


def _paired_interval(rows, score, *, candidate="dag", samples=5000, seed=20261120):
    """Resample whole intent families, retaining both outcomes for every question."""
    clusters = {}
    for row in rows:
        pair = row["planners"]
        delta = int(score(pair[candidate])) - int(score(pair["keyword"]))
        key = ("family", row["family"]) if "family" in row else ("case", row["id"])
        clusters.setdefault(key, []).append(delta)
    family_totals = [(sum(deltas), len(deltas)) for deltas in clusters.values()]
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        sampled = rng.choices(family_totals, k=len(family_totals))
        draws.append(sum(total for total, _ in sampled) / sum(size for _, size in sampled))
    return {
        f"{candidate.replace('-', '_')}_minus_keyword": sum(total for total, _ in family_totals) / len(rows),
        "ci95": [percentile(draws, 0.025), percentile(draws, 0.975)],
        "method": "paired intent-family cluster percentile bootstrap",
        "families": len(family_totals), "samples": samples, "seed": seed,
    }
