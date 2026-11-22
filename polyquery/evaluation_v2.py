"""Paired evaluation of the existing planners on independent source references."""

import hashlib
import json
import platform
import random
import re
import sqlite3
import time
from collections import Counter
from itertools import combinations
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from .backends import UnsupportedPlan
from .dag import DAGExecutionError, DAGValidationError
from .engine import Engine
from .evaluation import implementation_fingerprint, percentile, timing_summary
from .evaluation_v2_reference import ARGUMENTS, Reference
from .llm import LLMPlanningError
from .semantic_audit import audit_block


PLANNERS = ("keyword", "dag", "llm", "llm-dag")
DAG_GROUNDING_PLANNERS = {"llm-dag-catalog": "catalog", "llm-dag-examples": "examples",
                          "llm-dag-examples-only": "examples-only"}
SPLITS = ("dev", "validation", "test")
ANSWER_CATEGORIES = {"single_stage", "multi_stage", "paraphrase"}
REFUSAL_CATEGORIES = {"ambiguous_or_unsupported", "adversarial"}
SOURCE_FIELDS = {"chinook": {"data_path"}, "taxi": {"data_path"}, "ldbc": {"people_path", "edges_path"}}


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate_cases(cases):
    if type(cases) is not list or not cases:
        raise ValueError("Evaluation v2 requires a nonempty list of cases")
    ids, questions, families, intents = set(), set(), {}, {}
    for case in cases:
        fields = ("id", "source", "split", "category", "family", "question")
        if not isinstance(case, dict) or any(type(case.get(key)) is not str or not case[key].strip() for key in fields):
            raise ValueError("Invalid evaluation case fields")
        case_id, source, split, category, family, question = (case[key] for key in fields)
        normalized = (source, " ".join(re.findall(r"\w+", question.casefold())))
        if (source not in ARGUMENTS or split not in SPLITS or category not in ANSWER_CATEGORIES | REFUSAL_CATEGORIES
                or case_id in ids or normalized in questions or len(question) > 2000):
            raise ValueError(f"Invalid or duplicate case: {case_id}")
        ids.add(case_id)
        questions.add(normalized)
        if families.setdefault(family, split) != split:
            raise ValueError(f"Paraphrase family crosses splits: {family}")
        if category in REFUSAL_CATEGORIES:
            reasons = {"security"} if category == "adversarial" else {"ambiguous", "unsupported"}
            if case.get("expected_refusal") not in reasons or "reference" in case:
                raise ValueError(f"Invalid refusal gold: {case_id}")
            continue
        refs = case.get("reference")
        if (not isinstance(refs, list) or not 1 <= len(refs) <= 5 or "expected_refusal" in case
                or (category == "single_stage" and len(refs) != 1) or (category == "multi_stage" and len(refs) < 2)):
            raise ValueError(f"Invalid reference list: {case_id}")
        for spec in refs:
            if (not isinstance(spec, dict) or spec.keys() != {"operation", "args"}
                    or type(spec["operation"]) is not str or spec["operation"] not in ARGUMENTS[source]
                    or type(spec["args"]) is not list or len(spec["args"]) != ARGUMENTS[source][spec["operation"]]
                    or any(type(arg) is not str or not re.fullmatch(r"[0-9]+", arg) for arg in spec["args"])):
                raise ValueError(f"Invalid reference operation: {case_id}")
        # Also detect a relabeled paraphrase or a reordered list of the same operations.
        signature = (source, tuple(sorted((spec["operation"], tuple(spec["args"])) for spec in refs)))
        if intents.setdefault(signature, split) != split:
            raise ValueError(f"Reference intent crosses splits: {case_id}")


def _source_config(source, sources_path, sources):
    config = sources.get(source)
    if config is None:
        raise FileNotFoundError(f"No source configuration for {source}")
    if (not isinstance(config, dict) or set(config) != SOURCE_FIELDS[source]
            or any(type(path) is not str or not path.strip() for path in config.values())):
        raise ValueError(f"Invalid source configuration for {source}")
    paths = {key: (sources_path.parent / path).resolve() for key, path in config.items()}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(f"Dataset file absent: {path.name}")
    return paths


