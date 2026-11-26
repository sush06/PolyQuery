from types import SimpleNamespace

import pytest
from jsonschema import Draft7Validator

from benchmarks.subtask_grounding import combine, subtask_schema, task_context
from polyquery.catalog import build_catalog


@pytest.fixture
def catalog():
    graph = SimpleNamespace(people={"100", "200"}, adjacency={})
    return build_catalog(SimpleNamespace(source="ldbc", backends={"graph": graph}))


def test_context_includes_only_current_task_and_prerequisite_result(catalog):
    tasks = [
        {"intent": "Prerequisite intent", "expected_result": "An ordered path"},
        {"intent": "Unrelated intent", "expected_result": "Unrelated result"},
        {"intent": "Current intent", "expected_result": "Friend rows", "constraints": ["Use a path member"]},
    ]
    context = task_context(tasks, 3, [1], catalog)
    assert context["subtask"] == tasks[2]
    assert context["prerequisites"] == [{"task": 1, "expected_result": "An ordered path"}]
    assert set(context) == {"subtask", "prerequisites", "catalog"}
    with pytest.raises(ValueError, match="earlier tasks"):
        task_context(tasks, 1, [2], catalog)


def test_one_step_contract_reuses_catalog_argument_rules(catalog):
    validator = Draft7Validator(subtask_schema(catalog))
    assert validator.is_valid({"decision": "abstain", "step": None})
    assert not validator.is_valid({"decision": "continue", "steps": []})
    assert not validator.is_valid({"decision": "continue", "step": {"operation": "friends", "arguments": {}}})


def test_aggregation_restores_and_validates_both_subtasks(catalog):
    responses = [
        {"decision": "continue", "step": {"operation": "shortest_path", "arguments": {
            "source": {"source": "literal", "value": "ENTITY_1"},
            "target": {"source": "literal", "value": "ENTITY_2"}}}},
        {"decision": "continue", "step": {"operation": "friends", "arguments": {"source": {"source": "upstream"}}}},
    ]
    mapping = {"ENTITY_1": "100", "ENTITY_2": "200"}
    question = "Shortest path from ENTITY_1 to ENTITY_2; then list friends of a person on that path."
    output = combine(responses, catalog, 2, question, mapping)
    assert output["steps"][0]["literals"] == [{"name": "source", "value": "100"}, {"name": "target", "value": "200"}]
    assert output["steps"][1]["required_inputs"] == ["source"]
    assert [step["expected_output"] for step in output["steps"]] == ["path", "rows"]
    with pytest.raises(ValueError, match="abstained"):
        combine([responses[0], {"decision": "abstain", "step": None}], catalog, 2, question, mapping)
