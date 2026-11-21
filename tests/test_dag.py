import sqlite3
from contextlib import closing
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


@pytest.mark.integration
@pytest.mark.parametrize("source_data", ["ldbc-compact", "ldbc-dataset"], indirect=True)
def test_cross_backend_dag_with_forward_references_and_fan_in(source_data):
    source, target = source_data.endpoints
    peers = sorted(source_data.neighbor[source_data.center], key=int)
    with Engine(**source_data.kwargs) as engine:
        results = engine.execute_dag([
            DAGNode("count", "sql", "count_people", depends_on=("path",)),
            DAGNode("path", "graph", "shortest_path", bindings={
                "source": InputBinding("sql_friends", (peers.index(source), "id")),
                "target": InputBinding("graph_friends", (peers.index(target), "id")),
            }),
            DAGNode("sql_friends", "sql", "friends", {"source": source_data.center}),
            DAGNode("graph_friends", "graph", "friends", {"source": source_data.center}),
        ])
    assert list(results) == ["sql_friends", "graph_friends", "path", "count"]
    expected = [source_data.records[peer] for peer in peers]
    assert results["sql_friends"]["value"] == results["graph_friends"]["value"] == expected
    path = results["path"]["value"]
    # The two selected friends share the center, so a shortest path uses
    # one edge if they are adjacent and two otherwise. Ties are valid.
    assert path[0] == source and path[-1] == target
    assert len(path) == (2 if target in source_data.neighbor[source] else 3)
    assert all(b in source_data.neighbor[a] for a, b in zip(path, path[1:]))
    assert results["count"]["value"] == source_data.values["count_people"]


@pytest.mark.integration
@pytest.mark.parametrize(("source_data", "count", "statistic"), [
    ("chinook-compact", "count_tracks", "median"), ("chinook-dataset", "count_customers", "median"),
    ("taxi-compact", "count_trips", "median_fare"),
], indirect=["source_data"])
def test_tabular_dag_preserves_sql_and_statistics_results(source_data, count, statistic):
    with Engine(**source_data.kwargs) as engine:
        assert engine.execute_dag([]) == {}
        results = engine.execute_dag([
            DAGNode("statistic", "code", statistic, depends_on=("count",)),
            DAGNode("count", "sql", count),
        ])
    assert list(results) == ["count", "statistic"]
    assert results["count"]["value"] == source_data.values[count]
    assert results["statistic"]["value"] == pytest.approx(source_data.values[statistic])


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


@pytest.mark.security
@pytest.mark.parametrize(("source_data", "table", "count", "question"), [
    ("chinook-compact", "Invoice", "count_tracks", "How many tracks except Jazz?"),
    ("taxi-compact", "trips", "count_trips", "How many trips except airport trips?"),
], indirect=["source_data"])
def test_read_only_and_natural_language_guards_remain_active(source_data, table, count, question):
    with Engine(**source_data.kwargs) as engine:
        assert engine.backends["sql"].db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0
        for statement in (f"DELETE FROM {table}", "PRAGMA query_only=OFF", "ATTACH DATABASE ':memory:' AS other"):
            with pytest.raises(sqlite3.DatabaseError):
                engine.backends["sql"].db.execute(statement)
        assert engine.query(question)["status"] == "abstained"
        assert engine.query(f"Delete all rows from {table}")["status"] == "abstained"
        assert engine.execute_dag([DAGNode("count", "sql", count)])["count"]["value"] == source_data.values[count]


@pytest.mark.security
@pytest.mark.parametrize("source_data", ["ldbc-compact"], indirect=True)
def test_ldbc_public_plans_reject_arbitrary_sql_and_unknown_ids(source_data):
    with Engine(**source_data.kwargs, planner_kind="dag") as engine:
        count = DAGNode("count", "sql", "count_people")
        assert engine.query("Count people; then delete all rows from people")["status"] == "abstained"
        assert engine.query("How many people except person 21?")["status"] == "abstained"
        for unsupported in (
            DAGNode("write", "sql", "DELETE FROM people"),
            DAGNode("attach", "sql", "ATTACH DATABASE ':memory:' AS other"),
            DAGNode("cte", "sql", "WITH selected AS (SELECT id FROM people) DELETE FROM people"),
            DAGNode("friends", "sql", "friends", {"source": "21' OR '1'='1"}),
        ):
            with pytest.raises(DAGValidationError):
                engine.execute_dag([count, unsupported])
        result = engine.execute_dag([count, DAGNode("friends", "sql", "friends", {"source": source_data.center})])
        assert result["count"]["value"] == source_data.values["count_people"]
        assert result["friends"]["value"] == [
            source_data.records[person] for person in sorted(source_data.neighbor[source_data.center], key=int)
        ]


