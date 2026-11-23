"""Audit the saved intelligent-routing run without model calls."""
import argparse
import json
from collections import Counter
from pathlib import Path

from benchmarks.intelligent_routing import digest, read, write
from polyquery import Engine
from polyquery.composition_evaluation import _sources, reference_answer
from polyquery.constraints import unsafe_request_findings
from polyquery.llm_contract import PlanContractError

ROOT = Path("benchmarks")


def replay(planner, question, snapshot):
    """Replay lossless snapshots only. Node IDs were consistently normalized."""
    if "<redacted>" in json.dumps(snapshot):
        return {"status": "not_exactly_replayable", "reason": "Literals or references were redacted"}
    nodes, outputs = snapshot["nodes"], snapshot["outputs"]
    if outputs is None or any(not isinstance(node["parameters"], list) for node in nodes):
        return {"status": "not_exactly_replayable", "reason": "Original shape was redacted"}
    payload = {"kind": snapshot["kind"], "nodes": [
        {"id": f"node_{i}", **node} for i, node in enumerate(nodes, 1)], "outputs": outputs}
    planner._reset_metadata()
    try:
        decision = planner._decision(question, payload)
    except PlanContractError as error:
        return {"status": "normalized_replay", "stage": error.stage, "reason": error.category}
    return {"status": "normalized_replay", "decision": decision.kind,
            "reason": planner.last_metadata["plan_diagnostics"].get("final_validation_error_category")}


def audit(manifest, cache, sources, previous):
    cases = read(manifest)
    records = {r["id"]: r for r in map(json.loads, cache.read_text(encoding="utf-8").splitlines())}
    if records.keys() != {case["id"] for case in cases}:
        raise ValueError("Capture and manifest must contain the same IDs")
    original = read(previous)
    if digest(manifest) != original["manifest_sha256"] or digest(cache) != original["qwen_cache_sha256"]:
        raise ValueError("Original experiment fingerprints differ")
    rows = []
    for source, config, hashes, group, reference in _sources(sources, cases):
        # Construct the planner for deterministic decoding only. A dummy
        # client makes any accidental provider request impossible.
        with Engine(source=source, **config, planner_kind="llm-dag", model="qwen3:4b-instruct",
                    llm_client=object(), llm_api="chat") as engine:
            planner = engine.dag_planner
            for case in group:
                record = records[case["id"]]
                metadata = record["model"]["plan_diagnostics"]
                attempt = metadata["attempts"][0]
                snapshot = metadata["original_plan"]
                category = metadata["final_validation_error_category"]
                stage = ("json_schema" if not attempt["json_structure_valid"] else
                         "typed_catalog" if not attempt["catalog_contract_valid"] else "semantic_preflight")
                refused = case.get("expected_refusal")
                specs, gold = reference_answer(case, reference, engine=engine if not refused else None)
                rows.append({"id": case["id"], "question": case["question"], "source": source,
                             "reference_nodes": case.get("nodes"), "reference_outputs": case.get("outputs"),
                             "reference_status": "executed_and_verified" if not refused else "no_reference_required",
                             "gold": gold if specs else None, "expected_refusal": refused,
                             "recorded_payload_snapshot": snapshot, "first_stage": stage,
                             "recorded_rejection": category, "pre_model_safety_findings": unsafe_request_findings(case["question"]),
                             "replay": (replay(planner, case["question"], snapshot) if stage == "semantic_preflight"
                                        else {"status": "recorded_trace_only",
                                              "reason": "Original invalid payload shape or literals were not retained"})})
    return {"manifest_sha256": digest(manifest), "capture_sha256": digest(cache), "rows": rows,
            "model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("logs/intelligent-routing-failure-audit.json"))
    parser.add_argument("--after", type=Path, help="Compare an after-run report with the original")
    args = parser.parse_args()
    previous = Path("logs/intelligent-routing-results.json")
    if args.after:
        write(args.output, compare_runs(read(previous), read(args.after)))
        return
    write(args.output, audit(ROOT/"composition-test-sealed.json", Path("logs/intelligent-qwen-decisions.jsonl"),
                            ROOT/"router-sources.json", previous))


def compare_runs(before, after):
    if before["manifest_sha256"] != after["manifest_sha256"] or before["grounding"] != after["grounding"]:
        raise ValueError("Comparison requires the same held-out manifest and grounding")
    if before["model"].get("digest") != after["model"].get("digest"):
        raise ValueError("Comparison requires the same Qwen model digest")
    arms = ("qwen_fixed", "qwen_learned", "qwen_oracle")
    def metrics(report):
        results = {}
        for arm in arms:
            rows = [r for r in report["rows"] if r["arm"] == arm]
            results[arm] = {
                **report["summary"][arm],
                "accepted_plans": sum(r["decision"]["kind"] != "abstain" for r in rows),
                "correct_answers": sum(r["outcome"] == "correct" for r in rows),
                "verification_diagnostics": dict(Counter(r["semantic_failure"] for r in rows
                                                          if r["semantic_failure"])),
                "model_calls": sum(r["tokens"]["api_calls"] or 0 for r in rows),
            }
        return {"end_to_end": results, "routing": report["routing"],
                "reference_plan_control": {key: report["reference_plan_control"][key]
                                           for key in ("summary", "routing")}}
    return {"manifest_sha256": before["manifest_sha256"], "before": metrics(before), "after": metrics(after)}


if __name__ == "__main__":
    main()
