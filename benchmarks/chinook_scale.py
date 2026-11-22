"""Compare SQLite and SQL Server on an independently verified Chinook projection."""

import argparse
import json
import os
import platform
import random
import sqlite3
import time
from collections import Counter
from contextlib import ExitStack, closing
from pathlib import Path

from benchmarks.chinook_scale_data import FIELDS, SCALES, build_sqlite, build_sqlserver, counts, prefix, read_seed, spans, verify
from polyquery import DAGNode
from polyquery.backends import UnsupportedPlan
from polyquery.catalog import Catalog, OperationSpec, ParameterSpec, ResultSpec
from polyquery.dag import DAGExecutor
from polyquery.evaluation import equivalent, percentile, timing_summary
from polyquery.planner import Plan
from polyquery.sqlserver import CONNECTION_ENV, LOAD_CONNECTION_ENV, SQLServerError, _assert_reader, _permissions
from polyquery import sqlserver
from polyquery.tabular import lock_reads


WORKLOADS = {
    "filtered": ("SELECT COUNT(*), SUM(total_cents) FROM {s}invoice WHERE id <= ?", ("invoices", "cents")),
    "grouped": ("SELECT c.country_id, SUM(i.total_cents) FROM {s}invoice i JOIN {s}customer c "
                "ON c.id=i.customer_id WHERE i.id <= ? GROUP BY c.country_id ORDER BY c.country_id",
                ("country", "cents")),
    "joined": ("WITH facts AS (SELECT c.country_id, (g.id-1) % ? + 1 AS genre, l.unit_cents*l.quantity AS cents "
               "FROM {s}line l "
               "JOIN {s}invoice i ON i.id=l.invoice_id JOIN {s}customer c ON c.id=i.customer_id "
               "JOIN {s}track t ON t.id=l.track_id JOIN {s}genre g ON g.id=t.genre_id "
               "WHERE i.id <= ?) SELECT country_id, genre, SUM(cents) FROM facts "
               "GROUP BY country_id,genre ORDER BY country_id,genre",
               ("country", "genre", "cents")),
    "high_cardinality": ("SELECT customer_id, SUM(total_cents) FROM {s}invoice "
                         "WHERE id <= ? GROUP BY customer_id ORDER BY customer_id", ("customer", "cents")),
}
ROW_LIMIT = 65_536


def reference(seed, operation, bound):
    """Use standard-library reductions over unscaled source rows."""
    span = spans(seed)
    full, tail = divmod(bound, span["invoice"])
    customers = dict(seed["customer"])
    tracks = dict(seed["track"])

    def reduce(max_id):
        invoices = {key: (customer, cents) for key, customer, cents in seed["invoice"] if key <= max_id}
        if operation == "filtered":
            return len(invoices), sum(cents for _, cents in invoices.values())
        totals = Counter()
        if operation == "joined":
            for _, invoice, track, unit, quantity in seed["line"]:
                if invoice in invoices and tracks[track] is not None:
                    totals[customers[invoices[invoice][0]], tracks[track]] += unit * quantity
        else:
            for customer, cents in invoices.values():
                key = (customers[customer],) if operation == "grouped" else (customer,)
                totals[key] += cents
        return totals

    complete, partial = reduce(span["invoice"]), reduce(tail)
    fields = WORKLOADS[operation][1]
    if operation == "filtered":
        return [dict(zip(fields, (a*full+b for a,b in zip(complete, partial))))]
    if operation == "high_cardinality":
        return [{"customer": key + copy*span["customer"], "cents": cents}
                for copy in range(full + 1)
                for (key,), cents in sorted((complete if copy < full else partial).items())]
    keys = (complete.keys() if full else set()) | partial.keys()
    return [dict(zip(fields, (*key, complete[key]*full + partial[key]))) for key in sorted(keys)]


