"""Compare legacy and opt-in typed Qwen contracts on development cases."""
import argparse
import json
from contextlib import ExitStack
from pathlib import Path

from benchmarks.intelligent_routing import plan_qwen, evaluate_case, reference_decision, write, MODEL
from polyquery import Engine
from polyquery.composition_evaluation import _family_shape, _sources, reference_answer
from polyquery.evaluation import timing_summary
from polyquery.evaluation_v2 import _ollama_info
from polyquery.intelligent_router import IntelligentRouter, LearnedCosts
from polyquery.llm_contract import PlanContractError

ROOT = Path("benchmarks")
ENDPOINT = "http://127.0.0.1:11434/v1"


def negative_controls():
    """Attack the contract directly without counting these as model calls."""
    question = "List Sales employees by name; then all downstream reports of the first listed employee."
    rows = []
    with Engine(planner_kind="llm-dag", model=MODEL, llm_client=object(), llm_api="chat") as engine:
        planner = engine.dag_planner
        planner.enable_typed_bindings()
        for case, upstream, path, first_dependencies in (
            ("unknown_upstream", "missing", [0, "name"], []),
            ("nonexistent_field", "roster", [0, "missing"], []),
            ("numeric_field_to_identity", "roster", [0, "salary"], []),
            ("circular_dependency", "roster", [0, "name"], ["result"]),
            ("wrong_valid_index", "roster", [1, "name"], []),
        ):
            planner._reset_metadata()
            payload = {"kind": "dag_plan", "outputs": ["result"], "nodes": [
                {"id": "roster", "backend": "sql", "operation": "list",
                 "parameters": [{"name": "department", "value": "Sales"}], "bindings": [],
                 "depends_on": first_dependencies},
                {"id": "result", "backend": "graph", "operation": "descendants", "parameters": [],
                 "bindings": [{"name": "source", "node": upstream, "path": path}], "depends_on": []},
            ]}
            try:
                decision = planner._decision(question, payload)
            except PlanContractError as error:
                rows.append({"case": case, "stage": error.stage, "reason": error.category})
            else:
                if decision.kind != "abstain":
                    raise ValueError(f"Invalid control passed: {case}")
                rows.append({"case": case, "stage": "semantic_preflight", "reason": decision.reason})
    return rows


def run(manifest=ROOT/"typed-bindings-development.json", *, include_ir=False):
    model = _ollama_info(ENDPOINT, MODEL, 5)
    if model["status"] != "available":
        return {"model": model, "rows": []}
    cases = json.loads(manifest.read_bytes())
    families = {case["family"] for case in cases}
    ids = {case["id"] for case in cases}
    if len(ids) != len(cases):
        raise ValueError("Duplicate development case IDs")
    partitions = ["composition-validation.json", "composition-test-sealed.json"]
    if include_ir:
        partitions.append("typed-bindings-development.json")
    for filename in partitions:
        partition = json.loads((ROOT / filename).read_bytes())
        if families & {case["family"] for case in partition} or ids & {case["id"] for case in partition}:
            raise ValueError("Binding development cases must be family-disjoint")
        if include_ir and filename == "composition-test-sealed.json":
            development_shapes = {_family_shape(case) for case in cases if "nodes" in case}
            sealed_shapes = {_family_shape(case) for case in partition if "nodes" in case}
            if development_shapes & sealed_shapes:
                raise ValueError("Development and held-out dependency shapes overlap")
    rows = []
    for source, config, _, group, reference in _sources(ROOT/"router-sources.json", cases):
        with ExitStack() as stack:
            arms = ("legacy", "typed", "ir") if include_ir else ("legacy", "typed")
            engines = {arm: stack.enter_context(Engine(source=source, **config, planner_kind="llm-dag", model=MODEL,
                       llm_api="chat", llm_base_url=ENDPOINT, llm_timeout=90)) for arm in arms}
            legacy, extended = engines["legacy"], engines["typed"]
            legacy.dag_planner.configure_grounding("catalog")
            extended.dag_planner.configure_grounding("catalog")
            extended.dag_planner.enable_typed_bindings()
            if include_ir:
                engines["ir"].dag_planner.enable_semantic_ir()
            router = IntelligentRouter(legacy, LearnedCosts([]))
            for i, case in enumerate(group):
                gold = reference_answer(case, reference, engine=legacy)
                pair = list(engines.items())
                if i % 2:
                    pair.reverse()
                for arm, engine in pair:
                    decision, elapsed, metadata = plan_qwen(engine, case["question"])
                    row = evaluate_case(router, case, decision, reference, gold, arm, elapsed, metadata)
                    rows.append({**row, "details": metadata.get("plan_diagnostics", {})})
                    print(case["id"], arm, row["outcome"], flush=True)
    summary = {}
    for arm in arms:
        group = [r for r in rows if r["arm"] == arm]
        attempts = [a for r in group for a in r["details"].get("attempts", [])]
        summary[arm] = {
            "cases": len(group), "model_calls": len(attempts),
            "structurally_valid": sum(a.get("json_structure_valid") is True for a in attempts),
            "catalog_valid": sum(a.get("catalog_contract_valid") is True for a in attempts),
            "accepted": sum(r["decision"]["kind"] != "abstain" for r in group),
            "correct_answers": sum(r["outcome"] == "correct" for r in group),
            "correct_refusals": sum(r["correct"] and r["expected_refusal"] is not None for r in group),
            "tokens": sum(r["tokens"]["total_tokens"] or 0 for r in group),
            "latency_ms": timing_summary([r["total_ms"] for r in group]),
        }
    return {"model": model, "rows": rows, "summary": summary, "negative_controls": negative_controls()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("logs/typed-bindings-development.json"))
    parser.add_argument("--semantic-ir", action="store_true")
    args = parser.parse_args()
    write(args.output, run(ROOT / "semantic-ir-development.json", include_ir=True) if args.semantic_ir else run())


if __name__ == "__main__":
    main()
