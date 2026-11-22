"""Calibrate from the earlier scale run and evaluate new, disjoint SQL families."""

import argparse
import hashlib
import json
import platform
import random
from collections import Counter
from contextlib import ExitStack, closing
from pathlib import Path
from statistics import median
from time import perf_counter

from benchmarks.chinook_scale import Workload, _metadata, _open_server, _open_sqlite, _times
from benchmarks.chinook_scale_data import counts, prefix, read_seed, spans, verify
from polyquery import DAGNode
from polyquery.catalog import Catalog, OperationSpec, ParameterSpec, ResultSpec
from polyquery.dag import DAGExecutor
from polyquery.evaluation import percentile
from polyquery.physical_sql import PhysicalCostModel, PhysicalSQLRouter, SQLFeatures
from polyquery.planner import Plan
from polyquery.tabular import lock_reads


MANIFEST = Path(__file__).with_name("physical-routing-manifest.json")
BACKENDS = ("sqlite", "sqlserver")
ARMS = (*BACKENDS, "routed", "oracle")
QUERIES = {
    "active_customers": (
        "SELECT c.country_id, COUNT(*) FROM {s}customer c WHERE EXISTS "
        "(SELECT 1 FROM {s}invoice i WHERE i.customer_id=c.id AND i.id > ? AND i.id <= ?) "
        "GROUP BY c.country_id ORDER BY c.country_id", ("country", "customers"), 1, "country"),
    "basket_histogram": (
        "WITH baskets AS (SELECT invoice_id, SUM(quantity) AS units FROM {s}line "
        "WHERE invoice_id > ? AND invoice_id <= ? GROUP BY invoice_id) "
        "SELECT units, COUNT(*) FROM baskets GROUP BY units ORDER BY units",
        ("units", "invoices"), 0, "invoice"),
    "track_coverage": (
        "WITH sold AS (SELECT DISTINCT track_id FROM {s}line WHERE invoice_id > ? AND invoice_id <= ?), "
        "classified AS (SELECT (t.genre_id-1) % ? + 1 AS genre FROM sold "
        "JOIN {s}track t ON t.id=sold.track_id WHERE t.genre_id IS NOT NULL) "
        "SELECT genre, COUNT(*) FROM classified GROUP BY genre ORDER BY genre",
        ("genre", "tracks"), 1, "track"),
}


