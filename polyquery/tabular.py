"""Read-only Chinook and full-file Taxi projections with SQL/Python strategies."""
import ast
import math
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from .backends import UnsupportedPlan
from .planner import Plan

CHINOOK_QUESTIONS = {
    "how many tracks": "count_tracks",
    "how many customers": "count_customers",
    "how many invoices": "count_invoices",
    "total invoice revenue": "revenue",
    "average invoice total": "mean",
    "median invoice total": "median",
    "revenue by billing country": "revenue_by_country",
    "track count by genre": "tracks_by_genre",
}
TAXI_QUESTIONS = {
    "how many trips": "count_trips",
    "total fare amount": "sum_fare",
    "total trip amount": "sum_total",
    "average trip distance": "mean_distance",
    "average fare amount": "mean_fare",
    "median fare amount": "median_fare",
    "how many negative fares": "negative_fares",
}
CHINOOK_SQL = {
    "count_tracks": "SELECT COUNT(*) FROM Track",
    "count_customers": "SELECT COUNT(*) FROM Customer",
    "count_invoices": "SELECT COUNT(*) FROM Invoice",
    "revenue": "SELECT SUM(Total) FROM Invoice",
    "mean": "SELECT AVG(Total) FROM Invoice",
    "median": "SELECT AVG(Total) FROM (SELECT Total FROM Invoice WHERE Total IS NOT NULL ORDER BY Total LIMIT (2 - (SELECT COUNT(Total) FROM Invoice) % 2) OFFSET (SELECT (COUNT(Total)-1)/2 FROM Invoice))",
    "revenue_by_country": "SELECT BillingCountry AS country, SUM(Total) AS revenue FROM Invoice GROUP BY BillingCountry ORDER BY BillingCountry",
    "tracks_by_genre": "SELECT g.Name AS genre, COUNT(*) AS tracks FROM Track t JOIN Genre g ON g.GenreId=t.GenreId GROUP BY g.GenreId,g.Name ORDER BY g.Name",
}
TAXI_SQL = {
    "count_trips": "SELECT COUNT(*) FROM trips",
    "sum_fare": "SELECT SUM(fare_amount) FROM trips",
    "sum_total": "SELECT SUM(total_amount) FROM trips",
    "mean_distance": "SELECT AVG(trip_distance) FROM trips",
    "mean_fare": "SELECT AVG(fare_amount) FROM trips",
    "median_fare": "SELECT AVG(fare_amount) FROM (SELECT fare_amount FROM trips WHERE fare_amount IS NOT NULL ORDER BY fare_amount LIMIT (2 - (SELECT COUNT(fare_amount) FROM trips) % 2) OFFSET (SELECT (COUNT(fare_amount)-1)/2 FROM trips))",
    "negative_fares": "SELECT COUNT(*) FROM trips WHERE fare_amount < 0",
}


def lock_reads(db):
    db.execute("PRAGMA query_only = ON")
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    db.set_authorizer(lambda action, *args: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)


def validate_operation(plan, operations):
    if plan.operation not in operations or plan.parameters:
        raise UnsupportedPlan("Unsupported operation or parameters for this source")


class TabularPlanner:
    def __init__(self, source, policy="router"):
        if policy not in ("router", "fixed-sql", "fixed-code"):
            raise ValueError("Unknown tabular policy")
        self.questions = CHINOOK_QUESTIONS if source == "chinook" else TAXI_QUESTIONS
        self.policy = policy

    def plan(self, question):
        q = question.strip().lower().rstrip("?.") if isinstance(question, str) and len(question) <= 2000 else ""
        operation = self.questions.get(q)
        if operation is None:
            return Plan("abstain", "unsupported", reason="Unsupported grammar; use a documented question for this source.")
        backend = "code" if "median" in operation else "sql"
        if self.policy != "router":
            backend = self.policy.removeprefix("fixed-")
        return Plan(backend, operation, reason=f"{self.policy}: validated source operation; both tabular strategies support this operation.")


class TabularSQL:
    def __init__(self, db, statements):
        self.db, self.statements = db, statements
        self.operations = set(statements)

    def execute(self, plan):
        validate_operation(plan, self.statements)
        sql = self.statements[plan.operation]
        rows = self.db.execute(sql).fetchall()
        value = [dict(row) for row in rows] if plan.operation in ("revenue_by_country", "tracks_by_genre") else rows[0][0]
        return {"artifact": sql, "bindings": [], "value": value}

    def close(self):
        self.db.close()


