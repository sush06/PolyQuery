"""SQL frontier expansion over the same undirected person/knows projection."""
import sqlite3

from .backends import UnsupportedPlan
from .planner import Plan


_READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE,
                 sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}
_FRONTIER_BATCH_SIZE = 512
_FRONTIER_SQL = "SELECT source,target FROM knows WHERE source IN ({}) ORDER BY LENGTH(source),source"


def _authorize_read(action, name, argument, database, trigger):
    if action in _READ_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and argument.casefold() != "load_extension":
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and name.casefold() == "query_only" and argument is None:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class LDBCSQL:
    operations = {"count_people", "friends", "shortest_path"}
    def __init__(self, graph):
        self.graph = graph
        self.db = sqlite3.connect(":memory:")
        try:
            self.db.execute("CREATE TABLE people(id TEXT PRIMARY KEY)")
            self.db.execute("CREATE TABLE knows(source TEXT,target TEXT,PRIMARY KEY(source,target))")
            self.db.executemany("INSERT INTO people VALUES (?)", ((node,) for node in graph.people))
            self.db.executemany("INSERT INTO knows VALUES (?,?)", ((node, neighbor) for node, neighbors in graph.adjacency.items() for neighbor in neighbors))
            self.db.commit()
            self.db.execute("PRAGMA query_only = ON")
            self.db.set_authorizer(_authorize_read)
        except Exception:
            self.db.close()
            raise

    def execute(self, plan):
        required = {"count_people": set(), "friends": {"source"}, "shortest_path": {"source", "target"}}
        if plan.operation not in required or set(plan.parameters) != required[plan.operation]:
            raise UnsupportedPlan("Invalid LDBC SQL plan")
        if any(type(v) is not str or v not in self.graph.people for v in plan.parameters.values()):
            raise UnsupportedPlan("Unknown LDBC person ID")
        if plan.operation == "count_people":
            sql = "SELECT COUNT(*) FROM people"
            value = self.db.execute(sql).fetchone()[0]
        elif plan.operation == "friends":
            sql = "SELECT target FROM knows WHERE source=? ORDER BY LENGTH(target),target"
            value = [self.graph.people[row[0]] for row in self.db.execute(sql, (plan.parameters["source"],))]
        else:
            sql, value = self._shortest_path(plan.parameters["source"], plan.parameters["target"])
        return {"artifact": sql, "value": value,
                "implementation": "Read-only SQLite frontier lookups; Python traversal state; shortest-path ties may differ from BFS"}

    def _shortest_path(self, source, target):
        parents = {source: None}
        frontier = [source]
        sql = _FRONTIER_SQL.format("?")
        while frontier and target not in parents:
            # Match the old SQL tie-breaker even when the frontier spans batches.
            frontier.sort(key=lambda identifier: (len(identifier), identifier))
            next_frontier = []
            for offset in range(0, len(frontier), _FRONTIER_BATCH_SIZE):
                batch = frontier[offset:offset + _FRONTIER_BATCH_SIZE]
                sql = _FRONTIER_SQL.format(",".join("?" for _ in batch))
                for parent, neighbor in self.db.execute(sql, batch):
                    if neighbor not in parents:
                        parents[neighbor] = parent
                        next_frontier.append(neighbor)
            frontier = next_frontier
        if target not in parents:
            return sql, None
        path, node = [], target
        while node is not None:
            path.append(node)
            node = parents[node]
        return sql, path[::-1]

    def close(self):
        self.db.close()


class LDBCPolicyPlanner:
    def __init__(self, planner, policy):
        if policy not in ("router", "fixed-sql", "fixed-graph"):
            raise ValueError("LDBC supports router, fixed-sql, or fixed-graph")
        self.planner, self.policy = planner, policy

    def plan(self, question):
        plan = self.planner.plan(question)
        if plan.backend == "abstain" or self.policy != "fixed-sql":
            return plan
        return Plan("sql", plan.operation, plan.parameters, "Fixed SQLite execution over the same person/knows projection.")
