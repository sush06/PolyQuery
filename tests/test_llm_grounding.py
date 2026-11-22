import json
from unittest.mock import Mock

import pytest

from polyquery import Engine
from polyquery.evaluation_v2 import _run, evaluate_v2

from test_llm_dag import fake_chat, node, report_plan


pytestmark = pytest.mark.evaluation


@pytest.mark.parametrize("source_data", ["chinook-compact"], indirect=True)
def test_grounding_uses_source_catalog_and_only_matching_development_examples(source_data):
    with Engine(**source_data.kwargs, planner_kind="llm-dag", model="test", llm_client=Mock()) as engine:
        planner = engine.dag_planner
        original = planner.prompt_sha256
        example = {
            "source": "chinook", "split": "dev", "id": "development-sequence",
            "question": "How many customers?; then median invoice total",
            "reference": [{"operation": "count_customers", "args": []}, {"operation": "median", "args": []}],
        }
        ids = planner.configure_grounding("examples", [
            {**example, "split": "validation", "id": "validation-example"},
            {**example, "id": "wrong-reference", "reference": list(reversed(example["reference"]))},
            example,
        ])
        assert ids == ["development-sequence"]
        assert planner.prompt_sha256 != original
        context, example_text = planner.instructions.split("\nCATALOG:\n")[1].split("\nDevelopment example: ")
        context = json.loads(context)
        assert set(context["operations"]) == set(engine.catalog.operations)
        assert context["operations"]["median"]["router_backend"] == "code"
        assert context["operations"]["median"]["result"]["nullable"]
        assert [step["operation"] for step in json.loads(example_text)["plan"]["nodes"]] == [
            "count_customers", "median",
        ]
        assert planner.configure_grounding("catalog") == []
        assert "Development example:" not in planner.instructions


@pytest.mark.parametrize(("mode", "question", "proposal", "category"), [
    ("catalog", "Among Eve's direct reports, what is the mean salary?",
     report_plan("descendants", "Eve"), "wrong_graph_scope"),
    ("examples", "Among Eve's direct reports, what is the mean salary?",
     report_plan("descendants", "Eve"), "wrong_graph_scope"),
    ("catalog", "Find median pay for the Engineering employee we just discussed.",
     {"kind": "single_plan", "nodes": [node("median", "code", "median")], "outputs": ["median"]},
     "unsupported_dataset_capability"),
])
def test_preflight_does_not_dispatch_unverified_intent(mode, question, proposal, category):
    client = fake_chat(proposal)
    with Engine(planner_kind="llm-dag", model="test", llm_client=client, llm_api="chat") as engine:
        engine.dag_planner.configure_grounding(mode)
        execute = Mock()
        for backend in engine.backends.values():
            backend.execute = execute
        row = _run(engine, "llm-dag-catalog", {"id": "test", "question": question}, None, [])
        assert row["outcome"] == "validation_failure"
        assert row["failure_category"] == category
        assert row["checks"] == {"json_structure_valid": True, "catalog_contract_valid": True,
                                 "semantic_answer_correct": False}
        execute.assert_not_called()
    client.chat.completions.create.assert_called_once()


def test_bad_binding_passes_json_structure_but_fails_catalog():
    proposal = report_plan("direct_reports", "Eve")
    proposal["nodes"][0]["bindings"][0]["node"] = "absent"
    with Engine(planner_kind="llm-dag", model="test", llm_client=fake_chat(proposal), llm_api="chat") as engine:
        engine.dag_planner.configure_grounding("catalog")
        row = _run(engine, "llm-dag-catalog",
                   {"id": "binding", "question": "Among Eve's direct reports, what is the mean salary?"}, None, [])
    assert row["failure_category"] == "incorrect_dependency_binding"
    assert row["checks"] == {"json_structure_valid": True, "catalog_contract_valid": False,
                             "semantic_answer_correct": False}


def test_example_ablation_cannot_run_on_held_out_questions():
    with pytest.raises(ValueError, match="validation split only"):
        evaluate_v2("unused.json", "unused-source.json", planners=["llm-dag-examples"])
