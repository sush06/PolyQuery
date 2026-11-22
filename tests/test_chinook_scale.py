from contextlib import closing
import sqlite3

import pytest

from benchmarks.chinook_scale import WORKLOADS, Workload, _catalog, _paired_difference, reference
from benchmarks.chinook_scale_data import build_sqlite, verify
from polyquery import DAGNode
from polyquery.backends import UnsupportedPlan
from polyquery.dag import DAGExecutor
from polyquery.planner import Plan


@pytest.fixture
def seed():
    return {
        "country": [(1,), (2,)], "customer": [(1, 1), (2, 2)],
        "genre": [(1,)], "track": [(1, 1), (2, None)],
        "invoice": [(1, 1, 200), (2, 2, 300)],
        "line": [(1, 1, 1, 200, 1), (2, 2, 2, 300, 1)],
    }


def test_replicated_foreign_keys_and_workloads_match_independent_references(tmp_path, seed):
    path = tmp_path / "scale.sqlite"
    fixture = "a" * 64
    build_sqlite(path, seed, fixture, 10)
    with closing(sqlite3.connect(path)) as db:
        verify(db, seed, fixture, 10, server=False)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT invoice_id,track_id FROM line WHERE id=20").fetchone() == (20, 20)
        catalog = _catalog((1, 19, 20), 1)
        executor = DAGExecutor(catalog, {"sql": Workload(db, catalog)})
        for limit in (1, 19, 20):
            for operation in WORKLOADS:
                parameters = {"upper_id": str(limit)}
                if operation == "joined":
                    parameters["genre_span"] = "1"
                output = executor.execute([DAGNode("result", "sql", operation, parameters)])
                assert output["result"]["value"] == reference(seed, operation, limit)


def test_workload_contract_rejects_untyped_and_unlisted_inputs():
    adapter = Workload(None, _catalog((1, 2), 1))
    for plan in (Plan("sql", "arbitrary", {"upper_id": "1"}),
                 Plan("sql", "filtered", {"upper_id": "1 OR 1=1"}),
                 Plan("sql", "filtered", {"upper_id": "3"})):
        with pytest.raises(UnsupportedPlan):
            adapter.execute(plan)


def test_paired_bootstrap_direction_and_unresolved_noise():
    assert _paired_difference([1] * 12, [2] * 12, 7)["faster"] == "sqlite"
    assert _paired_difference([2] * 12, [1] * 12, 7)["faster"] == "sqlserver"
    assert _paired_difference([1, 2] * 6, [2, 1] * 6, 7)["faster"] == "unresolved"