def _ollama_info(base_url, model, timeout):
    endpoint = urlsplit(base_url)
    if (endpoint.scheme not in ("http", "https") or endpoint.hostname not in ("localhost", "127.0.0.1", "::1")
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            or endpoint.path.rstrip("/") != "/v1"):
        raise ValueError("Evaluation v2 Ollama URL must be a loopback /v1 endpoint")
    root = f"{endpoint.scheme}://{endpoint.netloc}"
    try:
        with urlopen(root + "/api/tags", timeout=min(timeout, 5)) as stream:
            models = json.load(stream)["models"]
        with urlopen(root + "/api/version", timeout=min(timeout, 5)) as stream:
            version = json.load(stream)["version"]
    except (URLError, OSError, ValueError, KeyError, TypeError) as error:
        return {"status": "unavailable", "reason": f"Ollama discovery failed ({type(error).__name__})"}
    if not isinstance(models, list) or any(not isinstance(item, dict) for item in models):
        return {"status": "unavailable", "reason": "Invalid Ollama model inventory"}
    for item in models:
        if model in (item.get("name"), item.get("model")):
            return {"status": "available", "model": model, "digest": item.get("digest"), "version": version,
                    "endpoint": base_url}
    return {"status": "unavailable", "reason": f"Model {model} is not installed in Ollama", "version": version}


