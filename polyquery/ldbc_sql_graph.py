"""Opt-in fixed SQL implementation of existing LDBC graph contracts."""
import hashlib
import json
import sqlite3
import time
from collections import Counter
from contextlib import closing

from . import sqlserver
from .backends import UnsupportedPlan

MAX_HOPS = 12
TABLES = ("person", "knows", "manifest")


class TraversalLimit(RuntimeError):
    """The SQL baseline could not complete the exact traversal within its limits."""


def projection(graph):
    ids = sorted(graph.people, key=lambda key: (int(key), key))
    ranks = {key: i for i, key in enumerate(ids, 1)}
    people = [(ranks[key], key, graph.people[key]["first_name"], graph.people[key]["last_name"]) for key in ids]
    edges = sorted((ranks[source], ranks[target]) for source in ids for target in graph.adjacency[source])
    fingerprint = hashlib.sha256(json.dumps((people, edges), ensure_ascii=False).encode()).hexdigest()
    return people, edges, fingerprint


def scope_for(size):
    if type(size) is not int or not 1 <= size <= 999999999:
        raise ValueError("Invalid projection size")
    return f"pq_ldbc_{size}"


def ingest(db, graph, *, server, grant_lab_reader=False):
    people, edges, fingerprint = projection(graph)
    schema = scope_for(len(people))
    prefix = schema + "." if server else ""
    with closing(db.cursor()) as cursor:
        if server:
            if cursor.execute("SELECT SCHEMA_ID(?)", schema).fetchone()[0] is not None:
                verify_projection(db, graph, server=True)
                return "verified existing"
            cursor.execute(f"CREATE SCHEMA {schema} AUTHORIZATION dbo")
        else:
            cursor.execute("PRAGMA foreign_keys=ON")
        data_type = "NVARCHAR(450)" if server else "TEXT"
        text_type = "NVARCHAR(MAX)" if server else "TEXT"
        cursor.execute(f"CREATE TABLE {prefix}person (rank_id INT NOT NULL PRIMARY KEY, "
                       f"person_id {data_type} NOT NULL UNIQUE, first_name {text_type} NOT NULL, last_name {text_type} NOT NULL)")
        cursor.execute(f"CREATE TABLE {prefix}knows (source_id INT NOT NULL REFERENCES {prefix}person(rank_id), "
                       f"target_id INT NOT NULL REFERENCES {prefix}person(rank_id), PRIMARY KEY(source_id,target_id))")
        cursor.execute(f"CREATE TABLE {prefix}manifest (fingerprint CHAR(64) NOT NULL)")
        cursor.executemany(f"INSERT INTO {prefix}person VALUES (?,?,?,?)", people)
        if edges:
            cursor.executemany(f"INSERT INTO {prefix}knows VALUES (?,?)", edges)
        cursor.execute(f"INSERT INTO {prefix}manifest VALUES (?)", (fingerprint,))
        if server and grant_lab_reader:
            for table in TABLES:
                cursor.execute(f"GRANT SELECT ON OBJECT::{prefix}{table} TO [PolyQueryReadProbe]")
    verify_projection(db, graph, server=server)
    return "loaded"


def verify_projection(db, graph, *, server):
    people, edges, fingerprint = projection(graph)
    prefix = scope_for(len(people)) + "." if server else ""
    with closing(db.cursor()) as cursor:
        for statement, expected in (
            (f"SELECT fingerprint FROM {prefix}manifest", [(fingerprint,)]),
            (f"SELECT rank_id,person_id,first_name,last_name FROM {prefix}person ORDER BY rank_id", people),
            (f"SELECT source_id,target_id FROM {prefix}knows ORDER BY source_id,target_id", edges),
        ):
            rows = cursor.execute(statement).fetchmany(len(expected) + 1)
            if [tuple(row) for row in rows] != expected:
                raise ValueError("LDBC SQL projection differs from the source")
    return fingerprint


