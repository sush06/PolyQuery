"""Summarize complete or partial M6 journals with family-cluster uncertainty."""
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from benchmarks.intelligent_routing import read, write
from benchmarks.m6_generalization import ARMS, LOG, metrics as arm_metrics, summarize
from polyquery.evaluation import percentile, timing_summary


def cluster_interval(rows, *, refusal, seed=126, draws=2000):
    clusters = defaultdict(list)
    for row in rows:
        if (row["expected_refusal"] is not None) == refusal:
            clusters[row["family"]].append(row["correct"] if refusal else row["outcome"] == "correct")
    if not clusters:
        return None
    rng = random.Random(seed)
    groups = list(clusters.values())
    results = []
    for _ in range(draws):
        sample = rng.choices(groups, k=len(groups))
        results.append(sum(sum(group) for group in sample)/sum(map(len, sample)))
    return {"families": len(groups), "bootstrap_95": [percentile(results, .025), percentile(results, .975)]}


def report():
    summarize()
    report = read(LOG/"summary.json")
    prior_wording = read(Path("benchmarks/m6-wording-overlap-v1.json"))
    for split in report:
        records = [json.loads(line) for line in (LOG/f"{split}.jsonl").read_text(encoding="utf-8").splitlines()]
        rows = [arm for case in records for arm in case["arms"]]
        report[split]["prior_exact_wording_controls"] = [r["id"] for r in records if r["id"] in prior_wording]
        report[split]["new_wording_only"] = {
            arm: arm_metrics([r for r in rows if r["arm"] == arm and r["id"] not in prior_wording])
            for arm in ARMS}
        report[split]["complete"] = len(records) == 40
        report[split]["categories"] = {}
        for category in sorted({r["category"] for r in records}):
            filtered = [r for r in records if r["category"] == category]
            report[split]["categories"][category] = {
                arm: dict(Counter(x["outcome"] for r in filtered for x in r["arms"] if x["arm"] == arm))
                for arm in ARMS}
        for arm, metrics in report[split]["arms"].items():
            group = [r for r in rows if r["arm"] == arm]
            metrics["correct_answer_family_bootstrap"] = cluster_interval(group, refusal=False)
            metrics["correct_refusal_family_bootstrap"] = cluster_interval(group, refusal=True)
            if arm.startswith("qwen"):
                metrics["verification_latency_ms"] = timing_summary(
                    [r["verification_ms"] for r in group if r["verification_ms"] is not None])
                metrics["inference_latency_ms"] = timing_summary(
                    [r["inference_ms"] for r in group if r["inference_ms"] is not None])
                metrics["schema_valid"] = sum(r["schema_valid"] is True for r in group)
                metrics["catalog_valid"] = sum(r["catalog_valid"] is True for r in group)
                metrics["binding_invalid"] = sum(r["binding_valid"] is False for r in group)
                failures = Counter()
                for row in group:
                    outcome = row["outcome"]
                    if outcome == "semantic_failure":
                        attempt = next(iter(row["semantic_diagnostics"].get("attempts", [])), {})
                        category = row["semantic_failure"] or "provider_or_response_error"
                        if attempt.get("catalog_contract_valid") is True:
                            stage = "semantic_verification"
                        elif attempt.get("json_structure_valid") is False:
                            stage = "structured_output"
                        elif attempt.get("catalog_contract_valid") is False:
                            stage = "catalog_or_binding"
                        else:
                            stage = "provider_or_response"
                        failures[f"{stage}/{category}"] += 1
                    elif outcome in ("timeout", "execution_failure", "unavailable", "incorrect"):
                        failures[f"execution/{outcome}"] += 1
                metrics["first_failure_by_stage"] = dict(failures)
    return report


def markdown(result):
    lines = ["# M6 benchmark results", "", "Coverage denominators include failed and abstained cases.", ""]
    for split, data in result.items():
        lines.extend([f"## {split.title()}", "", f"Recorded {data['completed']}/{data['required']} cases.", "",
                      "| Arm | Correct answers | Correct refusals | Accepted | End-to-end p50/p95 (ms) |",
                      "| --- | ---: | ---: | ---: | ---: |"])
        for arm, metrics in data["arms"].items():
            def fraction(stat):
                return f"{stat['successes']}/{stat['n']}" if stat else "unavailable"
            timer = metrics["latency_ms"]
            latency = f"{timer['p50']:.2f}/{timer['p95']:.2f}" if timer["samples"] else "unavailable"
            lines.append(f"| {arm} | {fraction(metrics['correct_answer_coverage'])} | "
                         f"{fraction(metrics['correct_refusals'])} | {metrics['accepted']} | {latency} |")
        lines.extend(["", "Reference-plan controls, failure taxonomy, new-wording subsets, and "
                      "confidence intervals are in the accompanying analysis JSON.", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    result = report()
    write(LOG/"analysis.json", result)
    (LOG/"results.md").write_text(markdown(result), encoding="utf-8")