def evaluate_v2(dataset_path, sources_path, *, split="test", planners=PLANNERS, ollama=False,
                model="qwen3:4b-instruct", base_url="http://127.0.0.1:11434/v1", timeout=90,
                bootstrap_samples=5000, seed=20261121, llm_repair=False):
    if (split not in (*SPLITS, "all") or not planners or len(set(planners)) != len(planners)
            or set(planners) - (set(PLANNERS) | DAG_GROUNDING_PLANNERS.keys())):
        raise ValueError("Invalid split or planner selection")
    if {"llm-dag-examples", "llm-dag-examples-only"} & set(planners) and split != "validation":
        raise ValueError("Development examples are evaluated on the separate validation split only")
    if llm_repair and DAG_GROUNDING_PLANNERS.keys() & set(planners):
        raise ValueError("Disable repair for the grounding comparison")
    if type(bootstrap_samples) is not int or bootstrap_samples < 1 or not 0 < timeout < float("inf"):
        raise ValueError("Bootstrap samples and model timeout must be positive")
    dataset_path, sources_path = Path(dataset_path), Path(sources_path)
    dataset_digest = _digest(dataset_path)
    implementation = implementation_fingerprint()
    cases = _read(dataset_path)
    validate_cases(cases)
    selected = [case for case in cases if split == "all" or case["split"] == split]
    if not selected:
        raise ValueError(f"No cases for split {split}")
    sources = _read(sources_path)
    if not isinstance(sources, dict):
        raise ValueError("Source configuration must be an object")
    candidates = ({"llm", "llm-dag"} | DAG_GROUNDING_PLANNERS.keys()) & set(planners)
    model_info = (_ollama_info(base_url, model, timeout) if ollama and candidates
                  else {"status": "unavailable", "reason": "Ollama disabled" if candidates else "No model planner selected"})
    rows, source_info, grounding_examples = [], {}, {}
    for source in sorted({case["source"] for case in selected}):
        source_cases = [case for case in selected if case["source"] == source]
        try:
            config = _source_config(source, sources_path, sources)
            hashes = {key: _digest(path) for key, path in config.items()}
            reference = Reference(source, config, {ref["operation"] for case in source_cases
                                                   for ref in case.get("reference", ())})
            gold = {case["id"]: [reference.answer(ref) for ref in case.get("reference", ())]
                    for case in source_cases}
        except (ImportError, OSError, sqlite3.Error, ValueError, KeyError) as error:
            reason = f"{type(error).__name__}: {error}"
            source_info[source] = {"status": "unavailable", "reason": reason}
            for planner in planners:
                rows.extend(_unavailable(case, planner, reason, "dataset") for case in source_cases)
            continue
        source_info[source] = {"status": "available", "sha256": hashes,
                               "files": {key: path.name for key, path in config.items()}}
        # Keep only one engine open at a time. Taxi's Arrow and SQLite projections
        # are large. Every configuration reads the same immutable source snapshot.
        for planner in planners:
            is_model = planner in candidates
            if is_model and model_info["status"] != "available":
                rows.extend(_unavailable(case, planner, model_info["reason"], "planner") for case in source_cases)
                continue
            options = dict(model=model, llm_api="chat", llm_base_url=base_url, llm_timeout=timeout,
                           llm_repair=llm_repair) if is_model else {}
            try:
                mode = DAG_GROUNDING_PLANNERS.get(planner)
                engine = Engine(source=source, planner_kind="llm-dag" if mode else planner, **config, **options)
            except (ImportError, OSError, ValueError, sqlite3.Error) as error:
                rows.extend(_unavailable(case, planner, f"{type(error).__name__}: {error}", "planner")
                            for case in source_cases)
                continue
            with engine:
                if mode:
                    ids = engine.dag_planner.configure_grounding(mode, cases)
                    grounding_examples.setdefault(planner, {})[source] = ids
                rows.extend(_run(engine, planner, case, reference, gold[case["id"]]) for case in source_cases)
        if any(_digest(path) != hashes[key] for key, path in config.items()):
            raise ValueError(f"Source files changed during evaluation: {source}")
    if _digest(dataset_path) != dataset_digest or implementation_fingerprint() != implementation:
        raise ValueError("Benchmark or implementation changed during evaluation")
    available = [row for row in rows if row["outcome"] != "unavailable"]
    return {
        "suite": "v2", "split": split, "questions": len(selected), "dataset_sha256": dataset_digest,
        "implementation_sha256": implementation, "sources": source_info, "ollama": model_info,
        "llm_repair": llm_repair,
        "grounding_example_ids": grounding_examples,
        "runtime": {"python": platform.python_version(), "platform": platform.platform(), "sqlite": sqlite3.sqlite_version},
        "bootstrap": {"method": "paired question-level percentile bootstrap", "samples": bootstrap_samples, "seed": seed},
        "overall": {planner: _summary([row for row in rows if row["planner"] == planner]) for planner in planners},
        **{key: _grouped(rows, dimension, planners) for key, dimension in (
            ("by_category", "category"), ("by_dataset", "source"), ("by_split", "split"),
        )},
        "paired": _paired(available, planners, bootstrap_samples, seed),
        "paired_by_split": {name: _paired([row for row in available if row["split"] == name],
                                        planners, bootstrap_samples, seed)
                            for name in SPLITS if any(case["split"] == name for case in selected)},
        "paired_by_category": {
            name: _paired([row for row in available if row["category"] == name], planners, bootstrap_samples, seed)
            for name in sorted({case["category"] for case in selected})
        },
        "rows": rows,
    }


def _unavailable(case, planner, reason, scope):
    return {**case, "planner": planner, "outcome": "unavailable", "correct": None, "reason": reason,
            "unavailable_scope": scope, "planning_ms": None, "execution_ms": None, "total_ms": None,
            "security_refusal": None, "model_usage": {}}


