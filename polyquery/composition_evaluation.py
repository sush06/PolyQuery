"""Reference admission and paired evaluation for source-local compositions."""

import argparse
import hashlib
import json
import re
from collections import Counter
from graphlib import TopologicalSorter
from pathlib import Path
from statistics import mean, median

from .dag import DAGNode, InputBinding
from .data import EMPLOYEES, REPORTS, SALES
from .engine import Engine
from .evaluation import equivalent, implementation_fingerprint
from .evaluation_v2 import (
    DAG_GROUNDING_PLANNERS, _digest, _grouped, _ollama_info, _paired, _read, _run,
    _source_config, _summary, _unavailable,
)
from .evaluation_v2_reference import Reference


ARMS = ("dag", "llm-dag", "llm-dag-examples-only", "llm-dag-examples")
MANIFESTS = {"validation": "composition-validation.json", "test": "composition-test-sealed.json"}
REFUSALS = {"unsupported", "ambiguous", "security"}
CATEGORIES = {"unseen_paraphrases", "unseen_compositions", "cross_backend_dependencies",
              "branching_dags", "unsupported", "adversarial"}
MODEL = "qwen3:4b-instruct"
_LDBC_ARGS = {"friends": ("source",), "shortest_path": ("source", "target")}


def _json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _nodes(case):
    return [DAGNode(step["id"], step["backend"], step["operation"], step["parameters"],
                    {name: InputBinding(binding["node"], tuple(binding["path"]))
                     for name, binding in step["bindings"].items()}, tuple(step["depends_on"]))
            for step in case.get("nodes", ())]


def _deps(node):
    return set(node.depends_on) | {binding.node for binding in node.bindings.values()}


def _topology(nodes):
    by_id = {node.id: node for node in nodes}
    parents = {node.id: _deps(node) for node in nodes}
    if len(by_id) != len(nodes) or any(deps - by_id.keys() for deps in parents.values()):
        raise ValueError("Duplicate reference ID or unknown predecessor")
    order = tuple(TopologicalSorter(parents).static_order())
    depths = {}
    for key in order:
        depths[key] = max((depths[parent] + 1 for parent in parents[key]), default=0)
    children = Counter(parent for deps in parents.values() for parent in deps)
    return by_id, order, {"dependency_depth": max(depths.values(), default=0), "nodes": len(nodes),
                          "backends": len({node.backend for node in nodes}),
                          "branching_factor": max(children.values(), default=0)}


def _family_shape(case):
    # Ignore backend choice and literals. Preserve operation roles and directed
    # edges, including which input is bound and the selector shape.
    by_id, order, _ = _topology(_nodes(case))
    shapes = {}
    for key in order:
        node = by_id[key]
        shapes[key] = (node.operation,
                       tuple(sorted(shapes[dep] for dep in node.depends_on)),
                       tuple(sorted((name, shapes[binding.node], tuple(type(part).__name__ for part in binding.path))
                                    for name, binding in node.bindings.items())))
    return case["source"], tuple(sorted(shapes.values()))


class IndependentReference:
    def __init__(self, source, config, operations):
        self.source = source
        self.reference = None if source == "synthetic" else Reference(source, config, operations)

    def spec(self, operation, parameters):
        if self.source == "synthetic":
            return {"operation": operation, "args": parameters}
        names = _LDBC_ARGS.get(operation, ())
        if parameters.keys() != set(names):
            raise ValueError(f"Unsupported oracle arguments for {operation}")
        return {"operation": operation, "args": [parameters[name] for name in names]}

    def answer(self, spec):
        if self.reference is not None:
            return self.reference.answer(spec)
        operation, args = spec["operation"], spec["args"]
        department = args.get("department")
        rows = [row for row in EMPLOYEES if department is None or row["department"] == department]
        if operation == "list":
            return sorted(rows, key=lambda row: row["name"])
        if operation == "count":
            return len(rows)
        if operation == "median":
            return median(row["salary"] for row in rows)
        if operation == "revenue":
            amounts = [amount for group, amount in SALES if department is None or department == group]
            return sum(amounts) if amounts else None
        if operation == "direct_reports":
            return sorted(child for parent, child in REPORTS if parent == args["source"])
        if operation == "descendants":
            reach = set()
            for _ in EMPLOYEES:
                reach.update(child for parent, child in REPORTS if parent == args["source"] or parent in reach)
            return sorted(reach)
        if operation == "mean_salary_of_employees":
            selected = set(args["employees"])
            return mean(row["salary"] for row in EMPLOYEES if row["name"] in selected) if selected else None
        raise ValueError(f"Unsupported synthetic oracle operation: {operation}")

    def matches(self, spec, actual, expected):
        return self.reference.matches(spec, actual, expected) if self.reference else equivalent(actual, expected)


