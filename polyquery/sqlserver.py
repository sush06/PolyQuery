"""Optional, source-scoped SQL Server execution for Chinook aggregates."""

import hashlib
import os
from contextlib import closing
from pathlib import Path

from .backends import UnsupportedPlan
from .tabular import validate_operation


CONNECTION_ENV = "POLYQUERY_SQLSERVER_CONNECTION_STRING"
LOAD_CONNECTION_ENV = "POLYQUERY_SQLSERVER_LOAD_CONNECTION_STRING"
TABLES = ("Track", "Customer", "Invoice", "DatasetManifest")
STATEMENTS = {
    "count_tracks": "SELECT COUNT_BIG(*) FROM [polyquery].[Track]",
    "count_customers": "SELECT COUNT_BIG(*) FROM [polyquery].[Customer]",
    "count_invoices": "SELECT COUNT_BIG(*) FROM [polyquery].[Invoice]",
    "revenue": "SELECT SUM([Total]) FROM [polyquery].[Invoice]",
    "mean": "SELECT AVG([Total]) FROM [polyquery].[Invoice]",
}
_PERMISSIONS = "SELECT permission_name FROM sys.fn_my_permissions(?, ?)"


class SQLServerError(RuntimeError):
    pass


def file_hash(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fixture_hash(path):
    path = Path(path)
    if path.with_name(path.name + "-wal").exists():
        raise ValueError("SQL Server requires a standalone Chinook SQLite snapshot without a WAL file")
    return file_hash(path)


def connect_from_env(variable, *, autocommit):
    value = os.environ.get(variable)
    if not value or not value.strip():
        raise ValueError(f"Set {variable} before using SQL Server")
    try:
        import pyodbc
    except ImportError:
        raise ValueError('Install the SQL Server extra with pip install -e ".[sqlserver]"') from None
    try:
        db = pyodbc.connect(value, autocommit=autocommit, timeout=10)
        try:
            db.timeout = 30
        except Exception:
            db.close()
            raise
    except pyodbc.Error:
        raise SQLServerError("SQL Server connection failed") from None
    return db, pyodbc.Error


def _permissions(cursor, name, scope):
    cursor.execute(_PERMISSIONS, name, scope)
    rows = cursor.fetchmany(128)
    if len(rows) == 128:
        raise SQLServerError("SQL Server permission inventory exceeds its bound")
    return {row[0] for row in rows}


def _assert_reader(cursor):
    # SQL Server permissions are the security boundary. The startup check
    # rejects principals with broader effective privileges on the projection.
    assert_projection_reader(cursor, "polyquery", TABLES)


def assert_projection_reader(cursor, schema, tables):
    server = _permissions(cursor, None, "SERVER")
    database = _permissions(cursor, None, "DATABASE")
    allowed_server = {"CONNECT SQL", "VIEW ANY DATABASE"}
    allowed_database = {"CONNECT", "VIEW ANY COLUMN ENCRYPTION KEY DEFINITION",
                        "VIEW ANY COLUMN MASTER KEY DEFINITION"}
    if server - allowed_server or database - allowed_database:
        raise SQLServerError("SQL Server requires a dedicated read-only principal")
    if _permissions(cursor, schema, "SCHEMA") - {"SELECT", "VIEW DEFINITION"}:
        raise SQLServerError("SQL Server projection schema permits more than read access")
    for table in tables:
        perms = _permissions(cursor, schema + "." + table, "OBJECT")
        if "SELECT" not in perms or perms - {"SELECT", "VIEW DEFINITION"}:
            raise SQLServerError("SQL Server projection tables require SELECT-only access")


def _scalar(cursor, statement, *parameters):
    cursor.execute(statement, *parameters)
    rows = cursor.fetchmany(2)
    if len(rows) != 1 or len(rows[0]) != 1:
        raise SQLServerError("SQL Server query returned an unexpected result shape")
    return rows[0][0]


class ChinookSQLServer:
    """Replace the SQL slot for the supported subset of an existing Chinook fixture."""

    def __init__(self, fallback, fixture_path):
        self.fallback = fallback
        self.fixture_hash = fixture_hash(fixture_path)
        self.db, self.driver_error = connect_from_env(CONNECTION_ENV, autocommit=True)
        try:
            with closing(self.db.cursor()) as cursor:
                _assert_reader(cursor)
                remote_hash = _scalar(cursor,
                                      "SELECT [SQLiteSHA256] FROM [polyquery].[DatasetManifest] WHERE [Id] = ?", 1)
            if remote_hash != self.fixture_hash or fixture_hash(fixture_path) != self.fixture_hash:
                raise SQLServerError("SQL Server projection does not match the selected Chinook fixture")
        except self.driver_error:
            self.db.close()
            raise SQLServerError("SQL Server projection validation failed") from None
        except Exception:
            self.db.close()
            raise

    def execute(self, plan):
        if plan.backend != "sql":
            raise UnsupportedPlan("SQL Server requires a SQL plan")
        validate_operation(plan, self.fallback.operations)
        if plan.operation not in STATEMENTS:
            return self.fallback.execute(plan)
        statement = STATEMENTS[plan.operation]
        try:
            with closing(self.db.cursor()) as cursor:
                value = _scalar(cursor, statement)
        except self.driver_error:
            raise SQLServerError("SQL Server aggregate query failed") from None
        return {"artifact": statement, "bindings": [], "value": value, "implementation": "sqlserver"}

    def close(self):
        try:
            self.db.close()
        finally:
            self.fallback.close()
