"""Test categories and deterministic, source-level references for DAG tests."""

import csv
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from statistics import median
from types import SimpleNamespace

import pytest


def pytest_configure(config):
    for name, description in {
        "unit": "isolated contracts, planning or failure injection",
        "integration": "engine, CLI or backend workflows over fixture data",
        "security": "authorization, unsafe input and fail-closed execution",
        "evaluation": "metrics, datasets, review and split integrity",
    }.items():
        config.addinivalue_line("markers", f"{name}: {description}")


def pytest_collection_modifyitems(items):
    defaults = {
        "test_costs": "evaluation", "test_dag_review": "evaluation", "test_evaluation": "evaluation",
        "test_label_review": "evaluation", "test_router_comparison": "evaluation",
        "test_real_sources": "integration", "test_ldbc": "integration",
    }
    security = {
        "test_code_rejects_escape_attempts", "test_sql_write_authorization",
        "test_external_plans_are_validated", "test_fail_closed_grammar_and_plans",
        "test_engine_enforces_contract_before_backend_execution", "test_invalid_outputs_never_execute",
        "test_field_substitution_is_blocked_before_execution", "test_dropped_filters_and_statistics_never_execute",
    }
    for item in items:
        name = getattr(item, "originalname", None) or item.name.split("[", 1)[0]
        if name in security or item.module.__name__ == "test_constraints":
            item.add_marker("security")
        if not any(item.get_closest_marker(mark) for mark in ("unit", "integration", "security", "evaluation")):
            item.add_marker(defaults.get(item.module.__name__, "unit"))


BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"


@pytest.fixture
def source_data(request, tmp_path):
    source, _, variant = request.param.partition("-")
    if source == "chinook":
        path = BENCHMARKS / "Chinook_Sqlite.sqlite" if variant == "dataset" else tmp_path / "chinook.sqlite"
        if variant == "dataset" and not path.is_file():
            pytest.skip("Optional Chinook benchmark database is absent")
        if not path.exists():
            with closing(sqlite3.connect(path)) as db:
                db.executescript("""
                    CREATE TABLE Track(TrackId INTEGER, GenreId INTEGER);
                    CREATE TABLE Genre(GenreId INTEGER, Name TEXT);
                    CREATE TABLE Customer(CustomerId INTEGER);
                    CREATE TABLE Invoice(Total REAL, BillingCountry TEXT);
                    INSERT INTO Track VALUES (1, 10), (2, 20), (3, 20);
                    INSERT INTO Genre VALUES (10, 'Jazz'), (20, 'Rock');
                    INSERT INTO Customer VALUES (7), (19);
                    INSERT INTO Invoice VALUES (4.5, 'CA'), (7.25, 'US'), (13, 'CA'), (NULL, 'CA');
                """)
                db.commit()
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            totals = [total for (total,) in db.execute("SELECT Total FROM Invoice") if total is not None]
            tracks = len(db.execute("SELECT TrackId FROM Track").fetchall())
            customers = len(db.execute("SELECT CustomerId FROM Customer").fetchall())
        return SimpleNamespace(
            kwargs={"source": source, "data_path": path},
            values={"count_tracks": tracks, "count_customers": customers,
                    "median": median(totals), "revenue": math.fsum(totals)},
        )
    if source == "taxi":
        pa = pytest.importorskip("pyarrow")
        parquet = pytest.importorskip("pyarrow.parquet")
        fares = [-3.0, 5.0, None, 22.0, float("inf"), 9.0]
        path = tmp_path / "trips.parquet"
        parquet.write_table(pa.table({"fare_amount": fares, "total_amount": [2.0] * len(fares),
                                      "trip_distance": [4.0] * len(fares)}), path, row_group_size=2)
        finite = [fare for fare in fares if fare is not None and math.isfinite(fare)]
        return SimpleNamespace(kwargs={"source": source, "data_path": path},
                               values={"count_trips": len(fares), "median_fare": median(finite),
                                       "negative_fares": sum(fare < 0 for fare in finite)})
    if variant == "dataset":
        people = BENCHMARKS / "people_0_0.csv"
        edges = BENCHMARKS / "person_knows_person_0_0.csv"
        if not people.is_file() or not edges.is_file():
            pytest.skip("Optional LDBC benchmark extracts are absent")
    else:
        people, edges = tmp_path / "person.csv", tmp_path / "knows.csv"
        people.write_text("id|firstName|lastName\n21|Mira|Liu\n34|Ari|Sen\n55|Nia|Patel\n"
                          "89|Seth|Ng\n144|Lee|Ives\n", encoding="utf-8")
        edges.write_text("Person.id|Person.id|creationDate\n21|34|0\n34|21|0\n"
                         "21|55|0\n34|89|0\n55|89|0\n", encoding="utf-8")
    with people.open(encoding="utf-8-sig", newline="") as stream:
        records = {row["id"]: {"id": row["id"], "first_name": row["firstName"], "last_name": row["lastName"]}
                   for row in csv.DictReader(stream, delimiter="|")}
    neighbor = {person: set() for person in records}
    with edges.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter="|")
        next(reader)
        for first, second, *_ in reader:
            neighbor[first].add(second)
            neighbor[second].add(first)
    center = next(person for person, peers in neighbor.items() if len(peers - {person}) >= 2)
    first, second, *_ = sorted(neighbor[center] - {center}, key=int)
    return SimpleNamespace(kwargs={"source": source, "people_path": people, "edges_path": edges},
                           values={"count_people": len(records)}, records=records, neighbor=neighbor,
                           center=center, endpoints=(first, second))


@pytest.fixture
def synthetic_reference():
    from polyquery.data import EMPLOYEES, REPORTS

    salaries = {row["name"]: row["salary"] for row in EMPLOYEES}

    def reports(manager, direct=False):
        members = {child for owner, child in REPORTS if owner == manager}
        if not direct:
            while True:
                expanded = members | {child for owner, child in REPORTS if owner in members}
                if expanded == members:
                    break
                members = expanded
        return sorted(members)

    return SimpleNamespace(employees=EMPLOYEES, salaries=salaries, reports=reports)
