"""Catalog-derived model context and redacted plan diagnostics."""

from dataclasses import asdict


class PlanContractError(ValueError):
    def __init__(self, category, *, repairable=False, stage=None):
        super().__init__(category)
        self.category = category
        self.repairable = repairable
        self.stage = stage or ("json_structure" if category in ("malformed_json", "invalid_schema") else "catalog_contract")


def catalog_context(catalog, *, bindings=False):
    operations = {}
    names = {name for spec in catalog.operations.values() for name in spec.parameters}
    for name, spec in sorted(catalog.operations.items()):
        inputs = {key: asdict(parameter) for key, parameter in spec.parameters.items()}
        for key, parameter in spec.parameters.items():
            if bindings and parameter.kind == "entity_ids":
                inputs[key]["binding"] = {
                    "selector": "whole_value",
                    "from_operations": sorted(
                        upstream for upstream, item in catalog.operations.items()
                        if item.result.kind == "entity_ids" and item.result.entity == parameter.domain
                    ),
                }
        operations[name] = {"backends": list(spec.backends), "parameters": inputs,
                            "result": asdict(spec.result), "description": spec.description}
    return {"source": catalog.source, "operations": operations,
            "parameter_names": sorted(names),
            "small_parameter_domains": {name: sorted(values) for name, values in catalog.domains.items()
                                        if len(values) <= 32}}


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise PlanContractError("malformed_json", repairable=True)
    return result


def plan_snapshot(payload, catalog):
    """Retain catalog identifiers and binding topology, with all literals removed."""
    if not isinstance(payload, dict):
        return {"shape": type(payload).__name__}
    backends = {"abstain"} | {backend for spec in catalog.operations.values() for backend in spec.backends}
    operations = {"unsupported"} | catalog.operations.keys()
    names = {name for spec in catalog.operations.values() for name in spec.parameters}

    def visible(value, allowed):
        return value if type(value) is str and value in allowed else "<redacted>"

    nodes = payload.get("nodes")
    ids = {node["id"]: f"node_{index}" for index, node in enumerate(nodes[:5], 1)
           if isinstance(node, dict) and type(node.get("id")) is str} if isinstance(nodes, list) else {}

    def entries(node, key):
        values = node.get(key)
        if not isinstance(values, list):
            return {"shape": type(values).__name__}
        return [{"name": visible(item.get("name"), names),
                 **({"node": ids.get(item["node"], "<redacted>")}
                    if key == "bindings" and type(item.get("node")) is str else {"value": "<redacted>"})}
                if isinstance(item, dict) else {"shape": type(item).__name__} for item in values[:8]]

    def summarized(node):
        if not isinstance(node, dict):
            return {"shape": type(node).__name__}
        return {"backend": visible(node.get("backend"), backends),
                "operation": visible(node.get("operation"), operations),
                "parameters": entries(node, "parameters"),
                **({"bindings": entries(node, "bindings")} if "bindings" in node else {})}

    if isinstance(nodes, list):
        outputs = payload.get("outputs")
        return {"kind": visible(payload.get("kind"), {"abstain", "single_plan", "dag_plan"}),
                "nodes": [summarized(node) for node in nodes[:5]],
                "outputs": [ids.get(value, "<redacted>") if type(value) is str else "<redacted>"
                            for value in outputs[:5]] if isinstance(outputs, list) else None}
    return summarized(payload)
