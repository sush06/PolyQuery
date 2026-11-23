"""Train and evaluate the opt-in semantic/physical router on frozen compositions."""
import argparse
import hashlib
import json
from collections import Counter
from contextlib import ExitStack, closing
from pathlib import Path
from statistics import median
from time import perf_counter

from polyquery import Engine
from polyquery import sqlserver
from polyquery.composition_evaluation import _sources, reference_answer
from polyquery.dag import DAGNode, InputBinding
from polyquery.dag_planner import PlanningDecision, validate_question_plan
from polyquery.evaluation import percentile
from polyquery.evaluation_v2 import _ollama_info, _source_config
from polyquery.intelligent_router import CHINOOK_TABLES, MODEL, IntelligentRouter, LearnedCosts, features
from polyquery.ldbc_sql_frontier import LDBCSQLFrontier
from polyquery.llm import LLMPlanningError
from polyquery.planner import Plan

ROOT = Path("benchmarks")
ARMS = ("keyword", "deterministic_dag", "qwen_fixed", "qwen_learned", "qwen_oracle")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")


def train(chinook_file, ldbc_file):
    a, b = read(chinook_file), read(ldbc_file)
    rows = []
    for backend in ("sqlite", "sqlserver"):
        for operation, table in CHINOOK_TABLES.items():
            entry = a["results"][backend][operation]
            if not entry["matches_gold"]:
                raise ValueError("Invalid Chinook calibration result")
            f = features("chinook", operation, rows=a["dataset_rows"][table], backend=backend)
            rows.append(label("chinook", operation, backend, f, entry["latency_ms"]["p50"], False))
    for size, frame in b["scales"].items():
        for case in frame["cases"]:
            if "backends" not in case or (int(size) == 1528 and not case["id"].startswith("existing-")):
                continue
            operation = case["operation"]
            params = case["parameters"]
            for source_arm, backend in (("bfs", "graph"), ("sqlserver_frontier", "sqlserver_frontier")):
                arm = case["backends"][source_arm]
                timeout = arm["status"] == "limited" and "time" in arm["reason"].lower()
                if arm["status"] != "correct" and not timeout:
                    continue
                f = features("ldbc", operation, rows=int(size), edges=frame["adjacency_entries"],
                             degree=case["source_degree"] or 0,
                             identical=params.get("source") == params.get("target"), backend=backend)
                rows.append(label("ldbc", operation, backend, f, None if timeout else arm["dag"]["p50"], timeout))
    LearnedCosts(rows)
    return {"version": 1, "rows": rows, "chinook_sha256": a["fixture_sha256"], "chinook_rows": a["dataset_rows"],
            "ldbc_source_hashes": b["source_hashes"], "development_sha256": [digest(chinook_file), digest(ldbc_file)],
            "timeout_penalty_ms": 1000 * b["timeout_seconds"]}


def label(source, operation, backend, feature, latency, timed_out):
    return {"source": source, "operation": operation, "backend": backend, "features": vars(feature),
            "latency_ms": latency, "timed_out": timed_out}


def from_record(raw):
    nodes = [DAGNode(n["id"], n["backend"], n["operation"], n["parameters"],
                     {key: InputBinding(b["node"], tuple(b["path"])) for key, b in n["bindings"].items()},
                     tuple(n["depends_on"]), n["retries"]) for n in raw["nodes"]]
    return PlanningDecision(raw["kind"], Plan.from_dict(raw["plan"]) if raw["plan"] else None,
                            tuple(nodes), tuple(raw["outputs"]), raw["reason"])


def shape(nodes, outputs):
    by_id = {node.id: node for node in nodes}
    def visit(key):
        node = by_id[key]
        return (node.operation, node.parameters,
                sorted((name, tuple(binding.path), visit(binding.node)) for name, binding in node.bindings.items()),
                sorted(json.dumps(visit(parent), sort_keys=True) for parent in node.depends_on))
    return [visit(key) for key in outputs]


def expected_shape(case):
    return shape(*IntelligentRouter.nodes(reference_decision(case)))


def reference_decision(case):
    raw = {"kind": "dag_plan", "plan": None, "outputs": case["outputs"], "reason": "",
           "nodes": [{"retries": 0, **row} for row in case["nodes"]]}
    return from_record(raw)


def plan_baseline(engine, question, kind):
    if kind != "keyword":
        return engine.dag_planner.decide(question)
    plan = engine.planner.plan(question)
    lexical, canonical = validate_question_plan(question, plan, engine.catalog)
    if plan.backend == "abstain" or not lexical.allowed or canonical["preserved"] is False:
        return PlanningDecision("abstain")
    return PlanningDecision("single_plan", plan=plan)