def reference_answer(case, reference, engine=None):
    """Interpret the manually authored graph using raw dataset reference operations."""
    if "expected_refusal" in case:
        return [], []
    nodes = _nodes(case)
    by_id, order, _ = _topology(nodes)
    values, specs = {}, {}
    for key in order:
        node = by_id[key]
        params = dict(node.parameters)
        for name, binding in node.bindings.items():
            value = values[binding.node]
            for component in binding.path:
                value = value[component]
            params[name] = value
        specs[key] = reference.spec(node.operation, params)
        values[key] = reference.answer(specs[key])
    if engine is not None:
        actual = engine.execute_dag(nodes)
        for key in order:
            if not reference.matches(specs[key], actual[key]["value"], values[key]):
                raise ValueError(f"{case['id']}: independent reference disagrees at {key}")
    return [specs[key] for key in case["outputs"]], [values[key] for key in case["outputs"]]


def _sources(path, cases):
    configs = _read(path)
    for source in sorted({case["source"] for case in cases}):
        config = {} if source == "synthetic" else _source_config(source, path, configs)
        hashes = ({key: _digest(file) for key, file in config.items()} if config
                  else {"data": _json_digest([EMPLOYEES, REPORTS, SALES])})
        group = [case for case in cases if case["source"] == source]
        operations = {node.operation for case in group for node in _nodes(case)}
        yield source, config, hashes, group, IndependentReference(source, config, operations)


def _validate_partitions(partitions, earlier):
    family_splits, shapes, ids = {}, {}, set()
    normalize = lambda text: " ".join(re.findall(r"\w+", text.casefold()))
    questions = {normalize(item["question"]) for item in earlier}
    for split, cases in partitions.items():
        for case in cases:
            key = case["id"]
            family = case["family"]
            question = normalize(case["question"])
            category = case["category"]
            if key in ids or question in questions or category not in CATEGORIES:
                raise ValueError(f"Duplicate or invalid case: {key}")
            ids.add(key)
            questions.add(question)
            if family_splits.setdefault(family, split) != split:
                raise ValueError(f"Family crosses splits: {family}")
            nodes = _nodes(case)
            if case["complexity"] != _topology(nodes)[2]:
                raise ValueError(f"Incorrect complexity metadata: {key}")
            if "expected_refusal" in case:
                expected = {"security"} if category == "adversarial" else {"ambiguous", "unsupported"}
                if category not in ("unsupported", "adversarial") or case["expected_refusal"] not in expected or nodes:
                    raise ValueError(f"Invalid refusal reference: {key}")
                continue
            if len(nodes) < 2 or category in ("unsupported", "adversarial"):
                raise ValueError(f"Invalid composition: {key}")
            outputs = case["outputs"]
            if not outputs or len(set(outputs)) != len(outputs) or set(outputs) - {node.id for node in nodes}:
                raise ValueError(f"Invalid outputs: {key}")
            shape = _family_shape(case)
            if shapes.setdefault(shape, split) != split:
                raise ValueError(f"Composition topology crosses splits: {key}")
            if category == "branching_dags" and case["complexity"]["branching_factor"] < 2:
                raise ValueError(f"No branch in {key}")
            if category == "cross_backend_dependencies":
                by_id = {node.id: node for node in nodes}
                if not any(by_id[b.node].backend != node.backend for node in nodes for b in node.bindings.values()):
                    raise ValueError(f"No cross-backend binding in {key}")


