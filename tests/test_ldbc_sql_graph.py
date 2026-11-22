"""Independent LDBC CSV and recursive SQL correctness and safety checks."""
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from benchmarks.ldbc_sql_graph import Reference
from polyquery.backends import UnsupportedPlan
from polyquery.catalog import build_catalog, dag_catalog
from polyquery.ldbc import LDBCGraph
from polyquery.ldbc_sql_graph import MAX_HOPS, LDBCSQLGraph, TraversalLimit, ingest
from polyquery.planner import Plan


def make_graph(tmp_path, size, pairs):
    people, edges = tmp_path / "people.csv", tmp_path / "knows.csv"
    people.write_text("id|firstName|lastName\n" + "".join(f"{key}|Person|{key}\n" for key in range(1, size+1)))
    edges.write_text("Person.id|Person.id|creationDate\n" + "".join(f"{a}|{b}|0\n" for a, b in pairs))
    graph = LDBCGraph(people, edges)
    catalog = dag_catalog(build_catalog(SimpleNamespace(source="ldbc", backends={"graph": graph},
                                                        source_metadata=graph.metadata)))
    return graph, catalog, Reference(people, edges, graph.people)


@pytest.mark.parametrize("operation, parameters", [
    ("count_people", {}),
    ("friends", {"source": "1"}),
    ("friends", {"source": "7"}),
    ("shortest_path", {"source": "1", "target": "2"}),
    ("shortest_path", {"source": "1", "target": "4"}),
    ("shortest_path", {"source": "4", "target": "1"}),
    ("shortest_path", {"source": "1", "target": "5"}),
    ("shortest_path", {"source": "7", "target": "7"}),
])
def test_cte_matches_independent_gold(tmp_path, operation, parameters):
    # Diamond with duplicate inputs in both directions, one self-loop and a
    # separate two-person component. Person 7 is isolated.
    graph, catalog, reference = make_graph(tmp_path, 7, [
        (1, 1), (1, 2), (2, 1), (1, 2), (1, 3), (2, 4), (3, 4), (5, 6)])
    with closing(sqlite3.connect(":memory:")) as db:
        ingest(db, graph, server=False)
        adapter = LDBCSQLGraph(db, graph, catalog)
        expected = reference.answer(Plan("graph", operation, parameters))
        assert graph.execute(Plan("graph", operation, parameters))["value"] == expected
        assert adapter.execute(Plan("sql", operation, parameters))["value"] == expected


def test_exact_at_limit_and_beyond_limit_is_explicit(tmp_path):
    graph, catalog, reference = make_graph(tmp_path, MAX_HOPS + 2, [(i, i+1) for i in range(1, MAX_HOPS + 2)])
    with closing(sqlite3.connect(":memory:")) as db:
        ingest(db, graph, server=False)
        adapter = LDBCSQLGraph(db, graph, catalog)
        exact = Plan("sql", "shortest_path", {"source": "1", "target": str(MAX_HOPS + 1)})
        assert adapter.execute(exact)["value"] == reference.answer(exact)
        with pytest.raises(TraversalLimit, match="depth bound"):
            adapter.execute(Plan("sql", "shortest_path", {"source": "1", "target": str(MAX_HOPS + 2)}))


def test_parameter_catalog_and_read_only_boundary(tmp_path):
    graph, catalog, _ = make_graph(tmp_path, 2, [(1, 2)])
    with closing(sqlite3.connect(":memory:")) as db:
        ingest(db, graph, server=False)
        adapter = LDBCSQLGraph(db, graph, catalog)
        for plan in (Plan("sql", "friends", {"source": "1 OR 1=1"}),
                     Plan("sql", "shortest_path", {"source": "1", "target": "2", "hops": "200"}),
                     Plan("graph", "friends", {"source": "1"})):
            with pytest.raises(UnsupportedPlan):
                adapter.execute(plan)
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("DELETE FROM knows")

def test_frontier_binds_original_ids_and_preserves_path_order():
    from unittest.mock import Mock

    from polyquery.ldbc_sql_frontier import LDBCSQLFrontier

    adapter = object.__new__(LDBCSQLFrontier)
    adapter.prefix = "pq_ldbc_128."
    cursor = Mock()
    cursor.execute.return_value = cursor
    cursor.fetchone.return_value = ("000000000400000000170000000002",)
    cursor.fetchall.return_value = [(2, "107"), (4, "412"), (17, "35184372090183")]
    assert adapter._paths(cursor, "412", "107", 0) == ["412", "35184372090183", "107"]
    statement, args = cursor.execute.call_args_list[0].args
    assert args == ("412", "107")
    assert "@seen" in statement
    assert "WHERE person_id = ?" in statement
    cursor.reset_mock()
    cursor.fetchone.return_value = None
    assert adapter._paths(cursor, "412", "107", 0) is None
    cursor.execute.assert_called_once()