def plan_qwen(engine, question):
    tick = perf_counter()
    try:
        decision = engine.dag_planner.decide(question)
    except LLMPlanningError:
        decision = PlanningDecision("abstain", reason="Model planning failed.")
        engine.dag_planner.last_metadata["planning_failed"] = True
    return decision, (perf_counter()-tick)*1000, engine.dag_planner.last_metadata

def evaluate_case(router, case, decision, reference, gold_data, arm, planning_ms, metadata):
    specs, gold = gold_data
    refusal = case.get("expected_refusal")
    diagnostic = metadata.get("plan_diagnostics", {})
    semantic_failure = diagnostic.get("final_validation_error_category") or diagnostic.get("validation_error_category")
    structural_match = False
    if decision.kind == "abstain":
        status = ("semantic_failure" if metadata.get("planning_failed") or
                  semantic_failure and semantic_failure != "security_refusal" else "abstained")
        correct = status == "abstained" and refusal is not None
        structural_match = correct
    else:
        structural_match = refusal is None and shape(*router.nodes(decision)) == expected_shape(case)
        status, correct = "pending", False
    mode = {"qwen_fixed": "fixed", "qwen_learned": "learned", "qwen_oracle": "oracle"}.get(arm, "native")
    router.traces = []
    started = perf_counter()
    attempts = []
    if status == "pending":
        # Each repeat keeps the logical decision fixed. Gold verification is
        # outside the timed section. Oracle probe time is reported separately.
        for _ in range(3 if mode != "oracle" else 1):
            tick = perf_counter()
            try:
                values = router.execute(case["question"], decision, mode=mode)
            except Exception as error:
                traces = router.traces
                status = ("unavailable" if any(x["status"] == "unavailable" for x in traces if "status" in x)
                          else "timeout" if any(x.get("status") == "timeout" for x in traces)
                          or type(error).__name__ == "TraversalLimit" else "execution_failure")
                attempts.append({"ms": (perf_counter()-tick)*1000, "status": status,
                                 "trace": traces, "error_type": type(error).__name__})
                correct = False
                break
            elapsed = (perf_counter()-tick)*1000
            correct = refusal is None and len(values) == len(gold) and all(
                reference.matches(spec, value, target) for spec, value, target in zip(specs, values, gold))
            status = "correct" if correct else "incorrect"
            attempts.append({"ms": elapsed, "status": status, "trace": router.traces})
            if not correct:
                break
    overhead = sum(t.get("selection_ms", 0) for t in router.traces)
    exec_ms = median(a["ms"] for a in attempts) if attempts else (perf_counter()-started)*1000
    if mode == "oracle" and attempts:
        # Oracle probes are an offline measurement, not simulated runtime cost.
        exec_ms = max(0, exec_ms - overhead)
    return {"id": case["id"], "source": case["source"], "family": case["family"], "arm": arm,
            "outcome": status, "correct": correct, "semantic_plan_match": structural_match,
            "semantic_failure": semantic_failure, "semantic_diagnostics": diagnostic,
            "expected_refusal": refusal, "decision": decision.to_dict(),
            "planning_ms": planning_ms, "execution_ms": exec_ms, "total_ms": planning_ms+exec_ms,
            "selection_ms": None if mode == "oracle" else overhead,
            "oracle_probe_ms": overhead if mode == "oracle" else None, "attempts": attempts,
            "tokens": {key: metadata.get(key) for key in ("input_tokens", "output_tokens", "total_tokens", "usd", "api_calls")}}


def summary(rows):
    result = {}
    for name in ARMS:
        group = [r for r in rows if r["arm"] == name]
        done = [r for r in group if r["outcome"] != "unavailable"]
        answers = [r for r in done if r["outcome"] in ("correct", "incorrect")]
        result[name] = {"n": len(group), "outcomes": dict(Counter(r["outcome"] for r in group)),
                        "accuracy": sum(r["correct"] for r in done)/len(done) if done else None,
                        "answer_accuracy": sum(r["correct"] for r in answers)/len(answers) if answers else None,
                        "coverage": len(answers)/len(group) if group else None,
                        "correct_refusals": sum(r["correct"] and r["expected_refusal"] is not None for r in done),
                        "semantic_matches": sum(r["semantic_plan_match"] for r in done),
                        "p50_ms": percentile([r["total_ms"] for r in done], .5),
                        "p95_ms": percentile([r["total_ms"] for r in done], .95),
                        "execution_p50_ms": percentile([r["execution_ms"] for r in answers], .5),
                        "execution_p95_ms": percentile([r["execution_ms"] for r in answers], .95),
                        "median_selection_ms": percentile([r["selection_ms"] for r in answers
                                                            if r["selection_ms"] is not None], .5),
                        "tokens": sum(r.get("tokens", {}).get("total_tokens") or 0 for r in done)
                        if any(r.get("tokens", {}).get("total_tokens") is not None for r in done) else None,
                        "usd": None}
    return result


