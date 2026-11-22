"""Load an isolated LDBC SQL projection and compare indexed SQL, CTEs and BFS."""
import argparse
import csv
import hashlib
import json
import random
import sqlite3
import tracemalloc
from contextlib import ExitStack, closing
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from polyquery import DAGNode, sqlserver
from polyquery.catalog import build_catalog, dag_catalog
from polyquery.dag import DAGExecutionError, DAGExecutor
from polyquery.evaluation import timing_summary
from polyquery.ldbc import LDBCGraph, LDBCPlanner
from polyquery.ldbc_sql_frontier import LDBCSQLFrontier
from polyquery.ldbc_sql_graph import LDBCSQLGraph, TraversalLimit, ingest, scope_for
from polyquery.planner import Plan

ROOT = Path("benchmarks")
PEOPLE = ROOT / "people_0_0.csv"
EDGES = ROOT / "person_knows_person_0_0.csv"
EXISTING = ROOT / "ldbc-benchmark-dev.json"


def snapshot(people, edges, size):
    graph = LDBCGraph(people, edges)
    if not 1 <= size <= len(graph.people):
        raise ValueError("Requested size is outside the source person count")
    if size < len(graph.people):
        ids = set(sorted(graph.people, key=int)[:size])
        graph.people = {key: person for key, person in graph.people.items() if key in ids}
        graph.adjacency = {key: graph.adjacency[key] & ids for key in ids}
        graph.metadata = {"name": "ldbc", "people": size}
    return graph


class Reference:
    """Read raw rows independently and compute distance layers from the target."""

    def __init__(self, people, edges, ids):
        with people.open(encoding="utf-8-sig", newline="") as stream:
            self.people = {row["id"]: {"id": row["id"], "first_name": row["firstName"], "last_name": row["lastName"]}
                           for row in csv.DictReader(stream, delimiter="|") if row["id"] in ids}
        self.edges = {key: set() for key in ids}
        self.raw = 0
        with edges.open(encoding="utf-8-sig", newline="") as stream:
            rows = csv.reader(stream, delimiter="|")
            next(rows)
            for a, b, _ in rows:
                if a in ids and b in ids:
                    self.edges[a].add(b)
                    self.edges[b].add(a)
                    self.raw += 1

    def distances(self, target):
        layers, frontier = {target: 0}, {target}
        depth = 0
        while frontier:
            depth += 1
            frontier = {neighbor for node in frontier for neighbor in self.edges[node]} - layers.keys()
            layers.update(dict.fromkeys(frontier, depth))
        return layers

    def answer(self, plan):
        if plan.operation == "count_people":
            return len(self.people)
        source = plan.parameters["source"]
        if plan.operation == "friends":
            return [self.people[node] for node in sorted(self.edges[source], key=int)]
        target = plan.parameters["target"]
        distance = self.distances(target)
        if source not in distance:
            return None
        path = [source]
        while path[-1] != target:
            node = path[-1]
            path.append(min((n for n in self.edges[node] if distance[n] == distance[node] - 1), key=int))
        return path


def workload(graph, reference, include_existing):
    ids = sorted(graph.people, key=int)
    high = max(ids, key=lambda key: len(reference.edges[key]))
    low = min((key for key in ids if reference.edges[key]), key=lambda key: len(reference.edges[key]), default=ids[0])
    isolated = next((key for key in ids if not reference.edges[key]), None)
    cases = [("neighbors-low", Plan("graph", "friends", {"source": low})),
             ("neighbors-high", Plan("graph", "friends", {"source": high}))]
    if isolated is not None:
        cases.append(("neighbors-isolated", Plan("graph", "friends", {"source": isolated})))
    for label, source in (("sparse", low), ("dense", high)):
        distance = reference.distances(source)
        target_by_depth = {depth: next((target for target in ids if distance.get(target) == depth), None)
                           for depth in (0, 1, 2, 4, max(distance.values()))}
        target_by_depth["unreachable"] = next((target for target in ids if target not in distance), None)
        for depth, target in target_by_depth.items():
            if target is not None:
                cases.append((f"{label}-{depth}", Plan("graph", "shortest_path", {"source": source, "target": target})))
    if include_existing:
        planner = LDBCPlanner(graph)
        for row in json.loads(EXISTING.read_text(encoding="utf-8"))["cases"]:
            plan = planner.plan(row["question"])
            expected = None if plan.backend == "abstain" else reference.answer(plan)
            saved = row["expected"]
            if plan.operation == "shortest_path" and isinstance(saved, list) and expected is not None:
                matches = (len(expected) == len(saved) and expected[0] == saved[0] and expected[-1] == saved[-1]
                           and all(b in reference.edges.get(a, ()) for a, b in zip(saved, saved[1:])))
            else:
                matches = expected == saved and (plan.backend == "abstain") == (row["family"] == "abstention")
            if not matches:
                raise ValueError(f"Existing LDBC label differs from independent gold: {row['id']}")
            cases.append(("existing-" + row["id"], plan))
    return cases