class ChinookCode:
    operations = set(CHINOOK_SQL)
    def __init__(self, db):
        # Materialize only the tables used by these documented operations.
        self.tables = {name: [dict(row) for row in db.execute(f'SELECT * FROM "{name}"')]
                       for name in ("Track", "Customer", "Invoice", "Genre")}

    def execute(self, plan):
        validate_operation(plan, CHINOOK_SQL)
        op = plan.operation
        invoices = self.tables["Invoice"]
        totals = [row["Total"] for row in invoices if row["Total"] is not None]
        if op.startswith("count_"):
            table = {"count_tracks": "Track", "count_customers": "Customer", "count_invoices": "Invoice"}[op]
            value, expression = len(self.tables[table]), f'len({table})'
        elif op in ("revenue", "mean", "median"):
            function = {"revenue": math.fsum, "mean": statistics.mean, "median": statistics.median}[op]
            value = function(totals) if totals else None
            expression = {"revenue": "fsum(invoice_total)", "mean": "mean(invoice_total)", "median": "median(invoice_total)"}[op]
        elif op == "revenue_by_country":
            groups = defaultdict(list)
            for row in invoices:
                groups[row["BillingCountry"]].append(row["Total"])
            value = [{"country": country, "revenue": math.fsum(v for v in groups[country] if v is not None) if any(v is not None for v in groups[country]) else None}
                     for country in sorted(groups, key=lambda c: (c is not None, c or ""))]
            expression = "group_sum(invoices, BillingCountry, Total)"
        else:
            genres = {row["GenreId"]: row["Name"] for row in self.tables["Genre"]}
            counts = Counter(row["GenreId"] for row in self.tables["Track"] if row["GenreId"] in genres)
            value = [{"genre": genres[key], "tracks": count} for key, count in sorted(counts.items(), key=lambda pair: genres[pair[0]])]
            expression = "join_count(tracks, genres, GenreId)"
        return {"artifact": expression, "value": value, "implementation": "Fixed Python templates; no eval or exec"}


class TaxiCode:
    operations = set(TAXI_SQL)
    expressions = {
        "count_trips": "count_rows(fare_amount)", "sum_fare": "sum(fare_amount)",
        "sum_total": "sum(total_amount)", "mean_distance": "mean(trip_distance)",
        "mean_fare": "mean(fare_amount)", "median_fare": "median(fare_amount)",
        "negative_fares": "negative_count(fare_amount)",
    }

    def __init__(self, table):
        self.table = table

    def execute(self, plan):
        validate_operation(plan, TAXI_SQL)
        import pyarrow.compute as pc
        expression = self.expressions[plan.operation]
        node = ast.parse(expression, mode="eval").body
        # Interpret fixed expressions against a small function/column registry.
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or len(node.args) != 1 or not isinstance(node.args[0], ast.Name) or node.keywords:
            raise UnsupportedPlan("Invalid columnar expression")
        functions = {
            "count_rows": lambda column: len(column),
            "sum": lambda column: pc.sum(column).as_py(),
            "mean": lambda column: pc.mean(column).as_py(),
            "median": lambda column: pc.quantile(column, q=.5, interpolation="linear")[0].as_py(),
            "negative_count": lambda column: pc.sum(pc.cast(pc.less(column, 0), "int64")).as_py() or 0,
        }
        if node.func.id not in functions or node.args[0].id not in self.table.column_names:
            raise UnsupportedPlan("Unknown column or function")
        value = functions[node.func.id](self.table[node.args[0].id])
        return {"artifact": expression, "value": value, "implementation": "AST allowlist dispatch to PyArrow kernels; no eval or exec"}


def chinook_backends(path):
    path = Path(path).resolve()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        required = {"Track": {"GenreId"}, "Genre": {"GenreId", "Name"}, "Invoice": {"Total", "BillingCountry"}, "Customer": {"CustomerId"}}
        for table, columns in required.items():
            present = {row["name"] for row in db.execute(f'PRAGMA table_info("{table}")')}
            if not columns <= present:
                raise ValueError(f"Chinook missing required table/columns: {table}")
        code = ChinookCode(db)
        metadata = {"name": "chinook", "database_file": str(path),
                    "rows": {name: len(rows) for name, rows in code.tables.items()},
                    "projection": "Track, Customer, Invoice, Genre; full tables"}
        lock_reads(db)
        return {"sql": TabularSQL(db, CHINOOK_SQL), "code": code}, metadata
    except Exception:
        db.close()
        raise


def taxi_backends(path):
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        import pyarrow.compute as pc
    except ImportError as error:
        raise ValueError('Taxi requires the optional dependency: python -m pip install -e ".[taxi]"') from error
    path = Path(path).resolve()
    columns = ["fare_amount", "total_amount", "trip_distance"]
    with pq.ParquetFile(path) as parquet:
        if not set(columns) <= set(parquet.schema_arrow.names):
            raise ValueError("Taxi Parquet is missing required numeric columns")
        table = parquet.read(columns=columns).cast(pa.schema([(c, pa.float64()) for c in columns]))
    # Align SQL and Arrow semantics: nonfinite numeric entries become null.
    table = pa.table({c: pc.if_else(pc.is_finite(table[c]), table[c], None) for c in columns})
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        db.execute("CREATE TABLE trips(fare_amount REAL,total_amount REAL,trip_distance REAL)")
        for batch in table.to_batches(max_chunksize=65536):
            vectors = [batch.column(i).to_pylist() for i in range(3)]
            db.executemany("INSERT INTO trips VALUES (?,?,?)", zip(*vectors))
        # Give the fixed SQL policy a covering index for fare statistics/median.
        db.execute("CREATE INDEX trips_fare_idx ON trips(fare_amount)")
        db.commit()
        metadata = {"name": "taxi", "parquet_file": str(path), "rows": len(table),
                    "columns": columns, "projection": "All rows; nonfinite numbers treated as null; negative and zero values retained",
                    "sql_indexes": ["trips_fare_idx(fare_amount)"],
                    "nulls": {c: table[c].null_count for c in columns}, "pyarrow_version": pa.__version__}
        lock_reads(db)
        return {"sql": TabularSQL(db, TAXI_SQL), "code": TaxiCode(table)}, metadata
    except Exception:
        db.close()
        raise
