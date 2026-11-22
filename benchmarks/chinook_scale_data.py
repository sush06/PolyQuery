"""Build deterministic relational Chinook projections for the scale benchmark."""

import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from polyquery.sqlserver import fixture_hash


SCALES = (1, 10, 100, 1000)
# Tables are in foreign-key order. Identifier domains are fixed by this module.
FIELDS = {
    "country": ("id",),
    "customer": ("id", "country_id"),
    "genre": ("id",),
    "track": ("id", "genre_id"),
    "invoice": ("id", "customer_id", "total_cents"),
    "line": ("id", "invoice_id", "track_id", "unit_cents", "quantity"),
}
FOREIGN_KEYS = {
    "customer": {"country_id": "country"}, "track": {"genre_id": "genre"},
    "invoice": {"customer_id": "customer"}, "line": {"invoice_id": "invoice", "track_id": "track"},
}
INDEXES = {"invoice": ("customer_id", "id"), "line": ("invoice_id", "track_id")}


def _cents(value):
    scaled = Decimal(str(value)) * 100
    if scaled != int(scaled):
        raise ValueError("Chinook money must have at most two fractional digits")
    return int(scaled)


def read_seed(path):
    """Read original records, keeping only the columns needed by the workload."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    before = fixture_hash(path)
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("BEGIN")
        customers = db.execute("SELECT CustomerId, Country FROM Customer ORDER BY CustomerId").fetchall()
        countries = {country: i for i, country in enumerate(sorted({row[1] for row in customers}), 1)}
        seed = {
            "country": [(i,) for i in countries.values()],
            "customer": [(key, countries[country]) for key, country in customers],
            "genre": db.execute("SELECT GenreId FROM Genre ORDER BY GenreId").fetchall(),
            "track": db.execute("SELECT TrackId, GenreId FROM Track ORDER BY TrackId").fetchall(),
            "invoice": [(key, customer, _cents(amount)) for key, customer, amount in
                        db.execute("SELECT InvoiceId, CustomerId, Total FROM Invoice ORDER BY InvoiceId")],
            "line": [(key, invoice, track, _cents(amount), qty) for key, invoice, track, amount, qty in
                     db.execute("SELECT InvoiceLineId, InvoiceId, TrackId, UnitPrice, Quantity FROM InvoiceLine ORDER BY InvoiceLineId")],
        }
    if before != fixture_hash(path):
        raise ValueError("Chinook source changed during extraction")
    return seed, before


def prefix(scale):
    if scale not in SCALES:
        raise ValueError("Unsupported Chinook scale factor")
    return f"pq_scale_{scale}."


def spans(seed):
    return {name: max(row[0] for row in rows) for name, rows in seed.items() if name != "country"}


def counts(seed, scale):
    return {name: len(rows) * (1 if name == "country" else scale) for name, rows in seed.items()}


def _populate(db, seed, fingerprint, scale, *, server):
    scope = prefix(scale) if server else ""
    integer = "bigint" if server else "INTEGER"
    replicas = "#replicas"
    span = spans(seed)
    with closing(db.cursor()) as cursor:
        if server:
            cursor.execute(f"CREATE TABLE {replicas} (copy_id {integer} PRIMARY KEY)")
            if scale > 1:
                cursor.executemany(f"INSERT INTO {replicas} VALUES (?)", [(i,) for i in range(1, scale)])
        for name, fields in FIELDS.items():
            fk = FOREIGN_KEYS.get(name, {})
            columns = [f"{key} {integer} {'PRIMARY KEY' if key == 'id' else 'NOT NULL' if key != 'genre_id' else 'NULL'}"
                       for key in fields]
            columns.extend(f"FOREIGN KEY ({key}) REFERENCES {scope}{parent}(id)" for key, parent in fk.items())
            cursor.execute(f"CREATE TABLE {scope}{name} ({','.join(columns)})")
            cursor.executemany(f"INSERT INTO {scope}{name} VALUES ({','.join('?' for _ in fields)})", seed[name])
            if scale > 1 and name != "country":
                offsets = {"id": span[name], **{key: span[parent] for key, parent in fk.items() if parent != "country"}}
                if server:
                    expressions = [f"s.{key} + r.copy_id * {offsets[key]}"
                                   if key in offsets else f"s.{key}" for key in fields]
                    cursor.execute(f"INSERT INTO {scope}{name} SELECT {','.join(expressions)} "
                                   f"FROM {scope}{name} AS s CROSS JOIN {replicas} AS r WHERE s.id <= ?", (span[name],))
                else:
                    # An immutable seed avoids SQLite's self-insert spool.
                    # Iterate copies in key order to preserve insert locality.
                    cursor.execute(f"CREATE TEMP TABLE seed_rows AS SELECT * FROM {name}")
                    expressions = [f"{key} + ?" if key in offsets else key for key in fields]
                    statement = f"INSERT INTO {name} SELECT {','.join(expressions)} FROM temp.seed_rows ORDER BY id"
                    cursor.executemany(statement, (tuple(copy * offsets[key] for key in fields if key in offsets)
                                                   for copy in range(1, scale)))
                    cursor.execute("DROP TABLE temp.seed_rows")
            if name in INDEXES:
                cursor.execute(f"CREATE INDEX ix_{name}_lookup ON {scope}{name} ({','.join(INDEXES[name])})")
        cursor.execute(f"CREATE TABLE {scope}manifest (source_hash varchar(64) NOT NULL, factor {integer} NOT NULL)")
        cursor.execute(f"INSERT INTO {scope}manifest VALUES (?, ?)", (fingerprint, scale))
        if server:
            cursor.execute(f"DROP TABLE {replicas}")
        verify(db, seed, fingerprint, scale, server=server)


def verify(db, seed, fingerprint, scale, *, server):
    scope = prefix(scale) if server else ""
    with closing(db.cursor()) as cursor:
        row = cursor.execute(f"SELECT source_hash, factor FROM {scope}manifest").fetchmany(2)
        if len(row) != 1 or tuple(row[0]) != (fingerprint, scale):
            raise ValueError("Projection fingerprint or scale mismatch")
        for name, expected in counts(seed, scale).items():
            actual = cursor.execute(f"SELECT COUNT(*) FROM {scope}{name}").fetchone()[0]
            if actual != expected:
                raise ValueError(f"Scaled row count mismatch for {name}")
            for key, parent in FOREIGN_KEYS.get(name, {}).items():
                missing = cursor.execute(f"SELECT COUNT(*) FROM {scope}{name} AS t "
                                         f"LEFT JOIN {scope}{parent} AS p ON t.{key}=p.id "
                                         f"WHERE t.{key} IS NOT NULL AND p.id IS NULL").fetchone()[0]
                if missing:
                    raise ValueError(f"Scaled foreign key mismatch for {name}.{key}")
        invoices = cursor.execute(f"SELECT SUM(total_cents) FROM {scope}invoice").fetchone()[0]
        lines = cursor.execute(f"SELECT SUM(unit_cents * quantity) FROM {scope}line").fetchone()[0]
        if invoices != scale * sum(row[2] for row in seed["invoice"]) or lines != scale * sum(
            row[3] * row[4] for row in seed["line"]
        ):
            raise ValueError("Scaled aggregate integrity check failed")


def build_sqlite(path, seed, fingerprint, scale):
    prefix(scale)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN")
            _populate(db, seed, fingerprint, scale, server=False)
    except Exception:
        path.unlink()
        raise


def build_sqlserver(db, seed, fingerprint, scale):
    scope = prefix(scale)
    if db.autocommit:
        raise ValueError("SQL Server benchmark loading requires a transactional connection")
    with db, closing(db.cursor()) as cursor:
        if cursor.execute("SELECT SCHEMA_ID(?)", scope[:-1]).fetchone()[0] is not None:
            raise ValueError(f"SQL Server benchmark schema {scope[:-1]} already exists")
        cursor.execute(f"CREATE SCHEMA {scope[:-1]} AUTHORIZATION dbo")
        _populate(db, seed, fingerprint, scale, server=True)
