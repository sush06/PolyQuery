"""Deterministic semantic slots, not a general-purpose text-to-query model.

Routing scores indicate matched features, not calibrated probabilities.
External planners can implement Planner and return the same validated Plan.
"""
import re
from dataclasses import asdict, dataclass, field
from typing import Protocol

from .data import EMPLOYEES


@dataclass(frozen=True)
class Plan:
    backend: str
    operation: str
    parameters: dict = field(default_factory=dict)
    reason: str = ""
    scores: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class Planner(Protocol):
    def plan(self, question: str) -> Plan: ...


class RulePlanner:
    def plan(self, question: str) -> Plan:
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            return Plan("abstain", "unsupported", reason="Provide a question of 1–2000 characters.")
        q = question.lower()
        scores = {
            "sql": sum(bool(re.search(p, q)) for p in [r"\b(count|how many)\b", r"\b(average|mean)\b", r"\b(revenue|list)\b", r"\b(total|sum)\s+sales\b"]),
            "graph": sum(bool(re.search(p, q)) for p in [r"\b(path|connection)\b", r"\b(reports|reporting|manager|downstream)\b"]),
            "code": sum(bool(re.search(p, q)) for p in [r"\bmedian\b", r"\b(correlation|standard deviation|stddev)\b"]),
        }
        def stop(reason):
            return Plan("abstain", "unsupported", reason=reason, scores=scores)

        if re.search(r"\b(delete|drop|update|insert|alter|truncate|execute|ignore|overwrite)\b", q):
            return stop("Only read-only analytical questions are supported.")
        if sum(s > 0 for s in scores.values()) > 1:
            return stop("Multiple backend intents detected; split the question into separate requests.")
        if re.search(r"\b(by|each|per)\s+department\b|\bgroup\s+by\b", q):
            return stop("Grouped queries are not supported in v0.1.")
        if ("median" in q and re.search(r"standard deviation|stddev", q)) or (re.search(r"\blist\b", q) and re.search(r"\b(count|how many)\b", q)):
            return stop("Request one operation at a time.")
        departments = sorted({r["department"] for r in EMPLOYEES})
        department_text = re.sub(r"\b(total|sum)\s+sales\b", "total revenue", q)
        mentioned = [d for d in departments if re.search(r"\b" + d.lower() + r"\b", department_text)]
        if len(mentioned) > 1:
            return stop("Only one department filter is supported per request.")
        department = mentioned[0] if mentioned else None
        if not scores["graph"]:
            filters = re.findall(r"\b(?:in|for|from)\s+(?:the\s+)?([a-z]+)", q)
            if any(f not in {d.lower() for d in departments} for f in filters):
                return stop("Unknown or unsupported filter; use Engineering, Sales, or Research.")
        # Fail closed on unsupported modifiers rather than silently broadening a query.
        modifier_text = re.sub(r"\bbetween salary and experience\b", "salary and experience", q) if "correlation" in q else q
        if re.search(r"\b(except|excluding|not|above|below|over|under|top|bottom|last|first|before|after|between|greater|less|percentile|percent|age|location|remote|202\d)\b|[<>!=]", modifier_text):
            return stop("This modifier or field is outside the supported query grammar.")
        if re.search(r"\b(?:in|for|from)\s+(?:the\s+)?(?:[a-z]+)\s+department\b", q) and not department:
            return stop("Unknown department; available: Engineering, Sales, Research.")
        if scores["graph"]:
            names = [r["name"] for r in EMPLOYEES if re.search(r"\b" + r["name"].lower() + r"\b", q)]
            if department:
                return stop("Department filters are not supported for graph operations.")
            if re.search(r"\b(path|connection)\b", q) and len(names) == 2:
                # Preserve source/target order in the question.
                names.sort(key=lambda n: q.index(n.lower()))
                return Plan("graph", "shortest_path", {"source": names[0], "target": names[1]}, "Relationship traversal; undirected shortest path.", scores)
            if re.search(r"\b(reports|reporting|downstream)\b", q) and len(names) == 1:
                return Plan("graph", "descendants", {"source": names[0]}, "Directed manager-to-report traversal, all depths.", scores)
            return stop("Graph questions require two known people for a path or one for downstream reports.")
        if scores["code"]:
            if re.search(r"\bcorrelation\b", q) and "salary" in q and "experience" in q and not department:
                return Plan("code", "correlation", {}, "Pearson correlation over salary and experience.", scores)
            if "salary" in q:
                operation = "median" if "median" in q else "stdev" if re.search(r"standard deviation|stddev", q) else None
                if operation:
                    return Plan("code", operation, {"department": department}, "Generated statistical expression over a bounded fixture.", scores)
            return stop("Supported code operations: salary median, sample salary stddev, salary/experience correlation.")
        if scores["sql"]:
            if re.search(r"\b(revenue|sales)\b", q) and re.search(r"\b(total|sum)\b", q):
                return Plan("sql", "revenue", {"department": department}, "Relational sum with optional department filter.", scores)
            if re.search(r"\b(average|mean)\b", q) and "salary" in q:
                return Plan("sql", "average_salary", {"department": department}, "Relational salary aggregation.", scores)
            if re.search(r"\b(count|how many)\b", q) and re.search(r"\bemployees?\b", q):
                return Plan("sql", "count", {"department": department}, "Relational employee count.", scores)
            if re.search(r"\blist\b", q) and re.search(r"\bemployees?\b", q):
                return Plan("sql", "list", {"department": department}, "Relational employee projection.", scores)
        return stop("Unsupported question. Try count, average salary, total revenue, path, reports, or salary median/stddev.")
