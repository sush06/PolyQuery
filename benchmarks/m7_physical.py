"""Independent benchmark for frozen physical selectors over verified plans."""
import argparse
import json
import math
import platform
import random
from collections import Counter
from contextlib import ExitStack, closing
from dataclasses import asdict, replace
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from benchmarks import chinook_scale_data as data
from benchmarks.chinook_scale import Workload, _open_server, _open_sqlite
from benchmarks.intelligent_routing import digest, read, write
from benchmarks.ldbc_sql_graph import EDGES, PEOPLE, Reference, snapshot
from benchmarks.physical_routing import MANIFEST as OLD_MANIFEST, calibration
from polyquery import DAGNode, sqlserver
from polyquery.catalog import Catalog, OperationSpec, ParameterSpec, ResultSpec, build_catalog, dag_catalog
from polyquery.dag import DAGExecutor
from polyquery.evaluation import equivalent, percentile
from polyquery.intelligent_router import LearnedCosts, features
from polyquery.ldbc_sql_graph import TraversalLimit
from polyquery.ldbc_sql_frontier import LDBCSQLFrontier
from polyquery.physical_sql import SQLFeatures
from polyquery.planner import Plan
from polyquery.tabular import lock_reads
from types import SimpleNamespace

ROOT = Path("benchmarks")
MANIFEST = ROOT/"m7-physical-manifest.json"
OLD_SQL = ROOT/"m7-sql-calibration-v1.json"
PROFILE = ROOT/"m6-selector-profile-v1.json"
SOURCE = ROOT/"Chinook_Sqlite.sqlite"
FOLDERS = Path("logs/chinook-scale-run")
QUERIES = {
    "invoice_price_frequency": (
        "SELECT total_cents, COUNT(*) FROM {s}invoice WHERE id > ? AND id <= ? GROUP BY total_cents ORDER BY total_cents", 0),
    "invoice_numeric_bands": (
        "SELECT total_cents / 500, COUNT(*) FROM {s}invoice WHERE id > ? AND id <= ? "
        "GROUP BY total_cents / 500 ORDER BY total_cents / 500", 0),
    "billed_country_reach": (
        "SELECT c.country_id, COUNT(DISTINCT c.id) FROM {s}customer c "
        "JOIN {s}invoice i ON c.id=i.customer_id JOIN {s}line l ON l.invoice_id=i.id "
        "JOIN {s}track t ON t.id=l.track_id WHERE i.id > ? AND i.id <= ? AND t.genre_id IS NOT NULL "
        "GROUP BY c.country_id ORDER BY c.country_id", 3),
}


def allow_scales():
    data.SCALES = tuple(sorted(set(data.SCALES) | {25, 250}))


class SQLWorkload(Workload):
    def _prepare(self, plan):
        self.catalog.validate_plan(plan)
        template, _ = QUERIES[plan.operation]
        return template.format(s=self.scope), ("key", "count"), tuple(
            int(plan.parameters[name]) for name in ("lower", "upper"))


