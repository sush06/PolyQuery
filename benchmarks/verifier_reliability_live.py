"""Compare the old and revised ordered verifier on exactly one model draw per case."""
import argparse
import re
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from benchmarks.intelligent_routing import evaluate_case, open_remote, plan_qwen, read, write
from benchmarks.planner_model_benchmark import BASELINE, HOST, Client
from polyquery import Engine
from polyquery.composition_evaluation import _sources, reference_answer
from polyquery.intelligent_router import IntelligentRouter, LearnedCosts
from polyquery.typed_intent import _ORDER

ROOT = Path("benchmarks")


def original_ordered(question, reference, catalog):
    text = re.sub(r"^(?:report|return|give|show) (?:the following )?in order:\s*", "",
                  question.strip(), flags=re.I)
    clauses = _ORDER.split(text)
    if not 2 <= len(clauses) <= 5:
        return None
    plans = [reference(clause) for clause in clauses]
    if any(plan is None for plan in plans):
        return None
    return [(plan.operation, plan.parameters, {}, (index-1,) if index else ())
            for index, plan in enumerate(plans)], tuple(range(len(plans)))


class ReplayClient(Client):
    replay = False
    response = None

    def create(self, **request):
        if self.replay:
            return self.response
        self.response = super().create(**request)
        return self.response


def run(*, server=False):
    cases = read(ROOT / "verifier-live-development.json")
    profile = read(Path("logs/intelligent-routing-profile.json")) if server else None
    rows = []
    for source, config, _, group, reference in _sources(ROOT/"router-sources.json", cases):
        with ExitStack() as stack:
            client = ReplayClient(native=True)
            engine = stack.enter_context(Engine(source=source, **config, planner_kind="llm-dag",
                        model=BASELINE, llm_api="chat", llm_client=client, llm_base_url=HOST+"/v1"))
            engine.dag_planner.enable_typed_bindings()
            remote = open_remote(stack, engine, config) if server else None
            model = (LearnedCosts(profile["rows"], timeout_penalty_ms=profile["timeout_penalty_ms"])
                     if profile else LearnedCosts([]))
            router = IntelligentRouter(engine, model, remote=remote)
            for case in group:
                gold = reference_answer(case, reference, engine)
                client.replay = False
                client.response = None
                decision, planning_ms, details = plan_qwen(engine, case["question"])
                revised = evaluate_case(router, case, decision, reference, gold, "revised", planning_ms, details)
                baseline = None
                if client.response is not None or details["api_calls"] == 0:
                    client.replay = True
                    with patch("polyquery.typed_intent._ordered", original_ordered):
                        before, _, before_details = plan_qwen(engine, case["question"])
                    baseline = evaluate_case(router, case, before, reference, gold, "baseline", planning_ms,
                                             before_details)
                    baseline["latency_note"] = "Includes the shared live proposal time measured with the revised verifier."
                routing = []
                if decision.kind != "abstain" and server:
                    for arm in ("qwen_fixed", "qwen_learned"):
                        row = evaluate_case(router, case, decision, reference, gold, arm, planning_ms, details)
                        routing.append(row)
                rows.append({"id": case["id"], "baseline": baseline, "revised": revised, "routing": routing,
                             "provider": client.telemetry})
                print(case["id"], baseline["outcome"] if baseline else "unavailable", revised["outcome"], flush=True)
    return {"model": BASELINE, "shared_proposals": True, "restricted_sqlserver_lab_reader": server, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", action="store_true")
    args = parser.parse_args()
    write(Path("logs/verifier-reliability-live.json"), run(server=args.server))


if __name__ == "__main__":
    main()