def _run(engine, planner, case, reference, gold):
    began = time.perf_counter()
    try:
        response = engine.query(case["question"])
    except Exception as error:
        metadata = getattr(engine.dag_planner or engine.planner, "last_metadata", {})
        failure = "execution_failure"
        if isinstance(error, DAGValidationError):
            failure = "invalid_plan"
        elif isinstance(error, DAGExecutionError):
            failure = "execution_failure" if error.stage == "backend execution" else "validation_failure"
        elif isinstance(error, UnsupportedPlan):
            failure = "validation_failure"
        elif isinstance(error, LLMPlanningError):
            failure = ("invalid_plan" if metadata.get("rejection") == "plan_contract"
                       or "violated the plan contract" in str(error) else "planning_failure")
        elapsed = (time.perf_counter() - began) * 1000
        before_execution = isinstance(error, LLMPlanningError)
        row = _scored_row(case, planner, failure, False, metadata, gold)
        return {**row, "error": f"{type(error).__name__}: {error}", "failure_stage": getattr(error, "stage", None),
                "planning_ms": elapsed if before_execution else None,
                "execution_ms": 0 if before_execution else None, "total_ms": elapsed}
    metadata = response.get("planner_metadata", {})
    if response["status"] == "abstained":
        reason = (response.get("decision") or response.get("plan") or {}).get("reason", "")
        blocked = ("proposed_plan" in response or metadata.get("rejection") == "constraint_preservation"
                   or metadata.get("plan_diagnostics", {}).get("final_validation_error_category") is not None
                   or "Constraint preservation blocked execution:" in reason)
        outcome = "validation_failure" if blocked else "abstained"
        correct = "expected_refusal" in case
        actual = None
    else:
        actual = response["result"]["value"]
        refs = case.get("reference", ())
        values = [actual] if len(refs) == 1 else actual
        correct = bool(refs and isinstance(values, list) and len(values) == len(gold)
                       and all(reference.matches(spec, answer, expected)
                               for spec, answer, expected in zip(refs, values, gold)))
        outcome = "correct" if correct else "incorrect"
    row = _scored_row(case, planner, outcome, correct, metadata, gold)
    proposal = getattr(engine.dag_planner, "last_semantic_proposal", None)
    reached_preflight = proposal is not None
    if reached_preflight:
        row["semantic_preflight"] = "rejected" if outcome == "validation_failure" else "accepted"
    if reached_preflight and row["semantic_preflight"] == "rejected" and case.get("split") == "validation":
        row["semantic_audit"] = audit_block(proposal, case, reference, gold)
    return {**row, "response_status": response["status"], "actual": actual,
            "decision": response.get("decision", {}).get("kind"),
            "plan": None if planner.startswith("llm") else response.get("plan"),
            "planning_ms": response.get("planning_ms"), "execution_ms": response.get("execution_ms"),
            "total_ms": response.get("latency_ms")}


def _scored_row(case, planner, outcome, correct, metadata, gold):
    diagnostics = metadata.get("plan_diagnostics") or {}
    attempts = diagnostics.get("attempts", ())
    last = attempts[-1] if attempts else {}
    category = diagnostics.get("final_validation_error_category")
    if outcome == "incorrect":
        category = "valid_but_incorrect_plan"
    elif outcome == "abstained" and not correct:
        category = "unnecessary_abstention"
    category = {"invalid_parameters": "invalid_parameter_value",
                "incorrect_binding": "incorrect_dependency_binding",
                "constraint_preservation": "valid_but_incorrect_plan"}.get(category, category)
    return {
        **case, "planner": planner, "outcome": outcome, "correct": correct,
        "expected": (gold[0] if len(gold) == 1 else gold) if gold else None,
        "security_refusal": correct if case.get("expected_refusal") == "security" else None,
        "model_usage": {key: metadata.get(key) for key in
                        ("api_calls", "input_tokens", "output_tokens", "total_tokens", "model", "prompt_sha256")},
        "plan_diagnostics": metadata.get("plan_diagnostics"),
        "semantic_preflight": "not_reached" if planner.startswith("llm-dag") else "not_applicable",
        "failure_category": category,
        "checks": {
            "json_structure_valid": last.get("json_structure_valid"),
            "catalog_contract_valid": last.get("catalog_contract_valid"),
            "semantic_answer_correct": correct,
        },
    }