def _catalog(bounds, genre_span):
    return Catalog("chinook_scale", {}, {
        name: OperationSpec(("sql",), ResultSpec("rows", fields=dict.fromkeys(columns, "integer")),
                            {"upper_id": ParameterSpec(domain="bounds"),
                             **({"genre_span": ParameterSpec(domain="genre_span")} if name == "joined" else {})})
        for name, (_, columns) in WORKLOADS.items()
    }, {"bounds": {str(bound) for bound in bounds}, "genre_span": {str(genre_span)}})


class Workload:
    """Benchmark-only fixed SQL templates, using typed range parameters."""

    def __init__(self, db, catalog, scope=""):
        self.db, self.catalog, self.scope = db, catalog, scope

    def execute(self, plan):
        return self._fetch(*self._prepare(plan))

    def _prepare(self, plan):
        self.catalog.validate_plan(plan)
        if plan.backend != "sql":
            raise UnsupportedPlan("Scale benchmark only accepts SQL workload plans")
        template, fields = WORKLOADS[plan.operation]
        order = ("genre_span", "upper_id") if plan.operation == "joined" else ("upper_id",)
        return template.format(s=self.scope), fields, tuple(int(plan.parameters[name]) for name in order)

    def _fetch(self, statement, fields, parameters):
        with closing(self.db.cursor()) as cursor:
            cursor.execute(statement, parameters)
            rows = cursor.fetchmany(ROW_LIMIT + 1)
        if len(rows) > ROW_LIMIT:
            raise ValueError("Scale benchmark result exceeds its row bound")
        return {"value": [dict(zip(fields, row)) for row in rows]}


