import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from benchmarks.compare_chinook_sqlserver import compare
from benchmarks.load_sqlserver_chinook import load
from polyquery import DAGNode, Engine
from polyquery.backends import UnsupportedPlan
from polyquery.planner import Plan
from polyquery.sqlserver import CONNECTION_ENV, ChinookSQLServer, SQLServerError, file_hash

from test_llm_dag import fake_chat, node


class DriverError(Exception):
    pass


class Cursor:
    def __init__(self, fixture_hash, permissions=None, result=((7,),)):
        self.fixture_hash = fixture_hash
        self.permissions = permissions or {}
        self.result = result
        self.calls = []
        self.bounds = []

    def execute(self, statement, *params):
        self.calls.append((statement, params))
        if "fn_my_permissions" in statement:
            name, scope = params
            defaults = {"SERVER": ("CONNECT SQL", "VIEW ANY DATABASE"),
                        "DATABASE": ("CONNECT",), "SCHEMA": (), "OBJECT": ("SELECT",)}
            self.rows = [(permission,) for permission in self.permissions.get((name, scope), defaults[scope])]
        elif "DatasetManifest" in statement:
            self.rows = [(self.fixture_hash,)]
        else:
            self.rows = self.result
        return self

    def fetchmany(self, limit):
        self.bounds.append(limit)
        return self.rows[:limit]

    def close(self):
        pass


def reader(monkeypatch, fixture_path, **kwargs):
    kwargs.setdefault("fixture_hash", file_hash(fixture_path))
    cursor = Cursor(**kwargs)
    connection = Mock()
    connection.cursor.return_value = cursor
    connector = Mock(return_value=(connection, DriverError))
    monkeypatch.setattr("polyquery.sqlserver.connect_from_env", connector)
    return cursor, connection, connector


@pytest.mark.parametrize("source_data", ["chinook-compact"], indirect=True)
@pytest.mark.parametrize("planner", ["keyword", "dag", "llm-dag", "llm"])
def test_sqlserver_reuses_chinook_planning_and_dag_slot(source_data, monkeypatch, planner):
    cursor, connection, connector = reader(monkeypatch, source_data.kwargs["data_path"],
                                           result=((source_data.values["count_tracks"],),))
    options = {}
    if planner == "llm-dag":
        options = {"model": "test", "llm_api": "chat", "llm_client": fake_chat({
            "kind": "single_plan", "outputs": ["tracks"], "nodes": [node("tracks", "sql", "count_tracks")],
        })}
    if planner == "llm":
        options = {"model": "test", "llm_api": "chat", "llm_client": fake_chat({
            "backend": "sql", "operation": "count_tracks", "parameters": [], "reason": "count",
        })}
    with Engine(**source_data.kwargs, sql_backend="sqlserver", planner_kind=planner, **options) as engine:
        result = engine.query("How many tracks?")["result"]
        assert result["value"] == source_data.values["count_tracks"]
        assert result["implementation"] == "sqlserver"
        assert engine.execute_dag([DAGNode("median", "sql", "median")])["median"]["value"] == source_data.values["median"]
        assert engine.source_metadata["sql_backend"] == "sqlserver"
        assert all(statement.startswith("SELECT ") for statement, _ in cursor.calls)
        assert cursor.calls[-2][1] == (1,)
        assert cursor.bounds[-1] == 2
    connector.assert_called_once_with(CONNECTION_ENV, autocommit=True)
    connection.close.assert_called_once()


@pytest.mark.parametrize("source_data", ["chinook-compact"], indirect=True)
@pytest.mark.parametrize("failure", [
    {"permissions": {(None, "SERVER"): ("CONTROL SERVER",)}},
    {"permissions": {(None, "DATABASE"): ("CONNECT", "ALTER ANY SCHEMA")}},
    {"permissions": {("polyquery", "SCHEMA"): ("ALTER",)}},
    {"permissions": {("polyquery.Invoice", "OBJECT"): ("SELECT", "UPDATE")}},
    {"permissions": {("polyquery.Track", "OBJECT"): ()}},
    {"fixture_hash": "incorrect"},
])
def test_startup_refuses_writable_mismatched_or_underprivileged_projection(source_data, monkeypatch, failure):
    _, connection, _ = reader(monkeypatch, source_data.kwargs["data_path"], **failure)
    with pytest.raises(SQLServerError):
        Engine(**source_data.kwargs, sql_backend="sqlserver")
    connection.close.assert_called_once()


@pytest.mark.parametrize("source_data", ["chinook-compact"], indirect=True)
def test_sqlserver_rejects_unapproved_plans_and_bounds_results(source_data, monkeypatch):
    path = source_data.kwargs["data_path"]
    cursor, _, _ = reader(monkeypatch, path, result=((1,), (2,), (3,)))
    fallback = Mock(operations={"count_tracks"})
    with_backend = ChinookSQLServer(fallback, path)
    try:
        calls = len(cursor.calls)
        for plan in (Plan("sql", "DELETE FROM Track"), Plan("sql", "count_tracks", {"sql": "DELETE FROM Track"}),
                     Plan("code", "count_tracks")):
            with pytest.raises(UnsupportedPlan):
                with_backend.execute(plan)
        assert len(cursor.calls) == calls
        with pytest.raises(SQLServerError, match="result shape"):
            with_backend.execute(Plan("sql", "count_tracks"))
        assert cursor.bounds[-1] == 2
        fallback.execute.assert_not_called()
    finally:
        with_backend.close()


@pytest.mark.parametrize("source_data", ["chinook-compact"], indirect=True)
def test_loader_uses_separate_transaction_and_parameterized_inserts(source_data, monkeypatch):
    db = Mock()
    cursor = db.cursor.return_value
    cursor.fetchone.return_value = (None,)
    connect = Mock(return_value=(db, DriverError))
    monkeypatch.setattr("benchmarks.load_sqlserver_chinook.connect_from_env", connect)
    fingerprint, counts = load(source_data.kwargs["data_path"])
    assert counts["Track"] == source_data.values["count_tracks"]
    assert all(args[0].endswith("VALUES (?)") for args, _ in cursor.executemany.call_args_list)
    assert cursor.execute.call_args.args[1:] == (1, fingerprint)
    db.commit.assert_called_once()
    db.close.assert_called_once()


@pytest.mark.parametrize("source_data", ["chinook-compact"], indirect=True)
def test_sqlite_only_comparison_keeps_independent_gold(source_data):
    result = compare(source_data.kwargs["data_path"], repeats=1, backends=("sqlite",))
    assert result["all_match"]
    assert set(result["results"]) == {"sqlite"}
    assert result["results"]["sqlite"]["mean"]["observed"] == [8.25]
    assert json.dumps(result, allow_nan=False)


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get(CONNECTION_ENV), reason="SQL Server reader is not configured")
def test_live_sqlserver_chinook_matches_sqlite_and_independent_gold():
    pyodbc = pytest.importorskip("pyodbc")
    path = Path(os.environ.get("POLYQUERY_SQLSERVER_CHINOOK_PATH", "benchmarks/Chinook_Sqlite.sqlite"))
    result = compare(path, repeats=1)
    assert result["all_match"]
    with Engine(source="chinook", data_path=path, sql_backend="sqlserver") as engine:
        db = engine.backends["sql"].db
        with pytest.raises(pyodbc.Error):
            db.execute("UPDATE [polyquery].[Invoice] SET [Total] = 0 WHERE 1 = 0")