def _summary(rows):
    scored = [row for row in rows if row["outcome"] != "unavailable"]
    total = len(scored)
    def rate(count):
        return count / total if total else None
    counts = {name: sum(row["outcome"] == name for row in scored) for name in (
        "correct", "incorrect", "abstained", "execution_failure", "invalid_plan", "validation_failure", "planning_failure",
    )}
    counts["unavailable"] = len(rows) - total
    calls = [row["model_usage"] for row in scored if row["model_usage"].get("api_calls")]
    model_usage = {key: sum(record[key] for record in calls)
                   if calls and all(type(record.get(key)) is int for record in calls) else None
                   for key in ("input_tokens", "output_tokens", "total_tokens")}
    model_usage["calls"] = sum(record["api_calls"] for record in calls)
    safe = sum(row["security_refusal"] is True for row in scored)
    diagnostics = [row["plan_diagnostics"] for row in scored if row.get("plan_diagnostics")]
    structures = [plan["attempts"][-1].get("valid_structure") for plan in diagnostics if plan["attempts"]]
    return {
        "requested": len(rows), "scored": total, "counts": counts,
        "accuracy": rate(sum(row["correct"] for row in scored)),
        "answered": counts["correct"] + counts["incorrect"],
        "abstention_rate": rate(sum(row.get("response_status") == "abstained" for row in scored)),
        "incorrect_answer_rate": rate(counts["incorrect"]), "invalid_plan_rate": rate(counts["invalid_plan"]),
        "security_cases": sum(row["security_refusal"] is not None for row in scored), "security_refusals": safe,
        "latency_ms": {phase: timing_summary([row[key] for row in scored if row[key] is not None])
                       for phase, key in (("planning", "planning_ms"), ("execution", "execution_ms"), ("total", "total_ms"))},
        "model_usage": model_usage,
        "failure_categories": dict(Counter(row["failure_category"] for row in scored if row["failure_category"])),
        "semantic_audit": dict(Counter(row["semantic_audit"]["classification"] for row in scored if "semantic_audit" in row)),
        "checks": {check: {
            "passed": sum(row["checks"][check] is True for row in scored),
            "failed": sum(row["checks"][check] is False for row in scored),
        } for check in ("json_structure_valid", "catalog_contract_valid", "semantic_answer_correct")},
        "plan_validity": {
            "checked_outputs": sum(value is not None for value in structures),
            "valid_outputs": structures.count(True),
            "repair_attempts": sum(plan["repair_attempted"] for plan in diagnostics),
            "initial_categories": dict(Counter(plan["validation_error_category"] for plan in diagnostics
                                               if "validation_error_category" in plan)),
            "final_categories": dict(Counter(plan["final_validation_error_category"] for plan in diagnostics
                                             if "final_validation_error_category" in plan)),
        },
    }


def _grouped(rows, dimension, planners):
    return {value: {planner: _summary([row for row in rows if row[dimension] == value and row["planner"] == planner])
                    for planner in planners} for value in sorted({row[dimension] for row in rows})}


def _paired(rows, planners, samples, seed):
    matrix = {planner: {row["id"]: row["correct"] for row in rows if row["planner"] == planner} for planner in planners}
    outcomes = {(row["planner"], row["id"]): row.get("outcome") for row in rows}
    comparisons = []
    for reference, candidate in combinations(planners, 2):
        ids = sorted(matrix[reference].keys() & matrix[candidate].keys())
        deltas = [int(matrix[candidate][case_id]) - int(matrix[reference][case_id]) for case_id in ids]
        n = len(deltas)
        rng = random.Random(seed)
        draws = [sum(rng.choices(deltas, k=n)) / n for _ in range(samples)] if n else []
        interval = [percentile(draws, .025), percentile(draws, .975)] if draws else None
        transitions = Counter(
            (outcomes[reference, case_id], matrix[reference][case_id],
             outcomes[candidate, case_id], matrix[candidate][case_id]) for case_id in ids
        )
        comparisons.append({"reference": reference, "candidate": candidate, "n": n,
                            "gains": deltas.count(1), "losses": deltas.count(-1),
                            "accuracy_delta": sum(deltas) / n if n else None, "ci95": interval,
                            "uncertain": n < 20 or interval[0] <= 0 <= interval[1],
                            "transitions": [
                                {"from_outcome": old, "from_correct": old_correct, "to_outcome": new,
                                 "to_correct": new_correct, "count": count}
                                for (old, old_correct, new, new_correct), count in transitions.items()
                            ]})
    return comparisons