def _open_sqlite(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _open_server(scale):
    db, error = sqlserver.connect_from_env(CONNECTION_ENV, autocommit=True)
    try:
        with closing(db.cursor()) as cursor:
            _assert_reader(cursor)
            scope = prefix(scale)
            if _permissions(cursor, scope[:-1], "SCHEMA") - {"SELECT", "VIEW DEFINITION"}:
                raise SQLServerError("Scale benchmark schema requires a read-only reader")
            for table in (*FIELDS, "manifest"):
                rights = _permissions(cursor, scope + table, "OBJECT")
                if "SELECT" not in rights or rights - {"SELECT", "VIEW DEFINITION"}:
                    raise SQLServerError("Scale benchmark tables require SELECT-only access")
    except error:
        db.close()
        raise SQLServerError("SQL Server scale reader validation failed") from None
    except Exception:
        db.close()
        raise
    return db


def _times(samples):
    return {**timing_summary(samples), "min": min(samples), "max": max(samples), "samples_ms": samples}


def _paired_difference(sqlite, sqlserver, seed):
    delta = [remote-local for local, remote in zip(sqlite, sqlserver)]
    rng = random.Random(seed)
    draws = [percentile(rng.choices(delta, k=len(delta)), .5) for _ in range(3000)]
    interval = [percentile(draws, .025), percentile(draws, .975)]
    return {"median_sqlserver_minus_sqlite_ms": percentile(delta, .5), "ci95_ms": interval,
            "faster": "sqlite" if interval[0] > 0 else "sqlserver" if interval[1] < 0 else "unresolved"}


def _metadata(db, scale, server):
    with closing(db.cursor()) as cursor:
        if not server:
            indexes = {
                name: [{"name": index[1], "origin": index[3],
                        "keys": [row[0] for row in db.execute("SELECT name FROM pragma_index_info(?)", (index[1],))]}
                       for index in db.execute(f"PRAGMA index_list({name})")] for name in FIELDS
            }
            return {"version": sqlite3.sqlite_version, "indexes": indexes, "integer_primary_keys": list(FIELDS),
                    "logical_keys": {table: sorted([["id"], *(index["keys"] for index in secondary)])
                                     for table, secondary in indexes.items()}}
        version = cursor.execute("SELECT CONVERT(varchar(128),SERVERPROPERTY('ProductVersion')), "
                                 "CONVERT(varchar(128),SERVERPROPERTY('MachineName')), "
                                 "CONVERT(varchar(128),SERVERPROPERTY('Edition'))").fetchone()
        indexes = [list(row) for row in cursor.execute("""
            SELECT t.name,i.name,i.type_desc,c.name,ic.key_ordinal
            FROM sys.tables t JOIN sys.indexes i ON t.object_id=i.object_id
            JOIN sys.index_columns ic ON i.object_id=ic.object_id AND i.index_id=ic.index_id
            JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
            WHERE t.schema_id=SCHEMA_ID(?) AND ic.key_ordinal>0
            ORDER BY t.name,i.name,ic.key_ordinal
        """, prefix(scale)[:-1])]
        import pyodbc

        keys = {}
        for table, index, _, column, _ in indexes:
            keys.setdefault(table, {}).setdefault(index, []).append(column)
        space = cursor.execute("""
            SELECT COALESCE(SUM(CONVERT(bigint,a.total_pages)),0) * 8192
            FROM sys.tables AS t JOIN sys.partitions AS p ON p.object_id=t.object_id
            JOIN sys.allocation_units AS a ON (a.type IN (1,3) AND a.container_id=p.hobt_id)
                OR (a.type=2 AND a.container_id=p.partition_id)
            WHERE t.schema_id=SCHEMA_ID(?)
        """, prefix(scale)[:-1]).fetchone()[0]
        return {"version": version[0], "machine": version[1], "edition": version[2], "indexes": indexes,
                "allocated_bytes": space, "logical_keys": {table: sorted(indexes.values()) for table, indexes in keys.items()},
                "driver": db.getinfo(pyodbc.SQL_DRIVER_NAME), "pooling": pyodbc.pooling,
                "plan": "Not collected. SHOWPLAN permission was not granted to the reader."}


def compare(source, directory, scales, *, repeats=12, warmup=3, random_seed=7341, server=True):
    if repeats < 2 or warmup < 1:
        raise ValueError("At least two measurements and one warm-up are required")
    seed, fingerprint = read_seed(source)
    rng = random.Random(random_seed)
    report = {"source_sha256": fingerprint, "warmups": warmup, "repeats": repeats, "random_seed": random_seed,
              "environment": {"machine": platform.node(), "platform": platform.platform(), "python": platform.python_version(),
                              "logical_cpus": os.cpu_count()},
              "connection_strategy": "Measure and reuse one checked reader connection per engine and scale",
              "query_scope": "Fixed statement execution, bound parameters, fetch and result materialization; plan validation excluded",
              "dag_scope": "One-node typed DAG dispatch, query and fetch, and result validation", "scales": {}}
    for scale in scales:
        path = directory / f"chinook-{scale}x.sqlite"
        total = spans(seed)["invoice"] * scale
        bounds = {"narrow": max(1, total // 100), "broad": total * 9 // 10}
        genre_span = spans(seed)["genre"]
        catalog = _catalog(bounds.values(), genre_span)
        with ExitStack() as stack:
            adapters, executors, configuration = {}, {}, {}
            for name in ("sqlite", "sqlserver") if server else ("sqlite",):
                started = time.perf_counter()
                db = stack.enter_context(closing(_open_server(scale) if name == "sqlserver" else _open_sqlite(path)))
                setup = (time.perf_counter() - started) * 1000
                verify(db, seed, fingerprint, scale, server=name == "sqlserver")
                adapters[name] = Workload(db, catalog, prefix(scale) if name == "sqlserver" else "")
                executors[name] = DAGExecutor(catalog, {"sql": adapters[name]})
                configuration[name] = {**_metadata(db, scale, name == "sqlserver"), "connection_setup_ms": setup}
                if name == "sqlite":
                    lock_reads(db)
            if server and configuration["sqlserver"]["logical_keys"] != configuration["sqlite"]["logical_keys"]:
                raise ValueError(f"Unequal logical index keys at {scale}x")
            cases = []
            qualifying = {bound: reference(seed, "filtered", bound)[0]["invoices"] for bound in bounds.values()}
            for operation in WORKLOADS:
                for selectivity, bound in bounds.items():
                    expected = reference(seed, operation, bound)
                    parameters = {"upper_id": str(bound)}
                    if operation == "joined":
                        parameters["genre_span"] = str(genre_span)
                    plan = Plan("sql", operation, parameters)
                    node = DAGNode("result", "sql", operation, plan.parameters)
                    prepared = {name: adapter._prepare(plan) for name, adapter in adapters.items()}
                    query = {name: lambda name=name: adapters[name]._fetch(*prepared[name]) for name in adapters}
                    dag = {name: lambda name=name: executors[name].execute([node])["result"] for name in adapters}
                    passes = {name: equivalent(dag[name]()["value"], expected) for name in adapters}
                    if not all(passes.values()):
                        raise ValueError(f"Independent gold mismatch at {scale}x {operation} {selectivity}")
                    modes = {}
                    for mode, functions in (("query", query), ("dag", dag)):
                        timings = {name: [] for name in adapters}
                        for iteration in range(warmup + repeats):
                            order = list(functions)
                            rng.shuffle(order)
                            for name in order:
                                started = time.perf_counter()
                                functions[name]()
                                elapsed = (time.perf_counter()-started)*1000
                                if iteration >= warmup:
                                    timings[name].append(elapsed)
                        modes[mode] = {"backends": {name: _times(samples) for name, samples in timings.items()},
                                       "paired": _paired_difference(timings["sqlite"], timings["sqlserver"], random_seed)
                                       if server else None}
                    template, _, bindings = prepared["sqlite"]
                    steps = adapters["sqlite"].db.execute("EXPLAIN QUERY PLAN " + template, bindings).fetchall()
                    cases.append({"operation": operation, "selectivity": selectivity, "upper_invoice_id": bound,
                                  "parameters": parameters, "invoice_id_fraction": bound/total,
                                  "qualifying_invoice_rows": qualifying[bound], "gold_rows": len(expected), "correct": passes,
                                  "sqlite_plan": steps, "modes": modes})
            report["scales"][str(scale)] = {"rows": counts(seed, scale), "sqlite_bytes": path.stat().st_size,
                                             "configuration": configuration, "cases": cases}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build-sqlite", "build-sqlserver", "compare"))
    parser.add_argument("--source", type=Path, default=Path("benchmarks/Chinook_Sqlite.sqlite"))
    parser.add_argument("--directory", type=Path, default=Path("logs/chinook-scale"))
    parser.add_argument("--scales", type=int, nargs="+", choices=SCALES, default=[1, 10, 100])
    parser.add_argument("--sqlite-only", action="store_true")
    parser.add_argument("--repeats", type=int, default=12)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("logs/chinook-scale.json"))
    args = parser.parse_args()
    try:
        if args.action == "compare":
            result = compare(args.source, args.directory, args.scales, repeats=args.repeats,
                             warmup=args.warmup, server=not args.sqlite_only)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            print(args.output)
        else:
            seed, fingerprint = read_seed(args.source)
            for scale in args.scales:
                if args.action == "build-sqlite":
                    build_sqlite(args.directory / f"chinook-{scale}x.sqlite", seed, fingerprint, scale)
                else:
                    db, error = sqlserver.connect_from_env(LOAD_CONNECTION_ENV, autocommit=False)
                    with closing(db):
                        try:
                            build_sqlserver(db, seed, fingerprint, scale)
                        except error:
                            raise SQLServerError(f"SQL Server scale {scale}x loading failed") from None
                print(f"{scale}x verified: {counts(seed, scale)}")
    except (OSError, ValueError, sqlite3.Error, SQLServerError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
