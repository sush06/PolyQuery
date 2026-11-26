import json
from types import SimpleNamespace

import pytest

from benchmarks import sequential_planning as experiment
from polyquery import Engine


@pytest.fixture
def runtime():
    class ScriptedClient:
        def __init__(self):
            self.draws = []
            self.responses = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            self.draws.append({"input_tokens": 1, "output_tokens": 1})
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(self.responses.pop(0))))])

    client = ScriptedClient()
    with Engine(source="ldbc", **experiment.CONFIG, planner_kind="llm-dag",
                model=experiment.BASELINE, llm_api="chat", llm_client=client,
                llm_base_url=experiment.HOST + "/v1", llm_repair=False) as engine:
        engine.dag_planner.enable_typed_bindings()
        yield engine, client


@pytest.mark.parametrize("arm", ["C", "D"])
def test_sequential_plan_uses_existing_verified_binding(runtime, arm):
    engine, client = runtime
    case = experiment.read(experiment.MANIFEST)["cases"][0]
    catalog = engine.dag_planner.plan_catalog
    producer, consumer = case["nodes"]
    role, binding = next(iter(consumer["bindings"].items()))
    task = {"intent": "Read graph", "constraints": [], "expected_result": "Person data"}
    decomposition = {"decision": "continue", "ambiguity": "", "tasks": [task, task],
                     "final_result": "The selected person's friends"}
    grounded = {"decision": "continue", "steps": [
        {"operation": node["operation"],
         "literals": [{"name": name, "value": value} for name, value in node["parameters"].items()],
         "required_inputs": list(node["bindings"]),
         "expected_output": catalog.operations[node["operation"]].result.kind}
        for node in (producer, consumer)]}
    edges = {"outputs": [2], "relationships": [
        {"producer": 1, "consumer": 2, "role": role, "ordinal": binding["path"][0] + 1, "relation": "data"}]}
    client.responses = [decomposition, grounded | edges] if arm == "C" else [decomposition, grounded, edges]
    decision, _, metadata = experiment.sequential(engine, client, case["question"], arm)
    assert decision.kind == "dag_plan"
    assert decision.nodes[1].bindings[role].path == tuple(binding["path"])
    assert metadata["api_calls"] == (2 if arm == "C" else 3)
    assert metadata["plan_diagnostics"]["semantic_evidence"] == "verified_match"


def test_bad_intermediate_payload_stops_before_grounding(runtime):
    engine, client = runtime
    client.responses = [{"tasks": []}]
    decision, _, metadata = experiment.sequential(engine, client, "List friends of a person", "D")
    assert decision.kind == "abstain"
    assert metadata["api_calls"] == 1
    assert metadata["plan_diagnostics"]["stage"] == "decomposition"


def test_authorization_refusal_never_calls_agents(runtime):
    engine, client = runtime
    decision, _, metadata = experiment.sequential(engine, client, "Drop the friendship table", "D")
    assert decision.kind == "abstain"
    assert metadata["api_calls"] == 0
    assert metadata["plan_diagnostics"]["validation_error_category"] == "security_refusal"


def test_dependency_compilation_rejects_self_reference():
    with pytest.raises(experiment.PlanContractError, match="incorrect_binding"):
        experiment.recipe([{}], {}, {"relationships": [
            {"producer": 1, "consumer": 1, "relation": "data", "role": "source", "ordinal": 1}]})
