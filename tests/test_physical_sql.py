from pathlib import Path
from unittest.mock import Mock

import pytest

from benchmarks.chinook_scale_data import build_sqlite, read_seed, spans
from benchmarks.physical_routing import HeldOutWorkload, QUERIES, gold
from polyquery import DAGNode, Engine
from polyquery.backends import UnsupportedPlan
from polyquery.catalog import Catalog, OperationSpec, ParameterSpec, ResultSpec
from polyquery.physical_sql import PhysicalCostModel, PhysicalSQLRouter, SQLFeatures
from polyquery.planner import Plan
from test_sqlserver import reader


def registry():
    spec = OperationSpec(("sql",), ResultSpec("integer"))
    catalog = Catalog("fixture", {}, {"count": spec, "other": spec}, {})
    local = Mock()
    remote = Mock()
    local.execute.return_value = remote.execute.return_value = {"value": 7}
    return catalog, {"sqlite": ("same", {"count": spec}, local),
                     "sqlserver": ("same", {"count": spec}, remote)}


@pytest.mark.parametrize("selector, expected", [(None, "sqlite"), (lambda _: "sqlserver", "sqlserver")])
def test_physical_dispatch_and_typed_results(selector, expected):
    catalog, pair = registry()
    router = PhysicalSQLRouter(catalog, pair, **({"selector": selector} if selector else {}))
    plan = Plan("sql", "count")
    assert router.execute(plan) == {"value": 7, "physical_backend": expected}
    pair[expected][2].execute.assert_called_once_with(plan)
    pair[expected][2].execute.return_value = {"value": "7"}
    with pytest.raises(UnsupportedPlan, match="result contract"):
        router.execute(plan)


@pytest.mark.parametrize("failure", ["missing", "snapshot", "contract", "unavailable"])
def test_invalid_physical_registration(failure):
    catalog, pair = registry()
    snapshot, specs, adapter = pair["sqlserver"]
    if failure == "missing":
        del pair["sqlserver"]
    elif failure == "snapshot":
        pair["sqlserver"] = ("different", specs, adapter)
    elif failure == "contract":
        contract = OperationSpec(("sql",), ResultSpec("number"), {"x": ParameterSpec()})
        pair["sqlserver"] = (snapshot, {"count": contract}, adapter)
    else:
        pair["sqlserver"] = (snapshot, specs, None)
    with pytest.raises(ValueError):
        PhysicalSQLRouter(catalog, pair)


def test_no_dispatch_for_invalid_plans_and_no_fallback_on_outage():
    catalog, pair = registry()
    router = PhysicalSQLRouter(catalog, pair, lambda _: "sqlserver")
    local, remote = (pair[name][2] for name in ("sqlite", "sqlserver"))
    for plan in (Plan("sql", "other"), Plan("sql", "count", {"sql": "SELECT 1"}),
                 Plan("graph", "count"), Plan("sql", "unknown")):
        with pytest.raises(UnsupportedPlan):
            router.execute(plan)
    invalid = PhysicalSQLRouter(catalog, pair, lambda _: "unknown")
    with pytest.raises(UnsupportedPlan, match="Invalid physical"):
        invalid.execute(Plan("sql", "count"))
    remote.execute.assert_not_called()
    remote.execute.side_effect = RuntimeError("connection unavailable")
    with pytest.raises(RuntimeError, match="connection unavailable"):
        router.execute(Plan("sql", "count"))
    local.execute.assert_not_called()


def test_model_needs_calibration_and_uses_estimated_features():
    narrow = SQLFeatures(300, 0, 1, .1)
    broad = SQLFeatures(1_000_000, 3, 100, .7)
    model = PhysicalCostModel([(narrow, 1, 100), (broad, 1200, 100)])
    assert model.choose(narrow) == "sqlite"
    assert model.choose(broad) == "sqlserver"
    for rows in ([], [(narrow, float("nan"), 10)]):
        with pytest.raises(ValueError):
            PhysicalCostModel(rows)
    with pytest.raises(ValueError):
        model.choose(SQLFeatures(300, 0, 1, 1.1))


@pytest.mark.parametrize("source_data", ["chinook-compact"], indirect=True)
def test_engine_opt_in_applies_to_question_and_dag(source_data, monkeypatch):
    count = source_data.values["count_tracks"]
    _, connection, _ = reader(monkeypatch, source_data.kwargs["data_path"], result=((count,),))
    with Engine(**source_data.kwargs, physical_sql_selector=lambda _: "sqlserver") as engine:
        assert engine.query("How many tracks?")["result"]["physical_backend"] == "sqlserver"
        result = engine.execute_dag([DAGNode("tracks", "sql", "count_tracks")])["tracks"]
        assert result["physical_backend"] == "sqlserver"
        assert result["value"] == count
    connection.close.assert_called_once()
    with pytest.raises(ValueError):
        Engine(physical_sql_selector=lambda _: "sqlserver")


def test_family_disjoint_workloads_match_independent_reference_on_sqlite(tmp_path):
    from contextlib import closing
    import sqlite3

    fixture = Path(__file__).resolve().parents[1] / "benchmarks" / "Chinook_Sqlite.sqlite"
    seed, fingerprint = read_seed(fixture)
    path = tmp_path / "scaled.sqlite"
    build_sqlite(path, seed, fingerprint, 10)
    total = spans(seed)["invoice"] * 10
    windows = [(total * a // 100, total * b // 100) for a, b in ((3, 18), (41, 77))]
    parameters = {"lower": ParameterSpec(domain="lower"), "upper": ParameterSpec(domain="upper")}
    catalog = Catalog("scale", {}, {
        name: OperationSpec(("sql",), ResultSpec("rows", fields=dict.fromkeys(fields, "integer")), parameters)
        for name, (_, fields, _, _) in QUERIES.items()
    }, {key: {str(w[i]) for w in windows} for i, key in enumerate(parameters)})
    with closing(sqlite3.connect(path)) as db:
        workload = HeldOutWorkload(db, catalog, "", spans(seed)["genre"])
        for operation in QUERIES:
            for lower, upper in windows:
                plan = Plan("sql", operation, {"lower": str(lower), "upper": str(upper)})
                assert workload.execute(plan)["value"] == gold(seed, operation, lower, upper)
