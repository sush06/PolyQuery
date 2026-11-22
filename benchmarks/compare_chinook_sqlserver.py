"""Compare the Chinook SQL engines against independent Python reductions."""

import argparse
import json
import math
import os
import platform
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from polyquery import DAGNode, Engine
from polyquery.evaluation import equivalent, timing_summary
from polyquery import sqlserver
from polyquery.sqlserver import CONNECTION_ENV, SQLServerError, STATEMENTS, file_hash


def independent_gold(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        totals = [value for value, in db.execute("SELECT Total FROM Invoice") if value is not None]
        count = lambda table: sum(1 for _ in db.execute(f"SELECT 1 FROM {table}"))
        return {
            "count_tracks": count("Track"), "count_customers": count("Customer"), "count_invoices": count("Invoice"),
            "revenue": math.fsum(totals) if totals else None,
            "mean": math.fsum(totals) / len(totals) if totals else None,
        }


def _timing(times):
    return {**timing_summary(times), "min": min(times), "max": max(times)}


def _metadata(engine, backend, path):
    if backend == "sqlite":
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
            indexes = [dict(zip(("name", "table", "sql"), row)) for row in db.execute(
                "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index' "
                "AND tbl_name IN ('Track','Customer','Invoice') ORDER BY tbl_name,name"
            )]
        return {"sqlite_version": sqlite3.sqlite_version, "indexes": indexes, "transport": "local file"}
    import pyodbc

    db = engine.backends["sql"].db
    with closing(db.cursor()) as cursor:
        version = cursor.execute("""
            SELECT CONVERT(nvarchar(128), SERVERPROPERTY('ProductVersion')),
                   CONVERT(nvarchar(128), SERVERPROPERTY('MachineName')),
                   CONVERT(nvarchar(128), SERVERPROPERTY('Edition')),
                   compatibility_level, is_read_only
            FROM sys.databases WHERE database_id = DB_ID()
        """).fetchone()
        indexes = [dict(zip(("table", "name", "type", "column", "key_ordinal"), row))
                   for row in cursor.execute("""
            SELECT t.name, i.name, i.type_desc, c.name, ic.key_ordinal
            FROM sys.tables AS t JOIN sys.indexes AS i ON i.object_id=t.object_id
            LEFT JOIN sys.index_columns AS ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
            LEFT JOIN sys.columns AS c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
            WHERE t.schema_id=SCHEMA_ID(?) AND i.index_id>0
            ORDER BY t.name,i.name,ic.index_column_id
        """, "polyquery")]
    return {**dict(zip(("product_version", "server_machine", "edition", "compatibility_level", "database_read_only"), version)),
            "driver": db.getinfo(pyodbc.SQL_DRIVER_NAME), "driver_version": db.getinfo(pyodbc.SQL_DRIVER_VER),
            "odbc_pooling_enabled": pyodbc.pooling, "indexes": indexes}


def _connection_probe(backend, path):
    started = time.perf_counter()
    if backend == "sqlite":
        db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    else:
        db, _ = sqlserver.connect_from_env(CONNECTION_ENV, autocommit=True)
    elapsed = (time.perf_counter() - started) * 1000
    db.close()
    return elapsed


def compare(path, *, repeats=30, warmup=5, backends=("sqlite", "sqlserver")):
    if repeats < 1 or warmup < 0:
        raise ValueError("Repeats must be positive and warmup nonnegative")
    snapshot = file_hash(path)
    gold = independent_gold(path)
    results = {}
    setups = {}
    for backend in backends:
        with Engine(source="chinook", data_path=path, sql_backend=backend) as engine:
            setups[backend] = {"engine_initialization_ms": engine.initialization_ms,
                               "configuration": _metadata(engine, backend, path)}
            operations = {}
            for name in STATEMENTS:
                observed, times = [], []
                node = DAGNode("answer", "sql", name)
                for _ in range(warmup):
                    engine.execute_dag([node])
                for _ in range(repeats):
                    started = time.perf_counter()
                    result = engine.execute_dag([node])["answer"]
                    times.append((time.perf_counter() - started) * 1000)
                    if backend == "sqlserver" and result.get("implementation") != "sqlserver":
                        raise SQLServerError("Benchmark did not reach SQL Server")
                    observed.append(result["value"])
                operations[name] = {"expected": gold[name], "observed": observed,
                                    "matches_gold": all(equivalent(value, gold[name]) for value in observed),
                                    "latency_ms": _timing(times), "samples_ms": times}
            results[backend] = operations
        setups[backend]["connection_probe_ms"] = _timing([_connection_probe(backend, path) for _ in range(repeats)])
    if snapshot != file_hash(path):
        raise ValueError("Chinook SQLite changed during benchmark")
    return {"fixture_sha256": snapshot, "repeats": repeats, "warmup_per_operation": warmup,
            "environment": {"machine": platform.node(), "platform": platform.platform(),
                            "python": platform.python_version(), "logical_cpus": os.cpu_count()},
            "connection_strategy": "One reused engine per backend; separate connection-only probes after query timings",
            "query_timing_scope": "One-node typed DAG dispatch, database query and fetch, and result validation",
            "dataset_rows": {"Track": gold["count_tracks"], "Customer": gold["count_customers"],
                             "Invoice": gold["count_invoices"]},
            "setup": setups, "results": results,
            "all_match": all(row["matches_gold"] for operations in results.values() for row in operations.values())}


def markdown(result):
    lines = ["# Chinook SQL backend benchmark", "",
             f"{result['repeats']} measured runs after {result['warmup_per_operation']} warm-ups per operation.",
             "", result["connection_strategy"] + ".", result["query_timing_scope"] + ".",
             f"Fixture SHA-256 {result['fixture_sha256']}. Rows {result['dataset_rows']}.",
             f"Client {result['environment']['machine']}, Python {result['environment']['python']}.", ""]
    if result.get("authentication"):
        lines += [result["authentication"], ""]
    lines += ["| Backend | Operation | Gold match | Min (ms) | p50 (ms) | p95 (ms) | Max (ms) |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for backend, operations in result["results"].items():
        for name, row in operations.items():
            t = row["latency_ms"]
            lines.append(f"| {backend} | {name} | {row['matches_gold']} | {t['min']:.3f} | {t['p50']:.3f} | {t['p95']:.3f} | {t['max']:.3f} |")
    lines += ["", "| Backend | Engine setup (ms) | Connect min / p50 / p95 / max (ms) |",
              "| --- | ---: | --- |"]
    for backend, setup in result["setup"].items():
        values = " / ".join(f"{setup['connection_probe_ms'][name]:.3f}" for name in ("min", "p50", "p95", "max"))
        lines.append(f"| {backend} | {setup['engine_initialization_ms']:.3f} | {values} |")
    lines += ["", "Index definitions, configuration, and individual samples are in the accompanying JSON.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--sqlite-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    try:
        options = {"backends": ("sqlite",)} if args.sqlite_only else {}
        result = compare(args.data, repeats=args.repeats, warmup=args.warmup, **options)
        payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        if args.markdown:
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            args.markdown.write_text(markdown(result), encoding="utf-8")
        print(payload, end="")
    except (OSError, ValueError, sqlite3.Error, SQLServerError) as error:
        parser.error(str(error))
    if not result["all_match"]:
        parser.exit(1)


if __name__ == "__main__":
    main()