class LDBCSQLGraph:
    def __init__(self, db, graph, catalog, *, server=False, timeout=3, max_hops=MAX_HOPS, driver_error=sqlite3.Error):
        if type(max_hops) is not int or not 1 <= max_hops <= MAX_HOPS or type(timeout) is not int or not 1 <= timeout <= 30:
            raise ValueError("Invalid LDBC traversal bounds")
        self.db, self.catalog, self.server = db, catalog, server
        self.timeout, self.max_hops, self.driver_error = timeout, max_hops, driver_error
        self.size = len(graph.people)
        self.prefix = scope_for(self.size) + "." if server else ""
        # Preserve distinct identifiers that have the same numeric value.
        if len({int(key) for key in graph.people}) != self.size:
            raise UnsupportedPlan("Numeric ID ties lack a deterministic BFS tie order")
        if server:
            db.timeout = timeout
            with closing(db.cursor()) as cursor:
                sqlserver.assert_projection_reader(cursor, scope_for(self.size), TABLES)
        verify_projection(db, graph, server=server)
        if not server:
            from .tabular import lock_reads
            lock_reads(db)

    def _statement(self):
        if self.server:
            token = "RIGHT('0000000000' + CAST({} AS VARCHAR(10)),10)"
            anchor = "CAST('|' + " + token.format("rank_id") + " + '|' AS VARCHAR(MAX))"
            extended = "CAST(w.path + " + token.format("k.target_id") + " + '|' AS VARCHAR(MAX))"
            unseen = "CHARINDEX('|' + " + token.format("k.target_id") + " + '|', w.path)=0"
            first, tail = "TOP (1) ", "OPTION (MAXRECURSION 12)"
            ordering = "path COLLATE Latin1_General_100_BIN2"
        else:
            anchor = "printf('|%010d|', rank_id)"
            extended = "w.path || printf('%010d|', k.target_id)"
            unseen = "instr(w.path, printf('|%010d|', k.target_id))=0"
            first, tail, ordering = "", "LIMIT 1", "path COLLATE BINARY"
        return (f"WITH walk AS (SELECT rank_id, 0 AS hops, {anchor} AS path "
                f"FROM {self.prefix}person WHERE person_id=? UNION ALL "
                f"SELECT k.target_id,w.hops+1,{extended} FROM walk w "
                f"JOIN {self.prefix}knows k ON k.source_id=w.rank_id "
                f"WHERE w.hops < ? AND w.rank_id <> ? AND {unseen}) "
                f"SELECT {first}rank_id,path FROM walk "
                f"WHERE rank_id=? OR hops=? ORDER BY CASE WHEN rank_id=? THEN 0 ELSE 1 END,hops,{ordering} {tail}")

    def _paths(self, cursor, source, target, deadline):
        target_rank = cursor.execute(f"SELECT rank_id FROM {self.prefix}person WHERE person_id=?", (target,)).fetchone()[0]
        for depth in range(1, min(self.max_hops, max(1, self.size - 1)) + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TraversalLimit("Traversal time budget exhausted")
            if self.server:
                self.db.timeout = max(1, int(remaining + .999))
            row = cursor.execute(self._statement(), (source, depth, target_rank, target_rank, depth, target_rank)).fetchone()
            if row is None:
                return None
            if row[0] == target_rank:
                ranks = [int(token) for token in row[1].strip("|").split("|")]
                placeholders = ",".join("?" for _ in ranks)
                people = dict(cursor.execute(f"SELECT rank_id,person_id FROM {self.prefix}person "
                                             f"WHERE rank_id IN ({placeholders})", tuple(ranks)).fetchall())
                return [people[rank] for rank in ranks]
            if depth >= self.size - 1:
                return None
        raise TraversalLimit("Traversal depth bound reached before reachability was decided")

    def execute(self, plan):
        self.catalog.validate_plan(plan)
        if plan.backend != "sql" or plan.operation not in {"friends", "count_people", "shortest_path"}:
            raise UnsupportedPlan("Unsupported SQL graph operation")
        deadline = time.monotonic() + self.timeout
        if not self.server:
            self.db.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        else:
            self.db.timeout = self.timeout
        try:
            with closing(self.db.cursor()) as cursor:
                if plan.operation == "count_people":
                    value = cursor.execute(f"SELECT COUNT(*) FROM {self.prefix}person").fetchone()[0]
                elif plan.operation == "friends":
                    rows = cursor.execute(
                        f"SELECT p.person_id,p.first_name,p.last_name FROM {self.prefix}person a "
                        f"JOIN {self.prefix}knows k ON k.source_id=a.rank_id "
                        f"JOIN {self.prefix}person p ON p.rank_id=k.target_id WHERE a.person_id=? ORDER BY p.rank_id",
                        (plan.parameters["source"],)).fetchmany(self.size + 1)
                    if len(rows) > self.size:
                        raise UnsupportedPlan("SQL friends exceed the person domain")
                    value = [dict(zip(("id", "first_name", "last_name"), row)) for row in rows]
                else:
                    value = self._paths(cursor, plan.parameters["source"], plan.parameters["target"], deadline)
        except self.driver_error as error:
            if (self.server and error.args and error.args[0] in {"HYT00", "HYT01"}
                    or not self.server and getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT):
                raise TraversalLimit("SQL graph statement timed out") from None
            raise
        finally:
            if not self.server:
                self.db.set_progress_handler(None, 0)
        result = {"value": value}
        self.catalog.validate_result(plan, result)
        return result


def open_sqlserver(graph, catalog, *, timeout=3):
    db, error = sqlserver.connect_from_env(sqlserver.CONNECTION_ENV, autocommit=True)
    try:
        return db, LDBCSQLGraph(db, graph, catalog, server=True, timeout=timeout, driver_error=error)
    except BaseException:
        db.close()
        raise
