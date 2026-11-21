import sqlite3
from unittest.mock import Mock

import pytest

from polyquery import (
    DAGExecutionError, DAGNode, DAGValidationError, Engine, InputBinding, TransientBackendError,
)
from polyquery.backends import UnsupportedPlan


@pytest.fixture
def engine():
    with Engine() as instance:
        yield instance


def test_cross_backend_dag_with_forward_references_and_fan_in(engine):
    results = engine.execute_dag([
        DAGNode("median", "code", "median", bindings={"department": InputBinding("rows", (0, "department"))},
                depends_on=("path",)),
        DAGNode("path", "graph", "shortest_path", bindings={
            "source": InputBinding("rows", (0, "name")),
            "target": InputBinding("reports", (1,)),
        }),
        DAGNode("reports", "graph", "descendants", {"source": "Alice"}),
        DAGNode("rows", "sql", "list", {"department": "Engineering"}),
    ])
    assert list(results) == ["reports", "rows", "path", "median"]
    assert results["path"]["value"] == ["Alice", "Carol"]
    assert results["rows"]["bindings"] == ["Engineering"]
    assert results["median"]["value"] == 110000
    assert engine.query("Median salary in Engineering")["result"] == results["median"]


ROWS = DAGNode("rows", "sql", "list")


@pytest.mark.parametrize(("nodes", "message"), [
    ([ROWS, ROWS], "Duplicate node ID"),
    ([ROWS, DAGNode("", "sql", "count")], "nonempty string ID"),
    ([ROWS, DAGNode("x", "sql", "count", retries=True)], "Invalid retry count"),
    ([ROWS, DAGNode("x", "sql", "count", depends_on="rows")], "Invalid dependencies"),
    ([ROWS, DAGNode("x", "sql", "count", depends_on=("absent",))], "Unknown dependency"),
    ([ROWS, DAGNode("x", "code", "median", bindings={"department": InputBinding("absent")})], "Unknown dependency"),
    ([ROWS, DAGNode("x", "sql", "count", depends_on=("x",))], "Dependency cycle"),
    ([DAGNode("rows", "sql", "list", depends_on=("x",)),
      DAGNode("x", "code", "median", bindings={"department": InputBinding("rows", (0, "department"))})],
     "Dependency cycle"),
    ([ROWS, DAGNode("x", "graph", "descendants")], "Missing parameter"),
    ([ROWS, DAGNode("x", "sql", "count", {"query": "DROP TABLE employees"})], "Unexpected plan parameters"),
    ([ROWS, DAGNode("x", "sql", "DROP TABLE employees")], "Unavailable operation"),
    ([ROWS, DAGNode("x", "graph", "median")], "Unavailable operation"),
    ([ROWS, DAGNode("x", "abstain", "unsupported")], "Unavailable operation"),
    ([ROWS, DAGNode("x", "sql", "count", {"department": True})], "must be a string"),
    ([ROWS, DAGNode("x", "sql", "count", {"department": "Engineering' OR 1=1 --"})], "Unknown department"),
    ([ROWS, DAGNode("x", "code", "correlation", {"department": "Sales"})], "must be null"),
    ([ROWS, DAGNode("x", "code", "median", {"department": "Sales"},
                    {"department": InputBinding("rows", (0, "department"))})], "both a literal and a binding"),
    ([ROWS, DAGNode("x", "code", "median", bindings={"department": "rows"})], "Invalid binding"),
    ([ROWS, DAGNode("x", "code", "median", bindings={"department": InputBinding("rows", (-1, "name"))})],
     "nonnegative list index"),
    ([ROWS, DAGNode("x", "code", "median", bindings={"department": InputBinding("rows", (0, "unknown"))})],
     "Invalid result path"),
    ([ROWS, DAGNode("x", "code", "median", bindings={"department": InputBinding("rows", (0, "salary"))})],
     "Incompatible result type"),
    ([DAGNode("reports", "graph", "descendants", {"source": "Alice"}),
      DAGNode("x", "code", "median", bindings={"department": InputBinding("reports", (0,))})],
     "Incompatible result type"),
])
def test_invalid_dag_fails_before_backend_execution(engine, monkeypatch, nodes, message):
    calls = Mock()
    for backend in engine.backends.values():
        monkeypatch.setattr(backend, "execute", calls)
    with pytest.raises(DAGValidationError, match=message):
        engine.execute_dag(nodes)
    calls.assert_not_called()


@pytest.mark.parametrize(("path", "cause"), [((99, "name"), IndexError), ((0, "department"), UnsupportedPlan)])
def test_resolved_inputs_are_validated_before_dispatch(engine, monkeypatch, path, cause):
    graph = Mock()
    monkeypatch.setattr(engine.backends["graph"], "execute", graph)
    with pytest.raises(DAGExecutionError) as error:
        engine.execute_dag([ROWS, DAGNode("reports", "graph", "descendants",
                                         bindings={"source": InputBinding("rows", path)}, retries=3)])
    assert (error.value.node_id, error.value.stage, error.value.attempts) == ("reports", "input validation", 0)
    assert isinstance(error.value.__cause__, cause)
    graph.assert_not_called()


