import pytest

from polyquery import Engine
from polyquery.catalog import Catalog, OperationSpec, ParameterSpec, ResultSpec
from polyquery.llm_contract import PlanContractError
from polyquery.semantic_ir import compile_recipe, recipe_snapshot
from test_llm_dag import fake_chat


def step(operation, *, args=(), links=(), after=()):
    return {"op": operation, "args": list(args), "links": list(links), "after": list(after)}


def link(parent=1, item=1, field=None, consumer=None):
    return {"step": parent, "item": item, "field": field, "input": consumer}


def recipe(*steps, outputs):
    return {"steps": list(steps), "outputs": list(outputs)}


def test_ir_compiles_projected_identity_through_verified_execution(synthetic_reference):
    payload = recipe(step("list", args=[{"name": "department", "value": "Sales"}]),
                     step("direct_reports", links=[link(item=2)]), outputs=[2])
    with Engine(planner_kind="llm-dag", model="fake", llm_api="chat", llm_client=fake_chat(payload)) as engine:
        planner = engine.dag_planner
        planner.enable_semantic_ir()
        result = engine.query("List Sales employees by name; then direct reports of the second listed employee.")
        assert result["result"]["value"] == synthetic_reference.reports("Dan", direct=True)
        diagnostics = result["planner_metadata"]["plan_diagnostics"]
        assert diagnostics["compiled_plan"]["nodes"][1]["bindings"][0]["path"] == [1, "name"]
        assert diagnostics["semantic_recipe"]["steps"][1]["links"][0]["field"] is None
        assert "Sales" not in str(recipe_snapshot(payload, planner.plan_catalog))


def test_ir_does_not_accept_an_unrequested_valid_selection():
    payload = recipe(step("list", args=[{"name": "department", "value": "Research"}]),
                     step("descendants", links=[link(item=2)]), outputs=[2])
    with Engine(planner_kind="llm-dag", model="fake", llm_api="chat", llm_client=fake_chat(payload)) as engine:
        engine.dag_planner.enable_semantic_ir()
        result = engine.query("List Research employees by name; then all downstream reports of the first listed employee.")
    assert result["status"] == "abstained"


def binding_catalog():
    return Catalog("test", {}, {
        "rows": OperationSpec(("sql",), ResultSpec("rows", fields={"left": "string", "right": "string"})),
        "consume": OperationSpec(("sql",), ResultSpec("integer"), {"label": ParameterSpec()}),
    }, {})


def test_ir_requires_explicit_disambiguation_between_two_compatible_fields():
    catalog = binding_catalog()
    with pytest.raises(PlanContractError, match="ambiguous_binding"):
        compile_recipe(recipe(step("rows"), step("consume", links=[link()]), outputs=[2]), catalog)
    compiled = compile_recipe(recipe(step("rows"), step("consume", links=[link(field="right")]), outputs=[2]), catalog)
    assert compiled["nodes"][1]["bindings"] == [{"name": "label", "node": "stage_1", "path": [0, "right"]}]


@pytest.mark.parametrize("problem", ["self-link", "forward-order", "wrong-identity", "zero-row",
                                     "unknown-op", "output-duplicate", "too-many-stages"])
def test_ir_rejects_unsafe_or_noncanonical_dependencies(problem):
    payload = recipe(step("list"), step("direct_reports", links=[link()]), outputs=[2])
    if problem == "self-link":
        payload["steps"][1]["links"][0]["step"] = 2
    elif problem == "forward-order":
        payload["steps"][0]["after"] = [2]
    elif problem == "wrong-identity":
        payload["steps"][1]["links"][0]["field"] = "department"
    elif problem == "zero-row":
        payload["steps"][1]["links"][0]["item"] = 0
    elif problem == "unknown-op":
        payload["steps"][0]["op"] = "arbitrary_sql"
    elif problem == "output-duplicate":
        payload["outputs"] = [2, 2]
    else:
        payload["steps"] += [step("list")] * 4
    with Engine() as engine, pytest.raises(PlanContractError):
        compile_recipe(payload, engine.dag_executor.catalog)


@pytest.mark.parametrize("source_data", ["chinook-compact"], indirect=True)
def test_ir_preserves_explicit_order_and_output_slots(source_data):
    payload = recipe(step("count_customers"), step("median", after=[1]), outputs=[1, 2])
    with Engine(**source_data.kwargs, planner_kind="llm-dag", model="fake", llm_api="chat",
                llm_client=fake_chat(payload)) as engine:
        engine.dag_planner.enable_semantic_ir()
        result = engine.query("Show in order: how many customers, followed by median invoice total.")
        assert result["result"]["value"] == [source_data.values["count_customers"], source_data.values["median"]]
        assert result["decision"]["nodes"][1]["depends_on"] == ("stage_1",)


@pytest.mark.parametrize("swap", [False, True])
def test_ir_keeps_projected_path_endpoint_role(swap):
    from pathlib import Path
    from benchmarks.ldbc_sql_graph import Reference
    from polyquery.planner import Plan

    root = Path(__file__).resolve().parents[1] / "benchmarks"
    people = root / "people_0_0.csv"
    edges = root / "person_knows_person_0_0.csv"
    literal = {"name": "target" if swap else "source", "value": "1274"}
    payload = recipe(step("friends", args=[{"name": "source", "value": "987"}]),
                     step("shortest_path", args=[literal], links=[link(item=1)]), outputs=[2])
    with Engine(source="ldbc", people_path=people, edges_path=edges, planner_kind="llm-dag",
                model="fake", llm_api="chat", llm_client=fake_chat(payload)) as engine:
        engine.dag_planner.enable_semantic_ir()
        result = engine.query("Friends of 987; then shortest path from person 1274 to the first listed friend.")
        if swap:
            assert result["status"] == "abstained"
        else:
            reference = Reference(people, edges, engine.backends["graph"].people)
            first = reference.answer(Plan("graph", "friends", {"source": "987"}))[0]["id"]
            expected = reference.answer(Plan("graph", "shortest_path", {"source": "1274", "target": first}))
            assert result["result"]["value"] == expected
