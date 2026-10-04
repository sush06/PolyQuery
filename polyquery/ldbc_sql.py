"""SQL frontier expansion over the same undirected person/knows projection."""
import sqlite3

from .backends import UnsupportedPlan
from .planner import Plan


class LDBCSQL:
    def __init__(self, graph):
        self.graph = graph
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE people(id TEXT PRIMARY KEY)")
        self.db.execute("CREATE TABLE knows(source TEXT,target TEXT,PRIMARY KEY(source,target))")
        self.db.executemany("INSERT INTO people VALUES (?)", ((node,) for node in graph.people))
        self.db.executemany("INSERT INTO knows VALUES (?,?)", ((node, neighbor) for node, neighbors in graph.adjacency.items() for neighbor in neighbors))
        self.db.executescript("CREATE TEMP TABLE visited(id TEXT PRIMARY KEY,parent TEXT,depth INTEGER); CREATE TEMP TABLE frontier(id TEXT PRIMARY KEY)")
        self.db.commit()

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
            # SQL owns frontier joins and visited state. Python controls depth loops.
            source, target = plan.parameters["source"], plan.parameters["target"]
            self.db.execute("DELETE FROM visited")
            self.db.execute("DELETE FROM frontier")
            self.db.execute("INSERT INTO visited VALUES (?,NULL,0)", (source,))
            self.db.execute("INSERT INTO frontier VALUES (?)", (source,))
            depth = 0
            sql = "INSERT INTO visited SELECT target,source,? FROM (SELECT k.target,k.source,ROW_NUMBER() OVER (PARTITION BY k.target ORDER BY LENGTH(k.source),k.source) AS pick FROM knows k JOIN frontier f ON k.source=f.id LEFT JOIN visited v ON v.id=k.target WHERE v.id IS NULL) WHERE pick=1"
            while not self.db.execute("SELECT 1 FROM visited WHERE id=?", (target,)).fetchone():
                depth += 1
                cursor = self.db.execute(sql, (depth,))
                if cursor.rowcount == 0:
                    break
                self.db.execute("DELETE FROM frontier")
                self.db.execute("INSERT INTO frontier SELECT id FROM visited WHERE depth=?", (depth,))
            value = None
            if self.db.execute("SELECT 1 FROM visited WHERE id=?", (target,)).fetchone():
                value, node = [], target
                while node is not None:
                    value.append(node)
                    node = self.db.execute("SELECT parent FROM visited WHERE id=?", (node,)).fetchone()[0]
                value.reverse()
        return {"artifact": sql, "value": value,
                "implementation": "SQLite frontier joins and visited tables; Python loop control; shortest-path ties may differ from BFS"}

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