def paired(rows):
    by_id = {(r["id"], r["arm"]): r for r in rows}
    matches, regret = [], []
    for (case_id, arm), left in by_id.items():
        if arm != "qwen_learned" or not left.get("attempts") or not left["correct"]:
            continue
        right = by_id[case_id, "qwen_oracle"]
        if not right.get("attempts") or not right["correct"]:
            continue
        for a, b in zip(left["attempts"][-1]["trace"], right["attempts"][-1]["trace"], strict=True):
            if b.get("reason") != "evaluation_oracle":
                continue
            probes = b["predictions"]
            matches.append(a["chosen"] == b["chosen"])
            med = probes[a["chosen"]]["p50"]
            if med is not None:
                regret.append(med-probes[b["chosen"]]["p50"])
    return {"comparable_choices": len(matches), "correct_choices": sum(matches),
            "routing_accuracy": sum(matches)/len(matches) if matches else None,
            "median_regret_ms": median(regret) if regret else None, "regret_samples_ms": regret}


def open_remote(stack, engine, config):
    if engine.source == "chinook":
        auxiliary = stack.enter_context(Engine(source=engine.source, **config))
        adapter = sqlserver.ChinookSQLServer(auxiliary.backends["sql"], config["data_path"])
        del auxiliary.backends["sql"]
        stack.callback(adapter.close)
        return adapter
    if engine.source == "ldbc":
        db, driver_error = sqlserver.connect_from_env(sqlserver.CONNECTION_ENV, autocommit=True)
        stack.enter_context(closing(db))
        return LDBCSQLFrontier(db, engine.backends["graph"], engine.dag_executor.catalog,
                               timeout=2, driver_error=driver_error)
    return None


def check_profile(source, hashes, profile):
    if profile.get("version") != 1:
        raise ValueError("Unknown physical calibration version")
    if source == "chinook" and hashes["data_path"] != profile["chinook_sha256"]:
        raise ValueError("Chinook calibration snapshot differs")
    if source == "ldbc" and any(hashes[key+"_path"] != value
                                for key, value in profile["ldbc_source_hashes"].items()):
        raise ValueError("LDBC calibration snapshot differs")