def gold(seed, operation, lower, upper):
    """Reduce each intersecting unscaled copy, independently of the SQL."""
    span = spans(seed)["invoice"]
    customers, tracks = dict(seed["customer"]), dict(seed["track"])
    totals = Counter()
    for copy in range(lower // span, (upper - 1) // span + 1):
        offset = copy * span
        if operation == "active_customers":
            active = {customer for key, customer, _ in seed["invoice"] if lower < key + offset <= upper}
            totals.update(customers[customer] for customer in active)
            continue
        baskets, sold = Counter(), set()
        for _, invoice, track, _, quantity in seed["line"]:
            if lower < invoice + offset <= upper:
                baskets[invoice] += quantity
                sold.add(track)
        if operation == "basket_histogram":
            totals.update(baskets.values())
        else:
            totals.update(tracks[track] for track in sold if tracks[track] is not None)
    fields = QUERIES[operation][1]
    return [dict(zip(fields, item)) for item in sorted(totals.items())]


class HeldOutWorkload(Workload):
    def __init__(self, db, catalog, scope, genre_span):
        super().__init__(db, catalog, scope)
        self.genre_span = genre_span

    def _prepare(self, plan):
        self.catalog.validate_plan(plan)
        query, fields, _, _ = QUERIES[plan.operation]
        lower, upper = (int(plan.parameters[key]) for key in ("lower", "upper"))
        if lower >= upper:
            raise ValueError("Invalid invoice ID interval")
        extras = (self.genre_span,) if plan.operation == "track_coverage" else ()
        return query.format(s=self.scope), fields, (lower, upper, *extras)


def calibration(report, manifest):
    """Read development timing labels. Test workloads cannot enter this profile."""
    development = []
    families = manifest["development"]["families"]
    for frame in report["scales"].values():
        rows = frame["rows"]
        joins_groups = {
            "filtered": (0, 1), "grouped": (1, rows["country"]),
            "joined": (4, rows["country"] * 25), "high_cardinality": (0, rows["customer"]),
        }
        seen = set()
        for case in frame["cases"]:
            operation = case["operation"]
            label = (operation, case["selectivity"])
            if operation not in families or case["selectivity"] not in ("narrow", "broad") or label in seen:
                raise ValueError("Unexpected or duplicate calibration case")
            seen.add(label)
            fraction = case["invoice_id_fraction"]
            joins, groups = joins_groups[operation]
            features = SQLFeatures(rows["line" if operation == "joined" else "invoice"], joins, groups, fraction)
            times = case["modes"]["dag"]["backends"]
            if any(case["correct"].get(backend) is not True for backend in BACKENDS):
                raise ValueError("Calibration requires independently verified development cases")
            development.append((features, *(median(times[name]["samples_ms"]) for name in BACKENDS)))
        if len(seen) != len(families) * 2:
            raise ValueError("Incomplete calibration family")
    return PhysicalCostModel(development)


def features_for(rows, total, plan):
    _, _, joins, grouped_relation = QUERIES[plan.operation]
    fraction = (int(plan.parameters["upper"]) - int(plan.parameters["lower"])) / total
    inputs = rows["invoice" if plan.operation == "active_customers" else "line"]
    groups = min(rows[grouped_relation], max(1, round(rows[grouped_relation] * fraction)))
    return SQLFeatures(inputs, joins, groups, fraction)


def measure(executors, node, expected, repeats, warmups, rng):
    samples = {name: [] for name in ARMS[:-1]}
    choices = []
    for repeat in range(warmups + repeats):
        order = list(samples)
        rng.shuffle(order)
        for name in order:
            start = perf_counter()
            result = executors[name].execute([node])[node.id]
            elapsed = (perf_counter() - start) * 1000
            if result["value"] != expected:
                raise ValueError(f"Independent gold mismatch for {name}/{node.operation}")
            if repeat >= warmups:
                samples[name].append(elapsed)
                if name == "routed":
                    choices.append(result["physical_backend"])
    if len(set(choices)) != 1:
        raise ValueError("Router must be deterministic")
    # Choose one fixed backend per complete case. Samplewise minima would
    # overstate the benefit by selecting on random timing noise.
    oracle = min(BACKENDS, key=lambda backend: median(samples[backend]))
    samples["oracle"] = samples[oracle]
    return {"oracle_backend": oracle, "chosen_backend": choices[0], "correct": dict.fromkeys(ARMS, True),
            "arms": {name: _times(values) for name, values in samples.items()},
            "regret_ms": median(samples["routed"]) - median(samples["oracle"]),
            "wrong_backend_penalty_ms": median(samples[choices[0]]) - median(samples["oracle"])}


def summarize(cases, rng):
    def stats(group):
        per_case = [{name: case["arms"][name]["p50"] for name in ARMS} for case in group]
        hits = [c["chosen_backend"] == c["oracle_backend"] for c in group]

        def interval(values, statistic):
            draws = [statistic(rng.choices(values, k=len(values))) for _ in range(3000)]
            return [percentile(draws, .025), percentile(draws, .975)]

        def comparison(fixed):
            def speedup(sample):
                return sum(row[fixed] for row in sample) / sum(row["routed"] for row in sample)
            return {"speedup": speedup(per_case),
                    "paired_case_bootstrap_95": interval(per_case, speedup)}
        regret = [c["regret_ms"] for c in group]
        return {
            "cases": len(group),
            "routing_accuracy": sum(hits) / len(group),
            "routing_accuracy_bootstrap_95": interval(hits, lambda sample: sum(sample) / len(sample)),
            "latency_ms": {name: {f"p{int(q * 100)}": percentile(
                sorted(t for c in group for t in c["arms"][name]["samples_ms"]), q)
                for q in (.5, .95)} for name in ARMS},
            "median_regret_ms": median(regret),
            "median_regret_bootstrap_95_ms": interval(regret, median),
            "median_selection_overhead_ms": median(c["selection_overhead"]["p50"] for c in group),
            "speedup": {name: comparison(name) for name in BACKENDS},
        }

    groups = {"overall": cases}
    for key in ("operation", "scale"):
        for value in sorted({case[key] for case in cases}):
            groups[f"{key}:{value}"] = [case for case in cases if case[key] == value]
    for operation, scale in sorted({(c["operation"], c["scale"]) for c in cases}):
        groups[f"operation:{operation}/scale:{scale}"] = [
            c for c in cases if c["operation"] == operation and c["scale"] == scale]
    return {name: stats(group) for name, group in groups.items()}


def run(source, directory, dev_path, repeats, warmups, *, authentication, random_seed=5189):
    if repeats < 2 or warmups < 1:
        raise ValueError("Use two or more repetitions and at least one warm-up")
    manifest_raw = MANIFEST.read_bytes()
    manifest = json.loads(manifest_raw)
    dev_raw = dev_path.read_bytes()
    dev = json.loads(dev_raw)
    model = calibration(dev, manifest)
    partition = manifest["sealed_test"]
    if set(partition["families"]) != QUERIES.keys() or set(partition["families"]) & set(manifest["development"]["families"]):
        raise ValueError("Held-out families must match the manifest and be disjoint")
    seed, fingerprint = read_seed(source)
    if fingerprint != dev["source_sha256"]:
        raise ValueError("Calibration and test must use the same fixture")
    cases, environment = [], {}
    rng = random.Random(random_seed)
    for scale in partition["scales"]:
        total = spans(seed)["invoice"] * scale
        windows = [(total*a//100, total*b//100) for a, b in partition["windows_percent"]]
        catalog = Catalog("chinook_scale", {}, {
            name: OperationSpec(("sql",), ResultSpec("rows", fields=dict.fromkeys(fields, "integer")),
                                {"lower": ParameterSpec(domain="lower"), "upper": ParameterSpec(domain="upper")})
            for name, (_, fields, _, _) in QUERIES.items()
        }, {key: {str(window[i]) for window in windows} for i, key in enumerate(("lower", "upper"))})
        with ExitStack() as stack:
            implementations, metadata = {}, {}
            for name in BACKENDS:
                server = name == "sqlserver"
                db = stack.enter_context(closing(_open_server(scale) if server else _open_sqlite(
                    directory / f"chinook-{scale}x.sqlite")))
                verify(db, seed, fingerprint, scale, server=server)
                metadata[name] = _metadata(db, scale, server)
                if not server:
                    lock_reads(db)
                workload = HeldOutWorkload(db, catalog, prefix(scale) if server else "", spans(seed)["genre"])
                implementations[name] = ((fingerprint, scale), catalog.operations, workload)
            if metadata["sqlite"]["logical_keys"] != metadata["sqlserver"]["logical_keys"]:
                raise ValueError("Mismatched physical index keys")
            rows = counts(seed, scale)
            environment[str(scale)] = metadata

            def choose(plan):
                return model.choose(features_for(rows, total, plan))

            executors = {name: DAGExecutor(catalog, {"sql": registration[2]})
                         for name, registration in implementations.items()}
            executors["routed"] = DAGExecutor(catalog, {"sql": PhysicalSQLRouter(catalog, implementations, choose)})
            for operation in partition["families"]:
                for lower, upper in windows:
                    params = {"lower": str(lower), "upper": str(upper)}
                    node = DAGNode("result", "sql", operation, params)
                    plan = Plan("sql", operation, params)
                    result = measure(executors, node, gold(seed, operation, lower, upper), repeats, warmups, rng)
                    overhead = []
                    for _ in range(100):
                        start = perf_counter()
                        choose(plan)
                        overhead.append((perf_counter() - start) * 1000)
                    cases.append({"scale": scale, "operation": operation, "parameters": params,
                                  "features": vars(features_for(rows, total, plan)),
                                  "selection_overhead": _times(overhead), **result})
    return {"development_sha256": hashlib.sha256(dev_raw).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "source_sha256": fingerprint, "manifest": manifest, "calibration_points": len(model.points),
            "authentication": authentication, "machine": platform.node(), "datasets": environment,
            "warmups": warmups, "repeats": repeats, "order_seed": random_seed, "cases": cases,
            "summary_seed": 8247, "summary": summarize(cases, random.Random(8247))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("benchmarks/Chinook_Sqlite.sqlite"))
    parser.add_argument("--directory", type=Path, default=Path("logs/chinook-scale-run"))
    parser.add_argument("--development", type=Path, default=Path("logs/chinook-scale-results.json"))
    parser.add_argument("--output", type=Path, default=Path("logs/physical-routing-results.json"))
    parser.add_argument("--repeats", type=int, default=12)
    parser.add_argument("--warmups", type=int, default=3)
    args = parser.parse_args()
    report = run(args.source, args.directory, args.development, args.repeats, args.warmups,
                 authentication="Configured least-privilege SQL Server reader")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