@pytest.mark.security
@pytest.mark.parametrize("source_data", ["ldbc-compact"], indirect=True)
@pytest.mark.parametrize("statement", [
    "INSERT INTO people VALUES ('999')",
    "UPDATE people SET id = '999' WHERE id = '21'",
    "DELETE FROM people",
    "CREATE TABLE scratch (id TEXT)",
    "CREATE TEMP TABLE scratch (id TEXT)",
    "DROP TABLE knows",
    "WITH new_person(id) AS (VALUES ('999')) INSERT INTO people SELECT id FROM new_person",
    "WITH selected AS (SELECT id FROM people) DELETE FROM people WHERE id IN (SELECT id FROM selected)",
    "INSERT INTO people VALUES ('999') RETURNING id",
    "PRAGMA query_only=OFF",
    "PRAGMA writable_schema=ON",
    "ATTACH DATABASE ':memory:' AS other",
    "SELECT load_extension('unused')",
])
def test_ldbc_raw_sql_connection_denies_writes(source_data, statement):
    with Engine(**source_data.kwargs) as engine:
        db = engine.backends["sql"].db
        with pytest.raises(sqlite3.DatabaseError, match="not authorized|authorization denied"):
            db.execute(statement)
        assert db.execute("PRAGMA query_only").fetchone() == (1,)
        assert set(db.execute("SELECT id FROM people")) == {(person,) for person in source_data.records}
        assert set(db.execute("SELECT source,target FROM knows")) == {
            (person, peer) for person, peers in source_data.neighbor.items() for peer in peers
        }


@pytest.mark.security
@pytest.mark.parametrize("source_data", ["ldbc-compact"], indirect=True)
def test_ldbc_cannot_attach_or_export_databases(source_data, tmp_path):
    attached, exported = tmp_path / "other.sqlite", tmp_path / "export.sqlite"
    with closing(sqlite3.connect(attached)) as external, external:
        external.execute("CREATE TABLE marker (id INTEGER)")
        external.execute("INSERT INTO marker VALUES (1)")
    original = attached.read_bytes()
    with Engine(**source_data.kwargs) as engine:
        db = engine.backends["sql"].db
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            db.execute("ATTACH DATABASE ? AS other", (str(attached),))
        with pytest.raises(sqlite3.DatabaseError, match="not authorized|authorization denied"):
            db.execute("VACUUM INTO ?", (str(exported),))
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            db.executescript("SELECT 1; DELETE FROM people;")
        assert db.execute("SELECT COUNT(*) FROM people").fetchone() == (source_data.values["count_people"],)
    assert attached.read_bytes() == original
    assert not exported.exists()


@pytest.mark.integration
@pytest.mark.parametrize("source_data", ["ldbc-compact"], indirect=True)
def test_ldbc_sql_paths_remain_read_only_across_frontier_batches(source_data, monkeypatch):
    monkeypatch.setattr("polyquery.ldbc_sql._FRONTIER_BATCH_SIZE", 1)
    source = source_data.center
    target = next(person for person, peers in source_data.neighbor.items() if set(source_data.endpoints) == peers
                  and person != source)
    isolated = next(person for person, peers in source_data.neighbor.items() if not peers)
    with Engine(**source_data.kwargs, policy="fixed-sql") as engine:
        db = engine.backends["sql"].db
        db.execute("BEGIN")
        assert db.execute("""
            WITH RECURSIVE numbers(n) AS (
                VALUES(1) UNION ALL SELECT n+1 FROM numbers WHERE n<3
            ) SELECT SUM(n) FROM numbers
        """).fetchone() == (6,)
        for end, expected in ((target, [source, source_data.endpoints[0], target]), (isolated, None),
                              (source, [source]), (target, [source, source_data.endpoints[0], target])):
            via_sql = engine.query(f"Shortest path from {source} to {end}")
            assert via_sql["status"] == "ok"
            assert via_sql["result"]["value"] == expected
            via_dag = engine.execute_dag([DAGNode("path", "sql", "shortest_path", {"source": source, "target": end})])
            assert via_dag["path"]["value"] == expected
            assert via_dag["path"]["artifact"].startswith("SELECT ")
        db.rollback()


@pytest.mark.integration
@pytest.mark.parametrize("source_data", ["ldbc-compact"], indirect=True)
def test_nullable_path_cannot_feed_an_entity_binding(source_data):
    isolated = next(person for person, peers in source_data.neighbor.items() if not peers)
    with Engine(**source_data.kwargs) as engine:
        nodes = [
            DAGNode("friends", "sql", "friends", {"source": source_data.center}),
            DAGNode("path", "graph", "shortest_path", {"target": isolated},
                    {"source": InputBinding("friends", (0, "id"))}),
        ]
        results = engine.execute_dag(nodes)
        assert results["friends"]["value"] == [
            source_data.records[person] for person in sorted(source_data.neighbor[source_data.center], key=int)
        ]
        assert results["path"]["value"] is None
        nodes.append(DAGNode("next", "graph", "friends", bindings={"source": InputBinding("path", (0,))}))
        with pytest.raises(DAGExecutionError) as error:
            engine.execute_dag(nodes)
        assert (error.value.node_id, error.value.stage, error.value.attempts) == ("next", "input validation", 0)
