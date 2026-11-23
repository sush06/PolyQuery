"""Closed semantic contracts for typed bindings and ordered compositions."""
import re
from enum import StrEnum

from .backends import UnsupportedPlan
from .dag import DAGExecutor
from .planner import Plan

_ORDER = re.compile(r";\s*then\s+|,\s+(?:and then|followed by)\s+", re.I)
_NUMBER = {"first": 0, "second": 1, "third": 2, "fourth": 3}
_FIGURES = {"two": 2, "three": 3, "four": 4, "five": 5}
_CHECKLIST = re.compile(
    r"put (two|three|four|five) figures on (?:my|the) checklist:\s*", re.I)
_CHINOOK_MEASURES = {
    "average amount charged per invoice": ("mean", "Mean Invoice.Total.", "average invoice total"),
    "size of the entire customer register": ("count_customers", "Count Customer rows.", "how many customers"),
    "combined amount charged across all invoices": ("revenue", "Sum Invoice.Total.", "total invoice revenue"),
    "middle invoice amount": ("median", "Median Invoice.Total.", "median invoice total"),
}


class Evidence(StrEnum):
    MATCH = "verified_match"
    MISMATCH = "verified_mismatch"
    INSUFFICIENT = "insufficient_evidence"


def _identity(catalog, name, result):
    entity = result.entity
    if catalog.source == "synthetic" and name == "list":
        entity = "employee"
    keys = catalog.schema.get("entities", {}).get(entity, {}).get("key") or ()
    return entity, keys[0] if len(keys) == 1 else None


def binding_allowed(catalog, operation, parameter_name, producer, path):
    parameter = catalog.operations[operation].parameters.get(parameter_name)
    if parameter is None:
        return False
    result = catalog.operations[producer].result
    try:
        DAGExecutor._validate_binding(parameter_name, parameter, path, result)
    except UnsupportedPlan:
        return False
    if result.kind == "rows" and path and parameter.domain:
        entity, key = _identity(catalog, producer, result)
        return entity == parameter.domain and path[1] == key
    return True


def binding_context(catalog):
    routes = []
    for consumer, spec in catalog.operations.items():
        for parameter in spec.parameters:
            for producer, upstream in catalog.operations.items():
                result = upstream.result
                paths = [()]
                if result.kind == "rows":
                    paths.extend((0, field) for field in result.fields)
                elif result.kind in ("entity_ids", "path"):
                    paths.append((0,))
                for path in paths:
                    if binding_allowed(catalog, consumer, parameter, producer, path):
                        selector = [] if not path else ["index", *path[1:]]
                        routes.append([producer, selector, consumer, parameter])
    return routes


def _ordered(question, reference, catalog):
    text = re.sub(r"^(?:report|return|give|show) (?:the following )?in order:\s*", "",
                  question.strip(), flags=re.I)
    frame = _CHECKLIST.match(text)
    if frame:
        if catalog.source != "chinook":
            return None
        text = text[frame.end():]
    clauses = _ORDER.split(text)
    if not 2 <= len(clauses) <= 5 or frame and len(clauses) != _FIGURES[frame[1].lower()]:
        return None
    plans = [_ordered_clause(clause, reference, catalog) for clause in clauses]
    if any(plan is None for plan in plans):
        return None
    return [(plan.operation, plan.parameters, {}, (index-1,) if index else ())
            for index, plan in enumerate(plans)], tuple(range(len(plans)))


def _ordered_clause(clause, reference, catalog):
    text = " ".join(clause.casefold().strip().rstrip("?.").split()).removeprefix("the ")
    alias = _CHINOOK_MEASURES.get(text) if catalog.source == "chinook" else None
    if alias:
        operation, description, canonical = alias
        spec = catalog.operations.get(operation)
        if spec and spec.description == description and not spec.parameters and spec.result.kind in ("number", "integer"):
            return reference(canonical)
        return None
    return reference(clause)


def _projected(question, reference, catalog):
    # Entire source clause must independently verify. The consumer clauses
    # name the requested index, role, and operation with no ignored suffix.
    text = " ".join(question.strip().rstrip("? .").split())
    parts = re.split(r";\s*then\s+", text, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return None
    head, tail = parts
    if catalog.source == "synthetic":
        m = re.fullmatch(r"list (engineering|sales|research) employees by name(?: in ascending order)?", head, re.I)
        source = Plan("sql", "list", {"department": m[1].title()}) if m else None
        consumer = re.fullmatch(r"(direct reports|all downstream reports) of the (first|second|third|fourth) listed employee",
                                tail, re.I)
        if consumer and source:
            op = "direct_reports" if consumer[1].lower() == "direct reports" else "descendants"
            path = (_NUMBER[consumer[2].lower()], "name")
            return [(source.operation, source.parameters, {}, ()), (op, {}, {"source": (0, path)}, (0,))], (1,)
        return None
    if catalog.source != "ldbc":
        return None
    source = reference(head)
    route = re.fullmatch(
        r"shortest path (from the (first|second|third|fourth) listed friend to (?:person )?([0-9]+)"
        r"|from (?:person )?([0-9]+) to the (first|second|third|fourth) listed friend)", tail, re.I)
    if source is not None and source.operation == "friends" and route:
        role, endpoint, position = ("source", route[3], route[2]) if route[2] else (
            "target", route[4], route[5])
        opposing = "target" if role == "source" else "source"
        return [(source.operation, source.parameters, {}, ()),
                ("shortest_path", {opposing: endpoint}, {role: (0, (_NUMBER[position.lower()], "id"))}, (0,))], (1,)
    match = re.fullmatch(r"(?:list )?friends of the (first|second|third|fourth) (person on that path|listed friend)",
                         tail, re.I)
    expected = "shortest_path" if match and match[2].lower() == "person on that path" else "friends"
    if source is None or match is None or source.operation != expected:
        return None
    path = (_NUMBER[match[1].lower()],) if expected == "shortest_path" else (_NUMBER[match[1].lower()], "id")
    return [(source.operation, source.parameters, {}, ()), ("friends", {}, {"source": (0, path)}, (0,))], (1,)


def verify_intent(question, nodes, outputs, reference, catalog):
    expected = _projected(question, reference, catalog)
    if expected is None and catalog.source in ("chinook", "ldbc", "taxi"):
        expected = _ordered(question, reference, catalog)
    if expected is None:
        return Evidence.INSUFFICIENT
    specs, requested = expected
    positions = {node.id: i for i, node in enumerate(nodes)}
    if len(specs) != len(nodes) or tuple(positions.get(key) for key in outputs) != requested:
        return Evidence.MISMATCH
    for node, (operation, parameters, bindings, dependencies) in zip(nodes, specs, strict=True):
        projected = {name: (positions.get(binding.node), binding.path) for name, binding in node.bindings.items()}
        actual_deps = {positions.get(key) for key in node.depends_on} | {pos for pos, _ in projected.values()}
        if (node.operation != operation or projected != bindings or actual_deps != set(dependencies)
                or {k: v for k, v in node.parameters.items() if v is not None} !=
                   {k: v for k, v in parameters.items() if v is not None}):
            return Evidence.MISMATCH
    return Evidence.MATCH
