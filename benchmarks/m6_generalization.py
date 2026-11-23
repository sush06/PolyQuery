"""Run M6 with immutable inputs, one Qwen draw per case and a resumable journal."""
import argparse
import json
import subprocess
from collections import Counter
from contextlib import ExitStack
from dataclasses import asdict
from math import sqrt
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from benchmarks.intelligent_routing import (
    check_profile, digest, evaluate_case, open_remote, paired, plan_baseline,
    plan_qwen, read, reference_decision, write,
)
from benchmarks.planner_model_benchmark import BASELINE, HOST, SEED, TIMEOUT, Client, api
from polyquery import Engine
from polyquery.composition_evaluation import _family_shape, _sources, reference_answer
from polyquery.constraints import unsafe_request_findings
from polyquery.evaluation import equivalent, timing_summary
from polyquery.intelligent_router import IntelligentRouter, LearnedCosts

ROOT = Path("benchmarks")
MANIFEST = ROOT/"m6-manifest-v1.json"
GOLD = ROOT/"m6-gold-v1.json"
FREEZE = ROOT/"m6-freeze-v1.json"
PROFILE = ROOT/"m6-selector-profile-v1.json"
LOG = Path("logs/m6")
ARMS = ("keyword", "deterministic_dag", "qwen_fixed", "qwen_learned", "qwen_oracle")


def inputs():
    cases = read(MANIFEST)["cases"]
    a, b = ([case for case in cases if case["split"] == k] for k in ("development", "final"))
    for split in (a, b):
        if Counter(c["category"] for c in split) != dict.fromkeys(("single", "binding", "composition", "refusal"), 10):
            raise ValueError("Unexpected split balance")
    if {c["family"] for c in a} & {c["family"] for c in b} or (
            {_family_shape(c) for c in a if "nodes" in c} & {_family_shape(c) for c in b if "nodes" in c}):
        raise ValueError("M6 split families overlap")
    if len({c["id"] for c in cases}) != 80 or len({c["question"] for c in cases}) != 80:
        raise ValueError("Duplicate M6 case ID or wording")
    return cases


def engine_for(source, config, client):
    engine = Engine(source=source, **config, planner_kind="llm-dag", model=BASELINE, llm_api="chat",
                    llm_client=client, llm_base_url=HOST+"/v1", llm_timeout=TIMEOUT)
    engine.dag_planner.enable_typed_bindings()
    return engine


def prepare():
    if GOLD.exists() or FREEZE.exists():
        raise FileExistsError("The versioned M6 gold and freeze already exist")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "diff", "HEAD", "--name-only", "--", "polyquery"]).strip():
        raise ValueError("Freeze requires an unmodified production tree")
    model = next(m for m in api("/api/tags")["models"] if m["name"] == BASELINE)
    metadata = {}
    gold = {}
    for source, config, hashes, cases, reference in _sources(ROOT/"router-sources.json", inputs()):
        with engine_for(source, config, object()) as engine:
            planner = engine.dag_planner
            metadata[source] = {"fixture_sha256": hashes, "prompt_sha256": planner.prompt_sha256,
                                "schema": planner.schema, "catalog": planner.plan_catalog.to_dict()}
            # Catalog.to_dict includes full public identity domains. Keep the
            # authoritative catalog and fixture digest together in the freeze.
            for case in cases:
                specs, values = reference_answer(case, reference, engine)
                if case.get("expected_refusal") == "security" and not unsafe_request_findings(case["question"]):
                    raise ValueError(f"Unauthorized control missing an independent guard: {case['id']}")
                gold[case["id"]] = {"specs": specs, "values": values,
                                   "expected_refusal": case.get("expected_refusal")}
    write(GOLD, gold)
    write(FREEZE, {"git_commit": commit, "model": model, "ollama": api("/api/version"),
                   "manifest_sha256": digest(MANIFEST), "gold_sha256": digest(GOLD),
                   "selector_profile_sha256": digest(PROFILE), "sources": metadata,
                   "verifier_sha256": {str(path): digest(path) for path in Path("polyquery").glob("*.py")},
                   "settings": {"native_schema": True, "seed": SEED, "temperature": 0,
                                "thinking": False, "max_output_tokens": 1200,
                                "timeout_seconds": TIMEOUT, "repair": False}})