def measure(catalog, adapters, cases, reference, repeats, warmups, rng):
    executors = {name: DAGExecutor(catalog, {"sql" if name != "bfs" else "graph": adapter})
                 for name, adapter in adapters.items()}
    result = []
    for case_id, logical in cases:
        if logical.backend == "abstain":
            result.append({"id": case_id, "operation": "unsupported_question", "status": "unsupported"})
            continue
        expected = reference.answer(logical)
        measurements = {name: {"query": [], "dag": []} for name in adapters}
        limits = {}
        for mode in ("query", "dag"):
            for round_id in range(warmups + repeats):
                order = list(adapters)
                rng.shuffle(order)
                for name in order:
                    if name in limits:
                        continue
                    backend = "graph" if name == "bfs" else "sql"
                    plan = Plan(backend, logical.operation, logical.parameters)
                    node = DAGNode("result", backend, plan.operation, plan.parameters)
                    try:
                        started = perf_counter()
                        value = (executors[name].execute([node])["result"] if mode == "dag"
                                 else adapters[name].execute(plan))["value"]
                        elapsed = (perf_counter() - started) * 1000
                    except (TraversalLimit, DAGExecutionError) as error:
                        reason = error.__cause__ if isinstance(error, DAGExecutionError) else error
                        if not isinstance(reason, TraversalLimit):
                            raise
                        limits[name] = {"reason": str(reason), "phase": mode}
                        continue
                    if value != expected:
                        raise ValueError(f"Independent gold mismatch: {name}/{case_id}")
                    if round_id >= warmups:
                        measurements[name][mode].append(elapsed)
        arms = {name: {"status": "limited", **limits[name]} if name in limits else
                {"status": "correct", **{mode: {**timing_summary(times), "samples_ms": times}
                                         for mode, times in modes.items()}}
                for name, modes in measurements.items()}
        source = logical.parameters.get("source")
        result.append({"id": case_id, "operation": logical.operation, "parameters": logical.parameters,
                       "source_degree": len(reference.edges[source]) if source else None,
                       "hops": len(expected) - 1 if logical.operation == "shortest_path" and expected else None,
                       "reachable": expected is not None if logical.operation == "shortest_path" else None,
                       "expected": expected, "backends": arms})
    return result


def load(people, edges, sizes, *, grant_lab_reader=False):
    db, _ = sqlserver.connect_from_env(sqlserver.LOAD_CONNECTION_ENV, autocommit=False)
    with closing(db):
        for size in sizes:
            graph = snapshot(people, edges, size)
            try:
                started = perf_counter()
                status = ingest(db, graph, server=True, grant_lab_reader=grant_lab_reader)
                db.commit()
                print(f"{size}: {status} in {(perf_counter() - started) * 1000:.1f} ms", flush=True)
            except BaseException:
                db.rollback()
                raise


def server_setup(stack, graph, catalog, timeout, *, frontier=False):
    started = perf_counter()
    connection, error = sqlserver.connect_from_env(sqlserver.CONNECTION_ENV, autocommit=True)
    stack.enter_context(closing(connection))
    connect_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    adapter = (LDBCSQLFrontier(connection, graph, catalog, timeout=timeout, driver_error=error)
               if frontier else LDBCSQLGraph(connection, graph, catalog, server=True,
                                              timeout=timeout, driver_error=error))
    verify_ms = (perf_counter() - started) * 1000
    with closing(connection.cursor()) as cursor:
        pages = cursor.execute(
            "SELECT SUM(CONVERT(BIGINT,a.used_pages)) FROM sys.partitions p "
            "JOIN sys.allocation_units a ON p.hobt_id=a.container_id AND a.type IN (1,3) "
            "JOIN sys.tables t ON t.object_id=p.object_id WHERE t.schema_id=SCHEMA_ID(?)",
            (scope_for(len(graph.people)),)).fetchone()[0]
    return adapter, {"sqlserver_connection_ms": connect_ms, "sqlserver_reader_verify_ms": verify_ms,
                     "sqlserver_in_row_and_overflow_bytes": None if pages is None else pages * 8192}