def admit(benchmark_dir, sources):
    """Verify both partitions' reference graphs without asking any planner."""
    files = {split: benchmark_dir / name for split, name in MANIFESTS.items()}
    partitions = {split: _read(file) for split, file in files.items()}
    _validate_partitions(partitions, _read(benchmark_dir / "evaluation-v2.json"))
    source_hashes = {}
    for source, config, hashes, cases, reference in _sources(sources, sum(partitions.values(), [])):
        source_hashes[source] = hashes
        with Engine(source=source, **config) as engine:
            for case in cases:
                reference_answer(case, reference, engine)
    return {"manifests": {split: _json_digest(cases) for split, cases in partitions.items()}, "sources": source_hashes,
            "counts": {split: len(cases) for split, cases in partitions.items()}}


def _development_examples(benchmark_dir):
    cases = [case for case in _read(benchmark_dir / "evaluation-v2.json") if case["split"] == "dev"]
    # Reuse an earlier synthetic development example of independent stages.
    # Neither example arm sees a demonstration of a new dependency topology.
    example = next(case for case in _read(benchmark_dir / "dag-dev.json") if case["id"] == "sequence-three")
    cases.append({**example, "split": "dev", "source": "synthetic", "reference": [
        {"operation": "descendants", "args": ["Carol"]},
        {"operation": "count", "args": ["Research"]},
        {"operation": "median", "args": ["Engineering"]},
    ]})
    return cases


def run(benchmark_dir, sources, *, split="validation", ollama=False, timeout=90, selection=None):
    admission = _read(benchmark_dir / "composition-admission.json")
    implementation = implementation_fingerprint()
    if split == "test" and (not selection or selection.get("manifests") != admission["manifests"]
                            or selection.get("implementation_sha256") != implementation
                            or selection.get("arms") != list(ARMS) or not ollama):
        raise ValueError("A frozen live validation selection for this implementation is required before opening test")
    file = benchmark_dir / MANIFESTS[split]
    manifest = _read(file)
    if _json_digest(manifest) != admission["manifests"][split]:
        raise ValueError("Manifest no longer matches reference admission")
    cases = [{"split": split, **case} for case in manifest]
    development = _development_examples(benchmark_dir)
    development_hash = _json_digest(development)
    base_url = "http://127.0.0.1:11434/v1"
    model = _ollama_info(base_url, MODEL, timeout) if ollama else {"status": "unavailable", "reason": "Ollama disabled"}
    if split == "test" and (model["status"] != "available" or model["digest"] != selection.get("model_digest")
                            or model["version"] != selection.get("model_version")
                            or development_hash != selection.get("development_sha256")
                            or timeout != selection.get("timeout")):
        raise ValueError("Test model or settings differ from the frozen validation selection")
    rows, examples = [], {}
    for source, config, hashes, group, reference in _sources(sources, cases):
        if hashes != admission["sources"][source]:
            raise ValueError(f"Fixture changed after admission: {source}")
        gold = {case["id"]: reference_answer(case, reference) for case in group}
        for planner in ARMS:
            if planner != "dag" and model["status"] != "available":
                rows.extend(_unavailable(case, planner, model["reason"], "planner") for case in group)
                continue
            options = {} if planner == "dag" else dict(model=MODEL, llm_api="chat", llm_base_url=base_url,
                                                       llm_timeout=timeout)
            with Engine(source=source, planner_kind="dag" if planner == "dag" else "llm-dag", **config, **options) as engine:
                mode = DAG_GROUNDING_PLANNERS.get(planner)
                if mode:
                    examples.setdefault(planner, {})[source] = engine.dag_planner.configure_grounding(mode, development)
                for case in group:
                    refs, values = gold[case["id"]]
                    # Keep authored DAGs and gold references out of the planner input.
                    row = _run(engine, planner, {**case, "reference": refs}, reference, values, audit_semantics=False)
                    rows.append(row)
        if any(_digest(path) != hashes[name] for name, path in config.items()):
            raise ValueError(f"Fixture changed during evaluation: {source}")
    if (implementation_fingerprint() != implementation or _json_digest(_read(file)) != admission["manifests"][split]
            or _json_digest(_development_examples(benchmark_dir)) != development_hash):
        raise ValueError("Implementation or benchmark changed during evaluation")
    scored = [row for row in rows if row["outcome"] != "unavailable"]
    for row in rows:
        row["complexity_band"] = "refusal" if "expected_refusal" in row else (
            "branching" if row["complexity"]["branching_factor"] >= 2 else
            "deep_chain" if row["complexity"]["dependency_depth"] >= 2 else "two_stage"
        )
    return {
        "suite": "compositions", "split": split, "implementation_sha256": implementation,
        "manifests": admission["manifests"], "sources": admission["sources"], "arms": ARMS,
        "model": model, "timeout": timeout, "temperature": 0, "repair": False, "examples": examples,
        "max_output_tokens": 1200, "development_sha256": development_hash,
        "overall": {planner: _summarize([row for row in rows if row["planner"] == planner]) for planner in ARMS},
        "by_category": _breakdown(rows, "category"), "by_complexity": _breakdown(rows, "complexity_band"),
        "paired": _paired(scored, ARMS, 5000, 20261121), "rows": rows,
        "paired_by_complexity": {
            band: _paired([row for row in scored if row["complexity_band"] == band], ARMS, 5000, 20261121)
            for band in sorted({row["complexity_band"] for row in rows})
        },
    }