def sql_gold(seed, operation, lower, upper):
    people, tracks = dict(seed["customer"]), dict(seed["track"])
    invoice_span = data.spans(seed)["invoice"]
    qualified = {invoice for _, invoice, track, _, _ in seed["line"] if tracks[track] is not None}
    result = Counter()
    for offset in range(lower//invoice_span, (upper-1)//invoice_span+1):
        members = set()
        for invoice, customer, cost in seed["invoice"]:
            if lower < offset*invoice_span+invoice <= upper:
                if operation == "billed_country_reach":
                    if invoice in qualified:
                        members.add(customer)
                else:
                    result[cost if operation == "invoice_price_frequency" else cost//500] += 1
        result.update(people[c] for c in members)
    return [{"key": k, "count": n} for k,n in sorted(result.items())]


class TimedAdapter:
    def __init__(self, target, logical=None, trace=None):
        self.target, self.logical = target, logical
        self.trace = trace

    def execute(self, plan):
        start = perf_counter()
        try:
            value = self.target.execute(replace(plan, backend=self.logical) if self.logical else plan)
        except Exception:
            self.trace.append({"operation": plan.operation, "ms": (perf_counter()-start)*1000, "completed": False})
            raise
        self.trace.append({"operation": plan.operation, "ms": (perf_counter()-start)*1000, "completed": True})
        return value


def sample(factory, backend, nodes, gold, expected, warm_executor=None):
    with ExitStack() as stack:
        trace = []
        start = perf_counter()
        executor = warm_executor or factory(stack, backend, trace)
        setup = 0 if warm_executor else (perf_counter()-start)*1000
        if warm_executor:
            trace = executor.backends[nodes[0].backend].trace
            trace.clear()
        start = perf_counter()
        try:
            results = executor.execute(nodes)
        except Exception as error:
            elapsed = (perf_counter()-start)*1000
            reason = error.__cause__ or error
            code = reason.args[0] if reason.args else None
            status = "timeout" if isinstance(reason, TraversalLimit) or code in ("HYT00", "HYT01") else "failure"
            error_type = type(reason).__name__
        else:
            elapsed = (perf_counter()-start)*1000
            if any(not expected(node, results[node.id]["value"], gold[node.id]) for node in nodes):
                raise ValueError("Physical result differs from independent gold")
            status, error_type = "completed", None
        return {"status": status, "error_type": error_type, "setup_ms": setup,
                "execution_ms": elapsed, "nodes": list(trace)}


def run_case(case, nodes, gold, expected, factory, backends, decisions, manifest, rng):
    rows = []
    with ExitStack() as shared:
        warm = {name: factory(shared, name, []) for name in backends}
        for name in rng.sample(list(backends), len(backends)):
            sample(factory, name, nodes, gold, expected, warm[name])
        for mode, reps in (("warm", manifest["warm_repetitions"]), ("cold", manifest["cold_repetitions"])):
            for repeat in range(reps):
                selections = {}
                for policy, choose in decisions.items():
                    start = perf_counter()
                    chosen, predictions = choose()
                    if chosen not in backends:
                        raise ValueError("Policy chose an ineligible backend")
                    selections[policy] = {"backend": chosen, "predictions": predictions,
                                          "selection_ms": (perf_counter()-start)*1000}
                observed = {}
                order = rng.sample(list(backends), len(backends))
                for name in order:
                    observed[name] = sample(factory, name, nodes, gold, expected, warm[name] if mode=="warm" else None)
                rows.append({"mode": mode, "repeat": repeat, "backend_order": order,
                             "eligible": list(backends), "selections": selections, "observed": observed})
    return {**case, "logical_plan": [asdict(n) for n in nodes], "gold": gold, "trials": rows}


def fixed(name):
    return lambda: (name, {})


def relational(partition, split, manifest, rng, model):
    seed, fingerprint = data.read_seed(SOURCE)
    for scale in partition["scales"]:
        size = data.spans(seed)["invoice"] * scale
        for operation in partition["families"]:
            _, joins = QUERIES[operation]
            for lo, hi in partition["windows"]:
                lower, upper = size*lo//100, size*hi//100
                params = {"lower": str(lower), "upper": str(upper)}
                node = DAGNode("out", "sql", operation, params)
                catalog = Catalog("chinook_scale", {}, {operation: OperationSpec(
                    ("sql",), ResultSpec("rows", fields={"key":"integer", "count":"integer"}),
                    {key: ParameterSpec(domain=key) for key in params})},
                    {key:{value} for key,value in params.items()})
                gold = {"out": sql_gold(seed, operation, lower, upper)}
                rows = data.counts(seed, scale)
                f = SQLFeatures(rows["line"] if joins else rows["invoice"], joins,
                                rows["country"] if joins else min(rows["invoice"], 100),
                                (upper-lower)/size)

                def factory(stack, backend, trace):
                    remote = backend == "sqlserver"
                    db = stack.enter_context(closing(_open_server(scale) if remote else
                        _open_sqlite(FOLDERS/f"chinook-{scale}x.sqlite")))
                    if remote:
                        db.timeout = 2
                    else:
                        lock_reads(db)
                    return DAGExecutor(catalog, {"sql": TimedAdapter(SQLWorkload(
                        db, catalog, data.prefix(scale) if remote else ""), trace=trace)})

                # Verify the matching fixture once outside timings.
                with ExitStack() as stack:
                    for backend in ("sqlite", "sqlserver"):
                        executor = factory(stack, backend, [])
                        db = executor.backends["sql"].target.db
                        # A separate checked connection is needed for SQLite PRAGMAs in verify().
                        if backend == "sqlite":
                            db = stack.enter_context(closing(_open_sqlite(FOLDERS/f"chinook-{scale}x.sqlite")))
                        data.verify(db, seed, fingerprint, scale, server=backend=="sqlserver")

                def choose():
                    selected = model.choose(f)
                    vector = f.vector()
                    nearest = sorted((math.dist(vector,p), y) for p,y in model.points)[:5]
                    weights = [1/(.25+d*d) for d,_ in nearest]
                    estimate = sum(w*y for w,(_,y) in zip(weights, nearest))/sum(weights)
                    return selected.value, {"log_latency_ratio": estimate}

                heuristic = "sqlserver" if joins >= 2 and f.rows*f.selectivity >= 100_000 else "sqlite"
                yield run_case({"split":split, "source":"chinook_scale", "family":operation, "scale":scale,
                               "window":[lo,hi], "features":asdict(f)}, [node], gold,
                               lambda n,a,b: equivalent(a,b), factory, ("sqlite","sqlserver"),
                               {"fixed_local":fixed("sqlite"), "fixed_server":fixed("sqlserver"),
                                "heuristic":fixed(heuristic), "learned":choose}, manifest, rng)


def graph(partition, split, manifest, rng, model):
    for scale in partition["scales"]:
        initial = snapshot(PEOPLE, EDGES, scale)
        ref = Reference(PEOPLE, EDGES, initial.people)
        ids = sorted(initial.people, key=int)
        start = max(ids, key=lambda key: len(ref.edges[key]))
        distances = ref.distances(start)
        near = next(k for k in ids if distances.get(k)==1)
        far = max(distances, key=lambda k:(distances[k],-int(k)))
        detached = next((k for k in ids if k not in distances), None)
        for family in partition["families"]:
            if family == "closed_and_open_routes" and detached is None:
                continue
            specs = ([(start, near),(start, far)] if family == "same_start_two_routes" else
                     [(start, detached),(start, near)] if family == "closed_and_open_routes" else [(start, far)])
            nodes = [DAGNode(f"path_{i}", "graph", "shortest_path", {"source":a,"target":b})
                     for i,(a,b) in enumerate(specs)]
            if family == "neighbor_count_and_route":
                nodes.insert(0,DAGNode("count", "graph", "count_people"))
            gold = {n.id:ref.answer(Plan("graph",n.operation,n.parameters)) for n in nodes}
            edges = sum(map(len, initial.adjacency.values()))

            def factory(stack, backend, trace):
                g = snapshot(PEOPLE, EDGES, scale)
                catalog = dag_catalog(build_catalog(SimpleNamespace(source="ldbc", backends={"graph": g},
                                                                     source_metadata=g.metadata)))
                if backend == "graph":
                    adapter = TimedAdapter(g, trace=trace)
                else:
                    db, driver_error = sqlserver.connect_from_env(sqlserver.CONNECTION_ENV, autocommit=True)
                    stack.enter_context(closing(db))
                    adapter = TimedAdapter(LDBCSQLFrontier(db,g,catalog,timeout=2,driver_error=driver_error),
                                           logical="sql",trace=trace)
                return DAGExecutor(catalog, {"graph":adapter})

            names = ("graph","sqlserver_frontier")
            def choose():
                # These lanes select per node. The existing model is called
                # separately and the benchmark only admits uniform choices.
                choices, predictions = [], []
                for n in nodes:
                    fs = {name:features("ldbc",n.operation, rows=scale,edges=edges,
                                        degree=len(initial.adjacency.get(n.parameters.get("source"),())),
                                        identical=n.parameters.get("source")==n.parameters.get("target"),
                                        backend=name) for name in names}
                    backend, pred, reason = model.choose("ldbc", n.operation, fs, "graph")
                    choices.append(backend)
                    predictions.append({"node":n.id,"values":pred,"reason":reason})
                if len(set(choices)) != 1:
                    raise ValueError("Mixed choices require per-node measurement")
                return choices[0], predictions

            yield run_case({"split":split,"source":"ldbc","family":family,"scale":scale,
                            "depths":[len(gold[n.id])-1 if isinstance(gold[n.id],list) else None
                                      for n in nodes if n.operation=="shortest_path"],
                            "start_degree":len(initial.adjacency[start])}, nodes,gold,
                           lambda n,a,b: equivalent(a,b),factory,names,
                           {"fixed_local":fixed("graph"),"fixed_server":fixed("sqlserver_frontier"),
                            "heuristic":fixed("graph"), "learned":choose},manifest,rng)


def cost(observation, penalty):
    return observation["setup_ms"] + (observation["execution_ms"]
                if observation["status"]=="completed" else penalty)


def summarize(rows, penalty):
    cohorts = {}
    for split in ("development","heldout"):
        for mode in ("cold","warm"):
            cases = [x for x in rows if x["split"]==split]
            values = []
            for case in cases:
                trials = [t for t in case["trials"] if t["mode"]==mode]
                backends = trials[0]["eligible"]
                mean_cost = {name:mean(cost(t["observed"][name],penalty) for t in trials) for name in backends}
                oracle = min(mean_cost,key=mean_cost.get)
                policies = {"oracle":{"backend":oracle,"penalized_ms":mean_cost[oracle],
                                      "completed":sum(t["observed"][oracle]["status"]=="completed" for t in trials)}}
                for policy in trials[0]["selections"]:
                    choice = trials[0]["selections"][policy]["backend"]
                    overhead = mean(t["selections"][policy]["selection_ms"] for t in trials)
                    datum = {"backend":choice,"selection_ms":overhead,"penalized_ms":mean_cost[choice]+overhead,
                             "regret_ms":mean_cost[choice]+overhead-mean_cost[oracle],
                             "completed":sum(t["observed"][choice]["status"]=="completed" for t in trials)}
                    policies[policy]=datum
                values.append({"case": {k:v for k,v in case.items() if k not in ("logical_plan","gold","trials")},
                               "oracle_backend":oracle,"policies":policies})
            rng = random.Random(441)
            n = len(values)
            comparisons = {}
            for baseline in ("fixed_local","fixed_server","heuristic"):
                diffs = [x["policies"]["learned"]["penalized_ms"]-x["policies"][baseline]["penalized_ms"] for x in values]
                draws = [mean(rng.choices(diffs,k=n)) for _ in range(2000)]
                comparisons[baseline] = {"mean_paired_delta_ms":mean(diffs),
                                         "bootstrap_95_ms":[percentile(draws,.025),percentile(draws,.975)]}
            cohorts[f"{split}/{mode}"] = {"cases":values,"paired":comparisons, "policies": {
                policy: {"average_penalized_ms":mean(x["policies"][policy]["penalized_ms"] for x in values),
                         "completed":sum(x["policies"][policy]["completed"] for x in values),
                         "trials":n*len(trials),
                         "oracle_matches":sum(x["policies"][policy]["backend"]==x["oracle_backend"] for x in values)}
                for policy in values[0]["policies"]}}
    return cohorts


def run(output, authentication):
    allow_scales()
    manifest = read(MANIFEST)
    archived = read(OLD_SQL)
    sql_model = calibration(archived,read(OLD_MANIFEST))
    p = read(PROFILE)
    graph_model = LearnedCosts(p["rows"],timeout_penalty_ms=p["timeout_penalty_ms"])
    rng = random.Random(manifest["seed"])
    rows = []
    for split in ("development","heldout"):
        for domain, engine, model in (("relational",relational,sql_model),("graph",graph,graph_model)):
            for row in engine(manifest[domain][split],split,manifest,rng,model):
                rows.append(row)
                print(split,domain,row["family"],row["scale"],flush=True)
    write(output, {"manifest":manifest,"manifest_sha256":digest(MANIFEST),
                   "profiles":{"relational_sha256":archived["original_sha256"],
                               "relational_archive_sha256":digest(OLD_SQL),"graph_sha256":digest(PROFILE)},
                   "machine":platform.node(),"authentication":authentication,
                   "rows":rows,"summary":summarize(rows,manifest["failure_penalty_ms"])})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,default=Path("logs/m7-physical-results.json"))
    p.add_argument("--load",action="store_true")
    args = p.parse_args()
    allow_scales()
    if args.load:
        seed, fingerprint = data.read_seed(SOURCE)
        for scale in (25,250):
            path = FOLDERS/f"chinook-{scale}x.sqlite"
            if not path.exists():
                data.build_sqlite(path,seed,fingerprint,scale)
            db,_ = sqlserver.connect_from_env(sqlserver.LOAD_CONNECTION_ENV,autocommit=False)
            with closing(db):
                data.build_sqlserver(db,seed,fingerprint,scale)
    else:
        run(args.output,"Configured read-only SQL Server reader")


if __name__ == "__main__":
    main()
