"""Load a minimal Chinook SQL Server projection into a new polyquery schema."""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from polyquery.sqlserver import LOAD_CONNECTION_ENV, SQLServerError, connect_from_env, fixture_hash


PROJECTION = {
    "Track": ("[TrackId] bigint NOT NULL PRIMARY KEY", "SELECT TrackId FROM Track"),
    "Customer": ("[CustomerId] bigint NOT NULL PRIMARY KEY", "SELECT CustomerId FROM Customer"),
    "Invoice": ("[Total] float NULL", "SELECT Total FROM Invoice"),
}


def load(path):
    path = Path(path).resolve()
    fingerprint = fixture_hash(path)
    counts = {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        source.execute("BEGIN")
        db, driver_error = connect_from_env(LOAD_CONNECTION_ENV, autocommit=False)
        with closing(db):
            try:
                with closing(db.cursor()) as cursor:
                    cursor.execute("SELECT SCHEMA_ID(?)", "polyquery")
                    if cursor.fetchone()[0] is not None:
                        raise ValueError("The polyquery schema already exists. Use a fresh SQL Server database.")
                    cursor.execute("CREATE SCHEMA [polyquery] AUTHORIZATION [dbo]")
                    for table, (schema, statement) in PROJECTION.items():
                        cursor.execute(f"CREATE TABLE [polyquery].[{table}] ({schema})")
                        rows = source.execute(statement)
                        counts[table] = 0
                        while batch := rows.fetchmany(1000):
                            cursor.executemany(f"INSERT INTO [polyquery].[{table}] VALUES (?)", batch)
                            counts[table] += len(batch)
                    cursor.execute("CREATE TABLE [polyquery].[DatasetManifest] "
                                   "([Id] int NOT NULL PRIMARY KEY, [SQLiteSHA256] char(64) NOT NULL)")
                    if fixture_hash(path) != fingerprint:
                        raise ValueError("Chinook SQLite changed during loading")
                    cursor.execute("INSERT INTO [polyquery].[DatasetManifest] VALUES (?, ?)", 1, fingerprint)
                db.commit()
            except driver_error:
                try:
                    db.rollback()
                except driver_error:
                    pass
                raise SQLServerError("SQL Server Chinook load failed") from None
            except Exception:
                db.rollback()
                raise
    return fingerprint, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    try:
        fingerprint, counts = load(args.data)
    except (OSError, ValueError, sqlite3.Error, SQLServerError) as error:
        parser.error(str(error))
    print(f"Loaded {counts}; SQLite SHA-256 {fingerprint}")


if __name__ == "__main__":
    main()
