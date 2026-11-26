from copy import deepcopy
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError

from benchmarks.minimal_grounding import adapt, protected_input
from polyquery.catalog import build_catalog


@pytest.fixture
def grounding():
    graph = SimpleNamespace(people={"123", "456"}, adjacency={})
    catalog = build_catalog(SimpleNamespace(source="ldbc", backends={"graph": graph}))
    question, decomposition, mapping = protected_input(
        "Shortest path from 123 to 456; list friends of a person on the path.",
        {"tasks": [{"intent": "Path from 123 to 456.", "constraints": ["Use person 123."]}]},
    )
    return catalog, question, decomposition, mapping


def proposal():
    return {"decision": "continue", "steps": [
        {"operation": "shortest_path", "arguments": {
            "source": {"source": "literal", "value": "ENTITY_1"},
            "target": {"source": "literal", "value": "ENTITY_2"}}},
        {"operation": "friends", "arguments": {"source": {"source": "upstream"}}},
    ]}


def test_catalog_adapter_restores_literals_and_keeps_pending_role(grounding):
    catalog, question, decomposition, mapping = grounding
    assert decomposition["tasks"][0] == {
        "intent": "Path from ENTITY_1 to ENTITY_2.", "constraints": ["Use person ENTITY_1."]}
    grounded = adapt(proposal(), catalog, 2, question, mapping)
    assert grounded["steps"] == [
        {"operation": "shortest_path", "literals": [{"name": "source", "value": "123"},
            {"name": "target", "value": "456"}], "required_inputs": [], "expected_output": "path"},
        {"operation": "friends", "literals": [], "required_inputs": ["source"], "expected_output": "rows"},
    ]


@pytest.mark.parametrize("step", [
    {"operation": "unapproved", "arguments": {}},
    {"operation": "friends", "arguments": {"person_id": {"source": "upstream"}}},
    {"operation": "friends", "arguments": {}},
    {"operation": "friends", "arguments": {"source": {"source": "upstream", "value": "ENTITY_1"}}},
])
def test_schema_rejects_unknown_or_incomplete_arguments(grounding, step):
    catalog, question, _, mapping = grounding
    payload = proposal()
    payload["steps"][1] = step
    with pytest.raises(ValidationError):
        adapt(payload, catalog, 2, question, mapping)


@pytest.mark.parametrize("token", ["ENTITY_9", "ENTITY__1", "ENTITY_1.5", "123"])
def test_adapter_rejects_unverified_identifier_literals(grounding, token):
    catalog, question, _, mapping = grounding
    payload = proposal()
    payload["steps"][0]["arguments"]["source"]["value"] = token
    with pytest.raises(ValueError):
        adapt(payload, catalog, 2, question, mapping)


def test_unavailable_producer_cannot_supply_upstream(grounding):
    catalog, question, _, mapping = grounding
    payload = deepcopy(proposal())
    payload["steps"][0] = {"operation": "count_people", "arguments": {}}
    with pytest.raises(ValueError, match="No earlier operation"):
        adapt(payload, catalog, 2, question, mapping)


def test_literal_equal_to_marker_stays_literal():
    catalog = build_catalog(SimpleNamespace(source="synthetic"))
    catalog.domains["department"].add("upstream")
    payload = {"decision": "continue", "steps": [{"operation": "median",
        "arguments": {"department": {"source": "literal", "value": "upstream"}}}]}
    result = adapt(payload, catalog, 1, "Median salary for department upstream.", {})
    assert result["steps"][0]["literals"] == [{"name": "department", "value": "upstream"}]
    assert result["steps"][0]["required_inputs"] == []
