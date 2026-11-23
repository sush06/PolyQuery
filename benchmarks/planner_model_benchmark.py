"""Controlled local model and decoding benchmark for the direct typed planner."""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen

from jsonschema import Draft7Validator

from benchmarks.intelligent_routing import digest, evaluate_case, plan_qwen, read, write
from polyquery import Engine
from polyquery.composition_evaluation import _family_shape, _sources, reference_answer
from polyquery.evaluation import timing_summary
from polyquery.intelligent_router import IntelligentRouter, LearnedCosts
from polyquery.llm_contract import PlanContractError, unique_object

ROOT = Path("benchmarks")
HOST = "http://127.0.0.1:11434"
BASELINE = "qwen3:4b-instruct"
LARGER = "qwen3:8b"
SEED = 43
TIMEOUT = 120


def api(path, data=None):
    payload = json.dumps(data).encode() if data is not None else None
    with urlopen(Request(HOST+path, payload, {"Content-Type": "application/json"}), timeout=TIMEOUT) as response:
        return json.load(response)


def namespace(obj):
    if isinstance(obj, list):
        return [namespace(item) for item in obj]
    if isinstance(obj, dict):
        return SimpleNamespace(**{key: namespace(value) for key, value in obj.items()})
    return obj


class Client:
    """Same chat contract with one HTTP attempt and explicit decoding controls."""

    def __init__(self, native):
        self.native = native
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.telemetry = {}

    def create(self, *, model, messages, temperature, max_tokens, response_format):
        loaded = {item["name"]: {k: item.get(k) for k in ("size_vram", "context_length", "runner")}
                  for item in api("/api/ps")["models"]}
        self.telemetry = {"residency_before_request": loaded.get(model),
                          "cold_before_request": model not in loaded}
        if not self.native:
            response = api("/v1/chat/completions", {
                "model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                "seed": SEED, "reasoning_effort": "none", "response_format": response_format})
            content = response["choices"][0]["message"].get("content")
        else:
            response = api("/api/chat", {"model": model, "messages": messages, "stream": False,
                "think": False, "format": response_format["json_schema"]["schema"],
                "options": {"temperature": temperature, "num_predict": max_tokens, "seed": SEED}})
            content = response["message"].get("content")
            input_tokens, output_tokens = (response.get(k) for k in ("prompt_eval_count", "eval_count"))
            self.telemetry.update({key: response.get(key) for key in
                                   ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration")})
            self.telemetry["residency_after_request"] = next(
                ({k: item.get(k) for k in ("size_vram", "context_length", "runner")}
                 for item in api("/api/ps")["models"] if item["name"] == model), None)
            response = {"model": model, "choices": [{"finish_reason": response.get("done_reason"),
                        "message": {"content": content}}], "usage": {"prompt_tokens": input_tokens,
                        "completion_tokens": output_tokens, "total_tokens": input_tokens+output_tokens
                        if input_tokens is not None and output_tokens is not None else None}}
        schema = response_format["json_schema"]["schema"]
        try:
            decoded = json.loads(content, object_pairs_hook=unique_object)
        except (TypeError, ValueError, RecursionError):
            self.telemetry["json_valid"] = False
        else:
            self.telemetry["json_valid"] = True
            self.telemetry["provider_schema_valid"] = Draft7Validator(schema).is_valid(decoded)
        self.telemetry["finish_reason"] = response["choices"][0]["finish_reason"]
        return namespace(response)


def evaluate(manifest, model, native=False):
    rows, snapshots = [], {}
    for source, config, hashes, cases, reference in _sources(ROOT/"router-sources.json", read(manifest)):
        snapshots[source] = hashes
        client = Client(native)
        with Engine(source=source, **config, planner_kind="llm-dag", model=model, llm_api="chat",
                    llm_client=client, llm_base_url=HOST+"/v1", llm_timeout=TIMEOUT) as engine:
            planner = engine.dag_planner
            planner.enable_typed_bindings()
            router = IntelligentRouter(engine, LearnedCosts([]))
            for case in cases:
                gold = reference_answer(case, reference, engine)
                client.telemetry = {}
                decision, elapsed, metadata = plan_qwen(engine, case["question"])
                row = evaluate_case(router, case, decision, reference, gold, "model", elapsed, metadata)
                diagnostics = metadata["plan_diagnostics"]
                attempt = next(iter(diagnostics.get("attempts", [])), {})
                category = row["semantic_failure"]
                structure = client.telemetry.get("provider_schema_valid")
                catalog = attempt.get("catalog_contract_valid")
                binding = (False if category == "incorrect_binding" else True if catalog is True else None)
                row.update({"provider": client.telemetry, "schema_valid": structure,
                            "catalog_valid": catalog, "binding_valid": binding,
                            "semantically_accepted": decision.kind != "abstain",
                            "failure_category": category or ("provider_or_completion_error"
                                if metadata.get("planning_failed") else None),
                            "request_ms": attempt.get("latency_ms"),
                            "prompt_sha256": planner.prompt_sha256,
                            "schema_sha256": hashlib.sha256(json.dumps(planner.schema, sort_keys=True).encode()).hexdigest()})
                rows.append(row)
                print(model, "native" if native else "compatible", case["id"], row["outcome"], flush=True)
    return {"model": model, "native": native, "dataset_sha256": snapshots, "rows": rows, "summary": summarize(rows)}


def summarize(rows):
    from collections import Counter

    return {
        "questions": len(rows),
        "correct_answers": sum(r["outcome"] == "correct" for r in rows),
        "correct_refusals": sum(r["correct"] and r["expected_refusal"] is not None for r in rows),
        "accepted": sum(r["semantically_accepted"] for r in rows),
        "coverage": sum(r["outcome"] in ("correct", "incorrect") for r in rows)/len(rows),
        "schema_valid": sum(r["schema_valid"] is True for r in rows),
        "catalog_valid": sum(r["catalog_valid"] is True for r in rows),
        "binding_valid": sum(r["binding_valid"] is True for r in rows),
        "failure_categories": dict(Counter(r["failure_category"] for r in rows if r["failure_category"])),
        "model_calls": sum(r["tokens"]["api_calls"] or 0 for r in rows),
        "input_tokens": sum(r["tokens"]["input_tokens"] or 0 for r in rows),
        "output_tokens": sum(r["tokens"]["output_tokens"] or 0 for r in rows),
        "latency_ms": timing_summary([r["total_ms"] for r in rows]),
        "request_latency_ms": timing_summary([r["request_ms"] for r in rows if r["request_ms"] is not None]),
    }


def rank(result):
    s = result["summary"]
    return (s["correct_answers"]+s["correct_refusals"], s["correct_answers"],
            -s["latency_ms"]["p50"], -s["input_tokens"]-s["output_tokens"])


def run(output, models, resume=None):
    dev = ROOT/"planner-model-development.json"
    final = ROOT/"planner-model-final.json"
    development = read(dev)
    final_cases = read(final)
    dev_shapes = {_family_shape(case) for case in development if "nodes" in case}
    final_shapes = {_family_shape(case) for case in final_cases if "nodes" in case}
    if dev_shapes & final_shapes or {c["family"] for c in development} & {c["family"] for c in final_cases}:
        raise ValueError("Development and final families overlap")
    inventory = {item["name"]: item for item in api("/api/tags")["models"]}
    report = {"inventory": inventory, "ollama": api("/api/version"), "hardware": {
        "machine": platform.node(), "platform": platform.platform(), "logical_cpus": os.cpu_count(),
        "processor": os.environ.get("PROCESSOR_IDENTIFIER")},
        "settings": {"seed": SEED, "temperature": 0, "max_output_tokens": 1200, "reasoning": False,
                     "timeout_seconds": TIMEOUT, "repair": False},
        "manifest_sha256": {"development": digest(dev), "final": digest(final)},
        "unavailable": [], "development": []}
    prior = read(resume) if resume else None
    if prior:
        if ("final" in prior or prior["manifest_sha256"] != report["manifest_sha256"]
                or prior["settings"] != report["settings"] or prior["ollama"] != report["ollama"]):
            raise ValueError("Resume requires identical settings and an unscored final partition")
        prior_digests = {key: value["digest"] for key, value in prior["inventory"].items()}
        report["development"] = [arm for arm in prior["development"]
                                 if arm["model"] in models and arm["model"] in inventory
                                 and inventory[arm["model"]]["digest"] == prior_digests[arm["model"]]]
    for model in models:
        if any(arm["model"] == model for arm in report["development"]):
            continue
        inventory.update({item["name"]: item for item in api("/api/tags")["models"]})
        if model in inventory:
            report["development"].append(evaluate(dev, model))
        else:
            report["unavailable"].append(model)
        write(output, report)
    if not report["development"]:
        raise ValueError("No requested model is available")
    best = max(report["development"], key=rank)
    cached_native = prior.get("controlled_decoding") if prior else None
    native = (cached_native if cached_native and cached_native["model"] == best["model"]
              and any(arm["model"] == best["model"] for arm in prior["development"])
              and inventory[best["model"]]["digest"] == prior_digests[best["model"]]
              else evaluate(dev, best["model"], native=True))
    report["controlled_decoding"] = native
    winner = max((best, native), key=rank)
    report["frozen"] = {"model": winner["model"], "native": winner["native"],
                        "implementation_sha256": {name: digest(Path(name)) for name in
                            ("benchmarks/planner_model_benchmark.py", "polyquery/llm_dag.py", "polyquery/typed_intent.py")}}
    write(output, report)
    report["final"] = evaluate(final, winner["model"], native=winner["native"])
    write(output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("logs/planner-model-benchmark.json"))
    parser.add_argument("--models", nargs="+", default=[BASELINE, LARGER])
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    run(args.output, args.models, args.resume)


if __name__ == "__main__":
    main()