def score(router, case, decision, reference, gold, arm, plan_ms, meta):
    return evaluate_case(router, case, decision, reference, (gold["specs"], gold["values"]), arm,
                         plan_ms, meta)


def proposal_review(planner, case, row):
    proposal = planner.last_semantic_proposal
    if proposal is None:
        return "no_semantic_proposal"
    _, nodes, outputs = proposal
    expected = reference_decision(case) if "nodes" in case else None
    if expected is None:
        return "unsupported_intent"
    if not set(outputs) <= {node.id for node in nodes}:
        return "proposal_differs_from_review"
    # Compare full shapes only after typed validation. A nonmatch is
    # flagged for review, since equivalent alternate plans can exist.
    from benchmarks.intelligent_routing import shape
    exact = shape(nodes, outputs) == shape(expected.nodes, expected.outputs) and len(nodes) == len(expected.nodes)
    if exact and row["outcome"] == "semantic_failure":
        return "verified_reference_shape_rejected"
    return "reviewed_shape_match" if exact else "proposal_differs_from_review"


def run(*, server):
    freeze = read(FREEZE)
    inventory = {m["name"]: m for m in api("/api/tags")["models"]}
    if (freeze["model"]["digest"] != inventory[BASELINE]["digest"] or freeze["ollama"] != api("/api/version")
            or freeze["manifest_sha256"] != digest(MANIFEST) or freeze["gold_sha256"] != digest(GOLD)
            or any(digest(Path(name.replace("\\", "/"))) != sha
                   for name, sha in freeze["verifier_sha256"].items())):
        raise ValueError("Frozen inputs changed")
    profile = read(PROFILE)
    if freeze["selector_profile_sha256"] != digest(PROFILE):
        raise ValueError("Physical selector profile changed")
    model = LearnedCosts(profile["rows"], timeout_penalty_ms=profile["timeout_penalty_ms"])
    gold = read(GOLD)
    LOG.mkdir(parents=True, exist_ok=True)
    for split in ("development", "final"):
        filename = LOG/f"{split}.jsonl"
        done = {}
        if filename.exists():
            done = {r["id"]: r for r in (json.loads(line) for line in filename.read_text(encoding="utf-8").splitlines())}
            if any(r["freeze_sha256"] != digest(FREEZE) or r["server"] != server for r in done.values()):
                raise ValueError("Journal contains a different run configuration")
        cases = [c for c in inputs() if c["split"] == split]
        for source, config, hashes, group, reference in _sources(ROOT/"router-sources.json", cases):
            check_profile(source, hashes, profile)
            if hashes != freeze["sources"][source]["fixture_sha256"]:
                raise ValueError("Data fixture changed")
            with ExitStack() as stack:
                client = Client(native=True)
                engine = stack.enter_context(engine_for(source, config, client))
                planner = engine.dag_planner
                if planner.prompt_sha256 != freeze["sources"][source]["prompt_sha256"] or planner.schema != freeze["sources"][source]["schema"]:
                    raise ValueError("Prompt or schema differs from freeze")
                baseline = stack.enter_context(Engine(source=source, **config, planner_kind="dag"))
                remote = open_remote(stack, engine, config) if server else None
                router = IntelligentRouter(engine, model, remote=remote)
                for case in group:
                    if case["id"] in done:
                        continue
                    truth = gold[case["id"]]
                    arms = []
                    for arm in ARMS[:2]:
                        start = perf_counter()
                        decision = plan_baseline(baseline, case["question"], arm)
                        arms.append(score(router, case, decision, reference, truth, arm, (perf_counter()-start)*1000, {}))
                    verify_ms = []
                    preflight = planner._semantic_preflight

                    def timed(*args):
                        start = perf_counter()
                        try:
                            return preflight(*args)
                        finally:
                            verify_ms.append((perf_counter()-start)*1000)

                    client.telemetry = {}
                    with patch.object(planner, "_semantic_preflight", timed):
                        decision, planning_ms, meta = plan_qwen(engine, case["question"])
                    for arm in ARMS[2:]:
                        if arm == "qwen_oracle" and not server:
                            continue
                        row = score(router, case, decision, reference, truth, arm, planning_ms, meta)
                        diag = meta["plan_diagnostics"]
                        attempt = next(iter(diag.get("attempts", [])), {})
                        row.update({"schema_valid": client.telemetry.get("provider_schema_valid"),
                                    "catalog_valid": attempt.get("catalog_contract_valid"),
                                    "binding_valid": False if row["semantic_failure"] == "incorrect_binding"
                                    else True if attempt.get("catalog_contract_valid") is True else None,
                                    "verification_ms": sum(verify_ms) if verify_ms else None,
                                    "inference_ms": client.telemetry["total_duration"]/1e6
                                    if client.telemetry.get("total_duration") is not None else None,
                                    "review": proposal_review(planner, case, row)})
                        arms.append(row)
                    control = []
                    if "nodes" in case:
                        reviewed = reference_decision(case)
                        for arm in ("reference_native", "qwen_fixed", "qwen_learned"):
                            control.append(score(router, case, reviewed, reference, truth, arm, 0, {}))
                    entry = {"id": case["id"], "category": case["category"], "family": case["family"],
                             "split": split, "question": case["question"], "source": source,
                             "freeze_sha256": digest(FREEZE), "server": server, "provider": client.telemetry,
                             "proposal": planner.last_metadata["plan_diagnostics"].get("original_plan"),
                             "verified_decision": decision.to_dict(), "arms": arms, "reference_control": control}
                    with filename.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(entry, allow_nan=False)+"\n")
                    print(split, case["id"], arms[3]["outcome"], flush=True)
        summarize()