@pytest.mark.parametrize("result", [None, {}, {"value": [{"name": "Alice"}]}, {"value": [{"name": "Alice",
    "department": "Engineering", "salary": float("nan"), "experience": 6}]}])
def test_invalid_intermediate_is_neither_consumed_nor_retried(engine, monkeypatch, result):
    sql, graph = Mock(return_value=result), Mock()
    monkeypatch.setattr(engine.backends["sql"], "execute", sql)
    monkeypatch.setattr(engine.backends["graph"], "execute", graph)
    with pytest.raises(DAGExecutionError) as error:
        engine.execute_dag([DAGNode("rows", "sql", "list", retries=2),
                            DAGNode("reports", "graph", "descendants",
                                    bindings={"source": InputBinding("rows", (0, "name"))})])
    assert (error.value.node_id, error.value.stage, error.value.attempts) == ("rows", "result validation", 1)
    assert isinstance(error.value.__cause__, UnsupportedPlan)
    sql.assert_called_once()
    graph.assert_not_called()


def test_transient_retry_is_local_to_failed_node(engine, monkeypatch):
    sql = Mock(wraps=engine.backends["sql"].execute)
    graph = Mock(side_effect=[TransientBackendError("try again"), {"value": ["Bob"]}])
    monkeypatch.setattr(engine.backends["sql"], "execute", sql)
    monkeypatch.setattr(engine.backends["graph"], "execute", graph)
    results = engine.execute_dag([ROWS, DAGNode("reports", "graph", "descendants",
                                               bindings={"source": InputBinding("rows", (0, "name"))}, retries=1)])
    assert results["reports"]["value"] == ["Bob"]
    sql.assert_called_once()
    assert graph.call_count == 2
    assert graph.call_args_list[0] == graph.call_args_list[1]


@pytest.mark.parametrize(("failure", "retries", "attempts"), [
    (TransientBackendError("busy"), 0, 1),
    (TransientBackendError("busy"), 2, 3),
    (UnsupportedPlan("invalid"), 2, 1),
    (sqlite3.OperationalError("syntax error"), 2, 1),
    (RuntimeError("permanent"), 2, 1),
])
def test_retry_exhaustion_and_permanent_errors(engine, monkeypatch, failure, retries, attempts):
    sql = Mock(side_effect=failure)
    monkeypatch.setattr(engine.backends["sql"], "execute", sql)
    with pytest.raises(DAGExecutionError) as error:
        engine.execute_dag([DAGNode("count", "sql", "count", retries=retries)])
    assert (error.value.stage, error.value.attempts) == ("backend execution", attempts)
    assert error.value.__cause__ is failure
    assert sql.call_count == attempts


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_BUSY | 256])
def test_sqlite_lock_codes_are_retryable(engine, monkeypatch, code):
    busy = sqlite3.OperationalError("locked")
    busy.sqlite_errorcode = code
    sql = Mock(side_effect=[busy, {"value": 6}])
    monkeypatch.setattr(engine.backends["sql"], "execute", sql)
    assert engine.execute_dag([DAGNode("count", "sql", "count", retries=1)])["count"]["value"] == 6
    assert sql.call_count == 2


def test_read_only_and_natural_language_guards_remain_active(engine):
    with pytest.raises(sqlite3.DatabaseError):
        engine.backends["sql"].db.execute("DELETE FROM employees")
    assert engine.query("Delete all employees")["status"] == "abstained"
    assert engine.query("Count employees except Alice")["status"] == "abstained"
    assert engine.execute_dag([]) == {}
    assert engine.execute_dag([DAGNode("count", "sql", "count")])["count"]["value"] == 6


def test_ldbc_sql_to_graph_binding_and_nullable_path(tmp_path):
    people, edges = tmp_path / "people.csv", tmp_path / "knows.csv"
    people.write_text("id|firstName|lastName\n1|A|One\n2|B|Two\n3|C|Three\n", encoding="utf-8")
    edges.write_text("Person.id|Person.id|creationDate\n1|2|0\n", encoding="utf-8")
    with Engine(source="ldbc", people_path=people, edges_path=edges) as engine:
        nodes = [
            DAGNode("friends", "sql", "friends", {"source": "1"}),
            DAGNode("path", "graph", "shortest_path", {"target": "3"},
                    {"source": InputBinding("friends", (0, "id"))}),
        ]
        results = engine.execute_dag(nodes)
        assert results["friends"]["value"] == [{"id": "2", "first_name": "B", "last_name": "Two"}]
        assert results["path"]["value"] is None
        nodes.append(DAGNode("next", "graph", "friends", bindings={"source": InputBinding("path", (0,))}))
        with pytest.raises(DAGExecutionError) as error:
            engine.execute_dag(nodes)
        assert (error.value.node_id, error.value.stage, error.value.attempts) == ("next", "input validation", 0)