def evaluate(benchmark_dir, sources, profile, *, remote=False, cache=None, authentication=None, typed_bindings=False,
             semantic_ir=False,
             endpoint="http://127.0.0.1:11434/v1"):
    test_path = benchmark_dir / "composition-test-sealed.json"
    cases = read(test_path)
    validation = read(benchmark_dir / "composition-validation.json")
    if {c["family"] for c in cases} & {c["family"] for c in validation}:
        raise ValueError("Composition families overlap")
    model_info = _ollama_info(endpoint, MODEL, 5)
    records = {r["id"]: r for r in (json.loads(line) for line in cache.read_text().splitlines())} if cache else {}
    if cache and records.keys() != {case["id"] for case in cases}:
        raise ValueError("Cached Qwen decisions must cover exactly this manifest")
    model = LearnedCosts(profile["rows"], timeout_penalty_ms=profile["timeout_penalty_ms"])
    rows, controls, availability = [], [], {}
    for source, config, hashes, group, reference in _sources(sources, cases):
        check_profile(source, hashes, profile)
        with ExitStack() as stack:
            engine = stack.enter_context(Engine(source=source, **config, planner_kind="dag"))
            qwen = None
            if not cache and model_info["status"] == "available":
                qwen = stack.enter_context(Engine(source=source, **config, planner_kind="llm-dag", model=MODEL,
                                                       llm_api="chat", llm_base_url=endpoint, llm_timeout=90))
                qwen.dag_planner.configure_grounding("catalog")
                if semantic_ir:
                    qwen.dag_planner.enable_semantic_ir()
                elif typed_bindings:
                    qwen.dag_planner.enable_typed_bindings()
            adapter = None
            if remote and source in ("chinook", "ldbc"):
                try:
                    adapter = open_remote(stack, engine, config)
                except (ValueError, sqlserver.SQLServerError) as error:
                    availability[source] = {"available": False, "error": type(error).__name__}
            availability.setdefault(source, {"available": adapter is not None})
            router = IntelligentRouter(engine, model, remote=adapter)
            for case in group:
                gold = reference_answer(case, reference)
                if "expected_refusal" not in case:
                    for arm in ("qwen_learned", "qwen_fixed", "qwen_oracle"):
                        control = evaluate_case(router, case, reference_decision(case), reference, gold, arm, 0, {})
                        control["planner"] = "reviewed_reference"
                        controls.append(control)
                for arm in ARMS[:2]:
                    tick = perf_counter()
                    decision = plan_baseline(engine, case["question"], arm)
                    rows.append(evaluate_case(router, case, decision, reference, gold, arm, (perf_counter()-tick)*1000, {}))
                if cache:
                    entry = records[case["id"]]
                    decision, elapsed, metadata = from_record(entry["decision"]), entry["planning_ms"], entry["model"]
                elif qwen:
                    decision, elapsed, metadata = plan_qwen(qwen, case["question"])
                else:
                    rows.extend({"id": case["id"], "source": source, "arm": arm, "outcome": "unavailable"}
                                for arm in ARMS[2:])
                    continue
                for arm in ("qwen_learned", "qwen_fixed", "qwen_oracle"):
                    row = evaluate_case(router, case, decision, reference, gold, arm, elapsed, metadata)
                    proposal = qwen.dag_planner.last_semantic_proposal if qwen else None
                    if proposal and "nodes" in case:
                        expected_literals = [(n["operation"], n["parameters"]) for n in case["nodes"]]
                        actual_literals = [(n.operation, n.parameters) for n in proposal[1]]
                        row["proposal_operations_and_literals_match"] = actual_literals == expected_literals
                    rows.append(row)
                print(case["id"], flush=True)
    return {"manifest_sha256": digest(test_path), "qwen_cache_sha256": digest(cache) if cache else None,
            "model": model_info, "grounding": "catalog", "typed_bindings": typed_bindings or semantic_ir,
            "semantic_ir": semantic_ir,
            "authentication": authentication,
            "remote_available": availability, "summary": summary(rows), "routing": paired(rows), "rows": rows,
            "reference_plan_control": {"summary": summary(controls), "routing": paired(controls), "rows": controls}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("train", "evaluate", "query"))
    parser.add_argument("--chinook", type=Path, default=Path("logs/chinook-sqlserver-benchmark.json"))
    parser.add_argument("--ldbc", type=Path, default=Path("logs/ldbc-sql-frontier.json"))
    parser.add_argument("--profile", type=Path, default=Path("logs/intelligent-routing-profile.json"))
    parser.add_argument("--output", type=Path, default=Path("logs/intelligent-routing-results.json"))
    parser.add_argument("--server", action="store_true")
    schema_mode = parser.add_mutually_exclusive_group()
    schema_mode.add_argument("--typed-bindings", action="store_true")
    schema_mode.add_argument("--semantic-ir", action="store_true")
    parser.add_argument("--source", choices=("chinook", "ldbc", "synthetic", "taxi"))
    parser.add_argument("--question")
    args = parser.parse_args()
    if args.command == "train":
        write(args.profile, train(args.chinook, args.ldbc))
    elif args.command == "evaluate":
        write(args.output, evaluate(ROOT, ROOT / "router-sources.json", read(args.profile), remote=args.server,
                                    typed_bindings=args.typed_bindings, semantic_ir=args.semantic_ir,
                                    authentication="Configured least-privilege reader" if args.server else None))
    else:
        if not args.source or not args.question:
            parser.error("query requires --source and --question")
        path = ROOT / "router-sources.json"
        config = {} if args.source == "synthetic" else _source_config(args.source, path, read(path))
        profile = read(args.profile)
        check_profile(args.source, {name: digest(file) for name, file in config.items()}, profile)
        endpoint = "http://127.0.0.1:11434/v1"
        with ExitStack() as stack:
            engine = stack.enter_context(Engine(source=args.source, **config, planner_kind="llm-dag",
                                                model=MODEL, llm_api="chat", llm_base_url=endpoint, llm_timeout=90))
            engine.dag_planner.configure_grounding("catalog")
            if args.semantic_ir:
                engine.dag_planner.enable_semantic_ir()
            elif args.typed_bindings:
                engine.dag_planner.enable_typed_bindings()
            remote = open_remote(stack, engine, config) if args.server else None
            model = LearnedCosts(profile["rows"], timeout_penalty_ms=profile["timeout_penalty_ms"])
            answer = IntelligentRouter(engine, model, remote=remote).query(args.question)
            print(json.dumps(answer, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