def _summarize(rows):
    result = _summary(rows)
    answered, scored = result["answered"], result["scored"]
    result["answer_accuracy"] = result["counts"]["correct"] / answered if answered else None
    result["answer_coverage"] = answered / scored if scored else None
    result["answerable_questions"] = sum("expected_refusal" not in row and row["outcome"] != "unavailable" for row in rows)
    answerable = result["answerable_questions"]
    result["answerable_coverage"] = (
        sum("expected_refusal" not in row and row.get("response_status") == "ok" for row in rows) / answerable
        if answerable else None
    )
    result["correct_refusals"] = sum(row["correct"] is True and row.get("response_status") == "abstained" for row in rows)
    return result


def _breakdown(rows, dimension):
    return {value: {planner: _summarize([row for row in rows if row[dimension] == value and row["planner"] == planner])
                    for planner in ARMS} for value in sorted({row[dimension] for row in rows})}


def freeze(report):
    if (report.get("suite") != "compositions" or report.get("split") != "validation"
            or report.get("model", {}).get("status") != "available"
            or report.get("arms") != list(ARMS)
            or report.get("implementation_sha256") != implementation_fingerprint()
            or any(summary["counts"]["unavailable"] for summary in report["overall"].values())):
        raise ValueError("A complete live validation report is required to freeze the four configurations")
    return {"manifests": report["manifests"], "implementation_sha256": report["implementation_sha256"],
            "arms": list(ARMS), "model_digest": report["model"]["digest"], "model_version": report["model"]["version"],
            "timeout": report["timeout"], "development_sha256": report["development_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmarks", type=Path, default=Path("benchmarks"))
    parser.add_argument("--sources", type=Path, default=Path("benchmarks/router-sources.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=MANIFESTS, default="validation")
    parser.add_argument("--ollama", action="store_true")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--selection", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--admit", action="store_true")
    action.add_argument("--freeze-report", type=Path)
    args = parser.parse_args()
    try:
        if args.admit:
            report = admit(args.benchmarks, args.sources)
        elif args.freeze_report:
            report = freeze(_read(args.freeze_report))
        else:
            report = run(args.benchmarks, args.sources, split=args.split, ollama=args.ollama, timeout=args.timeout,
                         selection=_read(args.selection) if args.selection else None)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps(report.get("overall", report), indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
