"""Build development gold from raw files without calling PolyQuery adapters.

Reference calculations are automated cross-checks, not independent human labels.
The output is tied to exact source hashes and must not be called held-out data.
"""
import argparse
import csv
import hashlib
import json
import sqlite3
from collections import deque
from contextlib import closing
from pathlib import Path


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def save(root, source, cases, hashes, reference):
    payload = {"metadata": {"source": source, "split": "development", "source_hashes": hashes,
                           "label": f"{source} full supplied data; authored development questions",
                           "gold_note": reference + "; computational reference, not independently reviewed held-out annotations"},
               "cases": cases}
    path = root / f"{source}-benchmark-dev.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(path, len(cases), "cases")


def case(identifier, question, expected, family, backends=("sql", "code"), **extra):
    return {"id": identifier, "question": question, "expected": expected,
            "family": family, "backends": list(backends), **extra}


def chinook(root):
    path = root / "Chinook_Sqlite.sqlite"
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        queries = [
            ("tracks", "How many tracks?", "SELECT COUNT(TrackId) FROM Track", "count"),
            ("customers", "How many customers?", "SELECT COUNT(CustomerId) FROM Customer", "count"),
            ("invoices", "How many invoices?", "SELECT COUNT(InvoiceId) FROM Invoice", "count"),
            ("revenue", "Total invoice revenue", "SELECT SUM(Total) FROM Invoice", "aggregation"),
            ("mean", "Average invoice total", "SELECT SUM(Total)/COUNT(Total) FROM Invoice", "statistics"),
        ]
        cases = [case(i, q, db.execute(sql).fetchone()[0], family) for i, q, sql, family in queries]
        values = sorted(row[0] for row in db.execute("SELECT Total FROM Invoice WHERE Total IS NOT NULL"))
        mid = len(values) // 2
        median = (values[mid] if len(values) % 2 else (values[mid-1] + values[mid]) / 2) if values else None
        cases.append(case("median", "Median invoice total", median, "statistics"))
        cases.append(case("countries", "Revenue by billing country",
                          [{"country": country, "revenue": total} for country, total in db.execute("SELECT BillingCountry,SUM(Total) FROM Invoice GROUP BY BillingCountry ORDER BY BillingCountry")], "grouping"))
        cases.append(case("genres", "Track count by genre",
                          [{"genre": name, "tracks": count} for name, count in db.execute("SELECT Name,(SELECT COUNT(*) FROM Track WHERE Track.GenreId=Genre.GenreId) FROM Genre WHERE EXISTS(SELECT 1 FROM Track WHERE Track.GenreId=Genre.GenreId) ORDER BY Name")], "join"))
    cases.extend([case("unsupported-filter", "Total invoice revenue after 2020", None, "abstention", ("abstain",)),
                  case("write", "Delete all tracks", None, "abstention", ("abstain",))])
    save(root, "chinook", cases, {"database_file": digest(path)}, "Direct raw SQLite reference queries and sorted middle invoice totals")


def taxi(root):
    import numpy as np
    import pyarrow.parquet as pq
    path = root / "yellow_tripdata_2026-01.parquet"
    table = pq.read_table(path, columns=["fare_amount", "total_amount", "trip_distance"])
    arrays = {name: table[name].to_numpy().astype(float) for name in table.column_names}
    values = {name: vector[np.isfinite(vector)] for name, vector in arrays.items()}
    fare = values["fare_amount"]
    def result(vector, fn):
        return float(fn(vector)) if len(vector) else None
    cases = [
        case("trips", "How many trips?", len(table), "count"),
        case("fare-sum", "Total fare amount", result(fare, np.sum), "aggregation"),
        case("total-sum", "Total trip amount", result(values["total_amount"], np.sum), "aggregation"),
        case("distance-mean", "Average trip distance", result(values["trip_distance"], np.mean), "statistics"),
        case("fare-mean", "Average fare amount", result(fare, np.mean), "statistics"),
        case("fare-median", "Median fare amount", result(fare, np.median), "statistics"),
        case("negative-fares", "How many negative fares?", int(np.count_nonzero(fare < 0)), "filter"),
        case("unsupported-date", "Average fare amount after January 15", None, "abstention", ("abstain",)),
        case("unsupported-field", "Median passenger age", None, "abstention", ("abstain",)),
    ]
    save(root, "taxi", cases, {"parquet_file": digest(path)}, f"NumPy {np.__version__} reductions over all raw Parquet rows; nonfinite values excluded from numeric metrics")


def ldbc(root):
    people_path, edges_path = root / "people_0_0.csv", root / "person_knows_person_0_0.csv"
    with people_path.open(encoding="utf-8-sig", newline="") as stream:
        people = {r["id"]: {"id": r["id"], "first_name": r["firstName"], "last_name": r["lastName"]} for r in csv.DictReader(stream, delimiter="|")}
    adjacency = {node: set() for node in people}
    with edges_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.reader(stream, delimiter="|")
        next(rows)
        for source, target, _ in rows:
            adjacency[source].add(target)
            adjacency[target].add(source)
    def reference_path(source, target):
        queue, seen = deque([[source]]), {source}
        while queue:
            path = queue.popleft()
            if path[-1] == target:
                return path
            for neighbor in sorted(adjacency[path[-1]], key=int):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(path + [neighbor])
        return None
    nodes = sorted(people, key=int)
    if len(nodes) < 3:
        raise ValueError("LDBC development workload needs at least three people")
    cases = [case("people", "How many people?", len(people), "count", ("graph", "sql"))]
    sources = [nodes[0], nodes[-1], max(nodes, key=lambda n: len(adjacency[n]))]
    for index, source in enumerate(sources):
        cases.append(case(f"friends-{index}", f"Friends of {source}", [people[n] for n in sorted(adjacency[source], key=int)], "neighbors", ("graph", "sql")))
    pairs = [(nodes[0], nodes[-1]), (nodes[-1], nodes[0]), (nodes[0], nodes[1]), (nodes[1], nodes[-2]), (nodes[0], nodes[0])]
    for index, (source, target) in enumerate(pairs):
        cases.append(case(f"path-{index}", f"Shortest path from {source} to {target}", reference_path(source, target), "path", ("graph", "sql"), comparison="shortest_path"))
    cases.extend([case("modifier", f"Friends of {nodes[0]} before 2020", None, "abstention", ("abstain",)),
                  case("unsupported", "How many employees?", None, "abstention", ("abstain",))])
    save(root, "ldbc", cases, {"people_file": digest(people_path), "edges_file": digest(edges_path)}, "Separate raw-CSV traversal script; shortest-path scoring accepts valid paths at the reference length")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("benchmarks"))
    parser.add_argument("--source", choices=("chinook", "taxi", "ldbc", "all"), default="all")
    args = parser.parse_args()
    for name, build in (("chinook", chinook), ("taxi", taxi), ("ldbc", ldbc)):
        if args.source in (name, "all"):
            build(args.root)
