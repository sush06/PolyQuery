"""Native SQL, graph traversal, and generated-expression adapters.

Code expressions are interpreted using a tiny AST allowlist, never exec/eval.
This intentionally does not support arbitrary Python or act as a sandbox.
"""
import ast
import sqlite3
import statistics
from collections import deque

from .data import EMPLOYEES, REPORTS, SALES
from .planner import Plan


class UnsupportedPlan(ValueError):
    pass


def validate(plan, operations, required=(), optional=()):
    if plan.operation not in operations:
        raise UnsupportedPlan(f"Unsupported operation: {plan.operation}")
    if not set(required) <= set(plan.parameters) or set(plan.parameters) - set(required) - set(optional):
        raise UnsupportedPlan("Invalid plan parameters")
    department = plan.parameters.get("department")
    if department is not None and department not in {r["department"] for r in EMPLOYEES}:
        raise UnsupportedPlan("Unknown department")


class SQLBackend:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("CREATE TABLE employees(name TEXT, department TEXT, salary REAL, experience REAL); CREATE TABLE sales(department TEXT, amount REAL);")
        self.db.executemany("INSERT INTO employees VALUES (:name,:department,:salary,:experience)", EMPLOYEES)
        self.db.executemany("INSERT INTO sales VALUES (?,?)", SALES)
        self.db.commit()
        self.db.execute("PRAGMA query_only = ON")
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
        self.db.set_authorizer(lambda action, *args: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)

    def execute(self, plan: Plan):
        validate(plan, {"count", "average_salary", "revenue", "list"}, optional=("department",))
        statements = {
            "count": "SELECT COUNT(*) AS value FROM employees",
            "average_salary": "SELECT AVG(salary) AS value FROM employees",
            "revenue": "SELECT SUM(amount) AS value FROM sales",
            "list": "SELECT name, department, salary, experience FROM employees",
        }
        sql = statements[plan.operation]
        params = []
        if plan.parameters.get("department"):
            sql += " WHERE department = ?"
            params.append(plan.parameters["department"])
        if plan.operation == "list":
            sql += " ORDER BY name"
        rows = [dict(r) for r in self.db.execute(sql, params).fetchmany(100)]
        return {"artifact": sql, "bindings": params, "value": rows if plan.operation == "list" else rows[0]["value"]}

    def close(self):
        self.db.close()


class GraphBackend:
    def execute(self, plan):
        required = ("source", "target") if plan.operation == "shortest_path" else ("source",)
        validate(plan, {"shortest_path", "descendants"}, required=required)
        names = {r["name"] for r in EMPLOYEES}
        if any(v not in names for v in plan.parameters.values()):
            raise UnsupportedPlan("Unknown graph node")
        adjacency = {name: set() for name in names}
        for manager, report in REPORTS:
            adjacency[manager].add(report)
            if plan.operation == "shortest_path":
                adjacency[report].add(manager)
        source = plan.parameters["source"]
        queue, visited, descendants = deque([(source, [source])]), {source}, []
        value = None
        while queue:
            node, path = queue.popleft()
            if plan.operation == "shortest_path" and node == plan.parameters["target"]:
                value = path
                break
            for neighbor in sorted(adjacency[node]):
                if neighbor not in visited:
                    visited.add(neighbor)
                    descendants.append(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        if plan.operation == "descendants":
            value = sorted(descendants)
        return {"artifact": {"algorithm": "BFS", "directed": plan.operation == "descendants", **plan.parameters}, "value": value}


class ExpressionInterpreter:
    functions = {"median": statistics.median, "stdev": statistics.stdev, "correlation": statistics.correlation}

    def run(self, expression, variables):
        if len(expression) > 200:
            raise UnsupportedPlan("Expression is too long")
        node = ast.parse(expression, mode="eval").body
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id not in self.functions or node.keywords:
            raise UnsupportedPlan("Only allowlisted statistical calls are supported")
        arity = 2 if node.func.id == "correlation" else 1
        if len(node.args) != arity or any(not isinstance(a, ast.Name) or a.id not in variables for a in node.args):
            raise UnsupportedPlan("Only provided numeric vectors may be arguments")
        vectors = [variables[a.id] for a in node.args]
        if any(not isinstance(v, list) or len(v) > 10000 or any(type(n) not in (int, float) for n in v) for v in vectors):
            raise UnsupportedPlan("Expected bounded numeric lists")
        return self.functions[node.func.id](*vectors)


class CodeBackend:
    def execute(self, plan):
        validate(plan, {"median", "stdev", "correlation"}, optional=("department",))
        if plan.operation == "correlation" and plan.parameters.get("department"):
            raise UnsupportedPlan("Filtered correlation is not supported")
        rows = [r for r in EMPLOYEES if not plan.parameters.get("department") or r["department"] == plan.parameters["department"]]
        variables = {"salary": [r["salary"] for r in rows], "experience": [r["experience"] for r in rows]}
        expression = "correlation(salary, experience)" if plan.operation == "correlation" else f"{plan.operation}(salary)"
        return {"artifact": expression, "rows_used": len(rows), "value": ExpressionInterpreter().run(expression, variables)}

