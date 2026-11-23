import copy

import pytest

from polyquery import Engine, InputBinding
from polyquery.llm import LLMPlanningError
from test_llm_dag import fake_chat


def step(key, backend, op, params=None, bindings=(), order=()):
    return {"id": key, "backend": backend, "operation": op,
            "parameters": [{"name": k, "value": v} for k, v in (params or {}).items()],
            "bindings": list(bindings), "depends_on": list(order)}


def dag(nodes, outputs):
    return {"kind": "dag_plan", "nodes": nodes, "outputs": outputs}


def typed(engine):
    planner = engine.dag_planner
    planner.configure_grounding("catalog")
    planner.enable_typed_bindings()
    return planner


@pytest.mark.parametrize("direction, op", [("direct reports", "direct_reports"),
                                          ("all downstream reports", "descendants")])
def test_typed_field_selection_executes_through_engine(direction, op, synthetic_reference):
    binding = {"name": "source", "node": "roster", "path": [0, "name"]}
    proposal = dag([step("roster", "sql", "list", {"department": "Engineering"}),
                    step("result", "graph", op, bindings=[binding], order=["roster"])], ["result"])
    with Engine(planner_kind="llm-dag", model="fake", llm_client=fake_chat(proposal), llm_api="chat") as engine:
        planner = typed(engine)
        response = engine.query(f"List Engineering employees by name; then {direction} of the first listed employee.")
        assert response["result"]["value"] == synthetic_reference.reports("Alice", direct=op == "direct_reports")
        parsed = planner.last_semantic_proposal[1][1]
        assert parsed.bindings["source"] == InputBinding("roster", (0, "name"))
        assert parsed.depends_on == ("roster",)
        assert response["planner_metadata"]["plan_diagnostics"]["original_plan"]["nodes"][1]["bindings"][0]["path"] == [0, "name"]


@pytest.mark.parametrize("mutation", ["unknown-node", "missing-field", "wrong-identity", "number",
                                      "bool", "cycle", "literal-and-bound", "extra-key"])
def test_invalid_binding_stops_before_execution(mutation):
    binding = {"name": "source", "node": "roster", "path": [0, "name"]}
    proposal = dag([step("roster", "sql", "list", {"department": "Sales"}),
                    step("result", "graph", "direct_reports", bindings=[binding])], ["result"])
    first, result = proposal["nodes"]
    if mutation == "unknown-node":
        binding["node"] = "missing"
    elif mutation in ("missing-field", "wrong-identity", "number"):
        binding["path"][1] = {"missing-field": "bad", "wrong-identity": "department", "number": "salary"}[mutation]
    elif mutation == "bool":
        binding["path"][0] = True
    elif mutation == "cycle":
        first["depends_on"] = ["result"]
    elif mutation == "literal-and-bound":
        result["parameters"] = [{"name": "source", "value": "Carol"}]
    else:
        binding["extra"] = "rejected"
    with Engine(planner_kind="llm-dag", model="fake", llm_client=fake_chat(proposal), llm_api="chat") as engine:
        typed(engine)
        with pytest.raises(LLMPlanningError):
            engine.query("List Sales employees by name; then direct reports of the first listed employee.")


@pytest.mark.parametrize("mismatch", ["wrong-index", "wrong-scope", "extra-output"])
def test_valid_structure_cannot_change_requested_row_scope_or_output(mismatch):
    binding = {"name": "source", "node": "roster", "path": [0 if mismatch == "wrong-index" else 1, "name"]}
    proposal = dag([step("roster", "sql", "list", {"department": "Sales"}),
                    step("result", "graph", "descendants" if mismatch == "wrong-scope" else "direct_reports",
                         bindings=[binding])], ["roster", "result"] if mismatch == "extra-output" else ["result"])
    with Engine(planner_kind="llm-dag", model="fake", llm_client=fake_chat(proposal), llm_api="chat") as engine:
        typed(engine)
        assert engine.query("List Sales employees by name; then direct reports of the second listed employee.")["status"] == "abstained"


@pytest.mark.parametrize("mutation", [None, "missing-order", "wrong-statistic", "reversed-outputs"])
@pytest.mark.parametrize("source_data", ["chinook-compact"], indirect=True)
def test_ordered_alias_verifies_existing_operations_and_order(source_data, mutation):
    proposal = dag([step("customers", "sql", "count_customers"),
                    step("invoices", "sql", "mean" if mutation == "wrong-statistic" else "median",
                         order=[] if mutation == "missing-order" else ["customers"])],
                   ["invoices", "customers"] if mutation == "reversed-outputs" else ["customers", "invoices"])
    with Engine(**source_data.kwargs, planner_kind="llm-dag", model="fake", llm_client=fake_chat(proposal),
                llm_api="chat") as engine:
        typed(engine)
        response = engine.query("Show in order: how many customers, followed by median invoice total.")
    if mutation:
        assert response["status"] == "abstained"
    else:
        assert response["result"]["value"] == [source_data.values["count_customers"], source_data.values["median"]]


def test_legacy_schema_and_grounding_are_unchanged():
    with Engine(planner_kind="llm-dag", model="fake", llm_client=fake_chat(None), llm_api="chat") as engine:
        planner = engine.dag_planner
        old = copy.deepcopy(planner.schema)
        node = old["properties"]["nodes"]["items"]["properties"]
        assert "depends_on" not in node and "path" not in node["bindings"]["items"]["properties"]
        typed(engine)
        prompt = planner.instructions
        planner.enable_typed_bindings()
        assert prompt == planner.instructions
        assert old != planner.schema


def test_projected_path_item_does_not_need_a_row_field():
    from pathlib import Path
    from benchmarks.ldbc_sql_graph import Reference

    root = Path(__file__).resolve().parents[1] / "benchmarks"
    people, edges = root / "people_0_0.csv", root / "person_knows_person_0_0.csv"
    proposal = dag([step("route", "graph", "shortest_path", {"source": "987", "target": "1274"}),
                    step("neighbors", "graph", "friends",
                         bindings=[{"name": "source", "node": "route", "path": [1]}])],
                   ["neighbors"])
    with Engine(source="ldbc", people_path=people, edges_path=edges, planner_kind="llm-dag",
                model="fake", llm_api="chat", llm_client=fake_chat(proposal)) as engine:
        typed(engine)
        reference = Reference(people, edges, engine.backends["graph"].people)
        from polyquery.planner import Plan
        route = reference.answer(Plan("graph", "shortest_path", {"source": "987", "target": "1274"}))
        expected = reference.answer(Plan("graph", "friends", {"source": route[1]}))
        result = engine.query("Shortest path from 987 to 1274; then friends of the second person on that path.")
        assert result["result"]["value"] == expected
