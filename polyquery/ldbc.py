"""Validated CsvBasic person/knows projection, using only the standard library."""
import csv
import re
from collections import deque
from pathlib import Path

from .backends import UnsupportedPlan
from .planner import Plan


class LDBCGraph:
    def __init__(self, people_path, edges_path):
        self.people = {}
        with Path(people_path).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="|")
            if not {"id", "firstName", "lastName"} <= set(reader.fieldnames or []):
                raise ValueError("People CSV requires id, firstName, lastName columns")
            for row in reader:
                identifier = row["id"]
                if not identifier or not identifier.isascii() or not identifier.isdecimal() or identifier in self.people:
                    raise ValueError(f"Invalid or duplicate person ID at line {reader.line_num}")
                if not row["firstName"] or not row["lastName"] or None in row or None in row.values():
                    raise ValueError(f"Malformed person row at line {reader.line_num}")
                self.people[identifier] = {"id": identifier, "first_name": row["firstName"], "last_name": row["lastName"]}
        if not self.people:
            raise ValueError("People CSV is empty")
        self.adjacency = {identifier: set() for identifier in self.people}
        self.edge_rows = 0
        with Path(edges_path).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter="|")
            header = next(reader, [])
            # Repeated Person.id headers require positional parsing.
            if header != ["Person.id", "Person.id", "creationDate"]:
                raise ValueError("Knows CSV requires Person.id|Person.id|creationDate")
            for row in reader:
                if len(row) != 3 or any(node not in self.people for node in row[:2]):
                    raise ValueError(f"Malformed knows row or missing person at line {reader.line_num}")
                source, target = row[:2]
                self.adjacency[source].add(target)
                self.adjacency[target].add(source)
                self.edge_rows += 1
        self.metadata = {
            "name": "ldbc", "people_file": str(Path(people_path).resolve()),
            "edges_file": str(Path(edges_path).resolve()), "people": len(self.people),
            "edge_rows": self.edge_rows,
            "undirected_edges": (sum(len(v) for v in self.adjacency.values()) +
                                 sum(k in v for k, v in self.adjacency.items())) // 2,
            "projection": "Undirected knows; duplicate edges collapsed; supplied files only",
        }

    def execute(self, plan):
        required = {"count_people": set(), "friends": {"source"}, "shortest_path": {"source", "target"}}
        if plan.operation not in required or set(plan.parameters) != required[plan.operation]:
            raise UnsupportedPlan("Invalid LDBC operation or parameters")
        if any(type(v) is not str or v not in self.people for v in plan.parameters.values()):
            raise UnsupportedPlan("Unknown LDBC person ID")
        if plan.operation == "count_people":
            value = len(self.people)
        elif plan.operation == "friends":
            value = [self.people[node] for node in sorted(self.adjacency[plan.parameters["source"]], key=int)]
        else:
            source, target = plan.parameters["source"], plan.parameters["target"]
            parents, queue = {source: None}, deque([source])
            while queue and target not in parents:
                node = queue.popleft()
                for neighbor in sorted(self.adjacency[node], key=int):
                    if neighbor not in parents:
                        parents[neighbor] = node
                        queue.append(neighbor)
            value = None
            if target in parents:
                value, node = [], target
                while node is not None:
                    value.append(node)
                    node = parents[node]
                value.reverse()
        return {"artifact": {"algorithm": "count" if plan.operation == "count_people" else "adjacency" if plan.operation == "friends" else "BFS",
                             "directed": False, **plan.parameters}, "value": value}


class LDBCPlanner:
    def __init__(self, graph):
        self.graph = graph

    def plan(self, question):
        if isinstance(question, str) and 0 < len(question) <= 2000:
            q = question.strip().lower().rstrip("?.")
            operation, parameters = None, {}
            if re.fullmatch(r"(?:how many (?:people|persons)|count (?:people|persons))", q):
                operation = "count_people"
            match = re.fullmatch(r"(?:list friends of|friends of) (?:person )?([0-9]+)", q)
            if match:
                operation, parameters = "friends", {"source": match[1]}
            match = re.fullmatch(r"(?:find (?:the )?)?shortest path from (?:person )?([0-9]+) to (?:person )?([0-9]+)", q)
            if match:
                operation, parameters = "shortest_path", {"source": match[1], "target": match[2]}
            if operation and all(node in self.graph.people for node in parameters.values()):
                return Plan("graph", operation, parameters, "LDBC person/knows projection; exact supported grammar.", {"graph": 1})
        return Plan("abstain", "unsupported", reason="Use count people, friends of PERSON_ID, or shortest path from PERSON_ID to PERSON_ID with known IDs.")
