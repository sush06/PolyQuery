"""Independent reference calculations for the authored Evaluation v2 cases."""

import csv
import math
import sqlite3
from collections import deque
from contextlib import closing
from statistics import median


CHINOOK_SQL = {
    "count_tracks": "SELECT COUNT(*) FROM Track",
    "count_customers": "SELECT COUNT(*) FROM Customer",
    "count_invoices": "SELECT COUNT(*) FROM Invoice",
    "revenue": "SELECT SUM(Total) FROM Invoice",
    "mean": "SELECT AVG(Total) FROM Invoice",
    "median": "SELECT Total FROM Invoice WHERE Total IS NOT NULL",
    "revenue_by_country": "SELECT BillingCountry AS country, SUM(Total) AS revenue FROM Invoice GROUP BY BillingCountry ORDER BY BillingCountry",
    "tracks_by_genre": "SELECT Genre.Name AS genre, COUNT(*) AS tracks FROM Genre JOIN Track USING (GenreId) GROUP BY Genre.GenreId ORDER BY Genre.Name",
}
TAXI_COLUMNS = {
    "count_trips": None, "sum_fare": "fare_amount", "sum_total": "total_amount",
    "mean_distance": "trip_distance", "mean_fare": "fare_amount",
    "median_fare": "fare_amount", "negative_fares": "fare_amount",
}
ARGUMENTS = {
    "chinook": dict.fromkeys(CHINOOK_SQL, 0),
    "taxi": dict.fromkeys(TAXI_COLUMNS, 0),
    "ldbc": {"count_people": 0, "friends": 1, "shortest_path": 2},
}


class Reference:
    def __init__(self, source, config, operations):
        self.source = source
        if source == "chinook":
            self.values = _chinook(config["data_path"], operations)
        elif source == "taxi":
            self.values = _taxi(config["data_path"], operations)
        else:
            self.people, self.edges = _ldbc(config["people_path"], config["edges_path"])

    def answer(self, spec):
        operation, args = spec["operation"], spec["args"]
        if self.source != "ldbc":
            return self.values[operation]
        if any(person not in self.people for person in args):
            raise ValueError("Reference uses an unknown LDBC person ID")
        if operation == "count_people":
            return len(self.people)
        if operation == "friends":
            return [self.people[person] for person in sorted(self.edges[args[0]], key=lambda p: (int(p), p))]
        source, target = args
        queue = deque([(source, [source])])
        seen = {source}
        while queue:
            person, path = queue.popleft()
            if person == target:
                return path
            for neighbor in sorted(self.edges[person], key=lambda p: (int(p), p)):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, [*path, neighbor]))
        return None

    def matches(self, spec, actual, expected):
        from .evaluation import equivalent

        if spec["operation"] != "shortest_path" or expected is None:
            return equivalent(actual, expected)
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(type(node) is str and node in self.edges for node in actual)
                and [actual[0], actual[-1]] == spec["args"]
                and all(second in self.edges[first] for first, second in zip(actual, actual[1:])))


def _chinook(path, operations):
    results = {}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        for operation in sorted(operations):
            rows = db.execute(CHINOOK_SQL[operation]).fetchall()
            if operation == "median":
                results[operation] = median(row[0] for row in rows) if rows else None
            elif operation in ("revenue_by_country", "tracks_by_genre"):
                results[operation] = [dict(row) for row in rows]
            else:
                results[operation] = rows[0][0]
    return results


def _taxi(path, operations):
    import pyarrow.parquet as parquet

    results = {}
    with parquet.ParquetFile(path) as file:
        if "count_trips" in operations:
            results["count_trips"] = file.metadata.num_rows
        for column in sorted({TAXI_COLUMNS[op] for op in operations if TAXI_COLUMNS[op]}):
            values = [float(value) for batch in file.iter_batches(columns=[column])
                      for value in batch.column(0).to_pylist() if value is not None and math.isfinite(value)]
            for operation in operations:
                if TAXI_COLUMNS[operation] != column:
                    continue
                if operation == "negative_fares":
                    results[operation] = sum(value < 0 for value in values)
                elif operation == "median_fare":
                    results[operation] = median(values) if values else None
                elif operation.startswith("mean_"):
                    results[operation] = math.fsum(values) / len(values) if values else None
                else:
                    results[operation] = math.fsum(values) if values else None
    return results


def _ldbc(people_path, edges_path):
    with people_path.open(encoding="utf-8-sig", newline="") as stream:
        records = {row["id"]: {"id": row["id"], "first_name": row["firstName"], "last_name": row["lastName"]}
                   for row in csv.DictReader(stream, delimiter="|")}
    edges = {person: set() for person in records}
    with edges_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.reader(stream, delimiter="|")
        next(rows)
        for source, target, _ in rows:
            edges[source].add(target)
            edges[target].add(source)
    return records, edges