def run(people, edges, sizes, *, server, frontier=False, repeats=5, warmups=1, timeout=2, authentication=None):
    if frontier and not server:
        raise ValueError("The frontier arm requires SQL Server")
    if repeats < 2 or warmups < 1:
        raise ValueError("Use at least two measurements and one warm-up")
    rng = random.Random(6103)
    report = {"authentication": authentication, "timeout_seconds": timeout, "warmups": warmups,
              "repeats": repeats, "order_seed": 6103, "scales": {},
              "source_hashes": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                                for name, path in (("people", people), ("edges", edges))}}
    existing_label = json.loads(EXISTING.read_text(encoding="utf-8"))["metadata"]["source_hashes"]
    original = all(report["source_hashes"][name] == existing_label[name + "_file"] for name in ("people", "edges"))
    if not server:
        report["sqlserver_status"] = "not requested"
    for size in sizes:
        started = perf_counter()
        graph = snapshot(people, edges, size)
        bfs_load_ms = (perf_counter() - started) * 1000
        tracemalloc.start()
        try:
            memory_graph = snapshot(people, edges, size)
            bfs_python_memory, bfs_python_peak = tracemalloc.get_traced_memory()
            del memory_graph
        finally:
            tracemalloc.stop()
        reference = Reference(people, edges, graph.people.keys())
        catalog = dag_catalog(build_catalog(SimpleNamespace(
            source="ldbc", backends={"graph": graph}, source_metadata=graph.metadata)))
        with ExitStack() as stack:
            started = perf_counter()
            local = stack.enter_context(closing(sqlite3.connect(":memory:")))
            sqlite_connect_ms = (perf_counter() - started) * 1000
            started = perf_counter()
            ingest(local, graph, server=False)
            sqlite_payload = local.execute("PRAGMA page_count").fetchone()[0] * local.execute("PRAGMA page_size").fetchone()[0]
            adapters = {"bfs": graph, "sqlite": LDBCSQLGraph(local, graph, catalog, timeout=timeout)}
            setup = {"bfs_load_ms": bfs_load_ms,
                     "bfs_python_retained_bytes": bfs_python_memory, "bfs_python_peak_bytes": bfs_python_peak,
                     "sqlite_connection_ms": sqlite_connect_ms, "sqlite_load_and_verify_ms": (perf_counter() - started) * 1000,
                     "sqlite_page_bytes": sqlite_payload}
            if server:
                adapters["sqlserver"], sql_setup = server_setup(stack, graph, catalog, timeout)
                setup.update(sql_setup)
                if frontier:
                    adapters["sqlserver_frontier"], frontier_setup = server_setup(stack, graph, catalog, timeout,
                                                                                  frontier=True)
                    setup["frontier"] = frontier_setup
            cases = workload(graph, reference, original and size == 1528)
            rows = measure(catalog, adapters, cases, reference, repeats, warmups, rng)
            report["scales"][str(size)] = {"people": size, "raw_edges": reference.raw,
                                          "adjacency_entries": sum(map(len, reference.edges.values())),
                                          "initialization": setup, "cases": rows}
            print(f"{size}: checked {len(rows)} cases", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("load", "compare"))
    parser.add_argument("--people", type=Path, default=PEOPLE)
    parser.add_argument("--edges", type=Path, default=EDGES)
    parser.add_argument("--sizes", type=int, nargs="+", default=[128, 512, 1528])
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--frontier", action="store_true")
    parser.add_argument("--timeout", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("logs/ldbc-sql-graph.json"))
    args = parser.parse_args()
    if args.command == "load":
        load(args.people, args.edges, args.sizes)
        return
    report = run(args.people, args.edges, args.sizes, server=args.server, frontier=args.frontier, repeats=args.repeats,
                 warmups=args.warmups, timeout=args.timeout,
                 authentication="Configured least-privilege SQL Server reader" if args.server else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