def interval(k, n):
    if not n:
        return None
    p, z = k/n, 1.96
    scale = 1 + z*z/n
    center = (p + z*z/(2*n))/scale
    half = z*sqrt((p*(1-p)+z*z/(4*n))/n)/scale
    return {"n": n, "successes": k, "rate": p, "wilson_95": [max(0, center-half), min(1, center+half)]}


def metrics(rows):
    answerable = [r for r in rows if r["expected_refusal"] is None]
    refusals = [r for r in rows if r["expected_refusal"] is not None]
    return {"n": len(rows), "outcomes": dict(Counter(r["outcome"] for r in rows)),
            "correct_answer_coverage": interval(sum(r["outcome"] == "correct" for r in answerable), len(answerable)),
            "correct_refusals": interval(sum(r["correct"] for r in refusals), len(refusals)),
            "accepted": sum(r["decision"]["kind"] != "abstain" for r in rows),
            "semantic_false_acceptance_candidates": sum(r["outcome"] == "incorrect" for r in rows),
            "reference_shaped_false_rejections": sum(r.get("review") == "verified_reference_shape_rejected" for r in rows),
            "failure_taxonomy": dict(Counter(r["semantic_failure"] for r in rows if r["semantic_failure"])),
            "latency_ms": timing_summary([r["total_ms"] for r in rows]),
            "tokens": sum(r["tokens"]["total_tokens"] or 0 for r in rows),
            "model_calls": sum(r["tokens"]["api_calls"] or 0 for r in rows)}


def summarize():
    report = {}
    for split in ("development", "final"):
        path = LOG/f"{split}.jsonl"
        if not path.exists():
            continue
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows = [r for case in records for r in case["arms"]]
        controls = [r for case in records for r in case["reference_control"]]
        report[split] = {"completed": len(records), "required": 40, "arms": {
            arm: metrics([r for r in rows if r["arm"] == arm]) for arm in ARMS},
            "families": {family: metrics([r for r in rows if r["family"] == family and r["arm"] == "qwen_learned"])
                         for family in sorted({r["family"] for r in rows})},
            "reference_control": {arm: metrics([r for r in controls if r["arm"] == arm])
                                  for arm in ("reference_native", "qwen_fixed", "qwen_learned")}}
        if any(r["arm"] == "qwen_oracle" for r in rows):
            report[split]["routing"] = paired(rows)
    write(LOG/"summary.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "summarize"))
    parser.add_argument("--server", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "run":
        run(server=args.server)
    else:
        summarize()


if __name__ == "__main__":
    main()
