"""Compile the opt-in semantic recipe into the existing Qwen typed contract."""
import json

from .llm_contract import PlanContractError
from .typed_intent import binding_allowed, binding_context

MAX_STEPS = 5
MAX_ARGS = 8
_STRING = {"type": "string"}
_INT = {"type": "integer"}


def _object(**properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def _array(item):
    return {"type": "array", "items": item}


def _optional(kind):
    return {"anyOf": [kind, {"type": "null"}]}


def schema(catalog):
    names = sorted({key for spec in catalog.operations.values() for key in spec.parameters})
    return _object(
        steps=_array(_object(
            op={"type": "string", "enum": sorted(catalog.operations)},
            args=_array(_object(name={"type": "string", "enum": names or ["unused"]},
                                value=_optional(_STRING))),
            links=_array(_object(step=_INT, item=_optional(_INT), field=_optional(_STRING),
                                 input=_optional(_STRING))),
            after=_array(_INT),
        )),
        outputs=_array(_INT),
    )


def instructions(catalog):
    ops = {name: {"args": sorted(spec.parameters), "description": spec.description}
           for name, spec in sorted(catalog.operations.items())}
    routes = binding_context(catalog)
    domains = {name: sorted(values) for name, values in catalog.domains.items() if len(values) <= 32}
    return (
        "Return only a JSON recipe. Never emit code, SQL, or answers. Use empty steps and outputs "
        "to abstain on unsafe, ambiguous, or unsupported requests. Number steps from 1 by their "
        "position. Outputs lists only the requested steps in order. Put literal parameters in args. "
        "Use links for data dependencies and after for ordering without a data dependency. "
        "A link names an earlier step. Item is a one-based row or list position, or null for a "
        "whole result. Use null for field or input when the catalog uniquely determines it. "
        "If there is a choice, name the requested field or input. Never guess a missing scope. "
        "Use empty arrays where no arguments, links, or ordering edges are needed. "
        "For independent sequential requests put the preceding step number in after.\n"
        "OPERATIONS: " + json.dumps(ops, separators=(",", ":")) + "\n"
        "SMALL DOMAINS: " + json.dumps(domains, separators=(",", ":")) + "\n"
        "LINKS [producer, selection, consumer, input]: " + json.dumps(routes, separators=(",", ":"))
    )


def _record(value, keys):
    if type(value) is not dict or value.keys() != keys:
        raise PlanContractError("invalid_schema")


def _sequence(value, cap):
    if type(value) is not list or len(value) > cap:
        raise PlanContractError("invalid_schema")


def _id(number):
    return f"stage_{number}"


def _link(catalog, consumer, link, producer, used):
    index, field, parameter = (link[key] for key in ("item", "field", "input"))
    if (index is not None and (type(index) is not int or not 1 <= index <= 101)
            or field is not None and (type(field) is not str or index is None)
            or parameter is not None and type(parameter) is not str):
        raise PlanContractError("incorrect_binding")
    result = catalog.operations[producer].result
    if index is None:
        paths = [()]
    elif result.kind == "rows":
        paths = [(index - 1, field)] if field is not None else [
            (index - 1, name) for name in result.fields]
    else:
        paths = [(index - 1,)] if field is None else []
    parameters = catalog.operations[consumer].parameters
    options = [(name, path) for name in parameters if name not in used and (parameter is None or parameter == name)
               for path in paths if binding_allowed(catalog, consumer, name, producer, path)]
    if len(options) != 1:
        raise PlanContractError("ambiguous_binding" if options else "incorrect_binding")
    name, path = options[0]
    return {"name": name, "node": _id(link["step"]), "path": list(path)}


def compile_recipe(payload, catalog):
    """Validate the IR envelope and derive only unique, catalog-legal routes."""
    if payload is None:
        return None
    _record(payload, {"steps", "outputs"})
    steps, outputs = payload["steps"], payload["outputs"]
    _sequence(steps, MAX_STEPS)
    _sequence(outputs, MAX_STEPS)
    if not steps and not outputs:
        return {"kind": "abstain", "nodes": [], "outputs": []}
    if (not outputs or any(type(n) is not int or not 1 <= n <= len(steps) for n in outputs)
            or len(set(outputs)) != len(outputs)):
        raise PlanContractError("invalid_schema")
    nodes = []
    for ordinal, step in enumerate(steps, 1):
        _record(step, {"op", "args", "links", "after"})
        operation = step["op"]
        if type(operation) is not str or not catalog.capable_backends(operation):
            raise PlanContractError("unsupported_operation")
        args, links, after = (step[key] for key in ("args", "links", "after"))
        _sequence(args, MAX_ARGS)
        _sequence(links, MAX_ARGS)
        _sequence(after, MAX_STEPS - 1)
        if any(type(n) is not int or not 1 <= n < ordinal for n in after) or len(set(after)) != len(after):
            raise PlanContractError("incorrect_binding")
        used = set()
        for arg in args:
            _record(arg, {"name", "value"})
            if type(arg["name"]) is not str or arg["name"] in used:
                raise PlanContractError("invalid_parameters")
            used.add(arg["name"])
        bindings = []
        for link in links:
            _record(link, {"step", "item", "field", "input"})
            upstream = link["step"]
            if type(upstream) is not int or not 1 <= upstream < ordinal:
                raise PlanContractError("incorrect_binding")
            binding = _link(catalog, operation, link, nodes[upstream - 1]["operation"], used)
            bindings.append(binding)
            used.add(binding["name"])
        nodes.append({"id": _id(ordinal), "backend": catalog.capable_backends(operation)[0],
                      "operation": operation, "parameters": args, "bindings": bindings,
                      "depends_on": [_id(n) for n in after]})
    return {"kind": "single_plan" if len(steps) == 1 else "dag_plan",
            "nodes": nodes, "outputs": [_id(n) for n in outputs]}

def recipe_snapshot(payload, catalog):
    """Keep public structural selectors while discarding arbitrary literals."""
    if type(payload) is not dict or type(payload.get("steps")) is not list:
        return {"shape": type(payload).__name__}
    fields = {name for spec in catalog.operations.values() for name in spec.result.fields}
    parameters = {name for spec in catalog.operations.values() for name in spec.parameters}

    def name(value, allowed):
        return value if type(value) is str and value in allowed else "<redacted>"

    def number(value):
        return value if type(value) is int and 1 <= value <= 101 else "<redacted>"

    def items(value, limit, encode):
        if type(value) is not list:
            return {"shape": type(value).__name__}
        return [encode(item) for item in value[:limit]]

    def link(item):
        if type(item) is not dict:
            return {"shape": type(item).__name__}
        return {"step": number(item.get("step")),
                "item": None if item.get("item") is None else number(item["item"]),
                "field": None if item.get("field") is None else name(item["field"], fields),
                "input": None if item.get("input") is None else name(item["input"], parameters)}

    def step(item):
        if type(item) is not dict:
            return {"shape": type(item).__name__}
        return {"op": name(item.get("op"), catalog.operations),
                "args": items(item.get("args"), MAX_ARGS, lambda arg: {
                    "name": name(arg.get("name"), parameters)} if type(arg) is dict else "<redacted>"),
                "links": items(item.get("links"), MAX_ARGS, link),
                "after": items(item.get("after"), MAX_STEPS, number)}

    return {"steps": items(payload["steps"], MAX_STEPS, step),
            "outputs": items(payload.get("outputs"), MAX_STEPS, number)}
