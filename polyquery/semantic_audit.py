"""Audit blocked DAG proposals against independent benchmark references."""


def audit_block(proposal, case, reference, gold):
    """Compare authorized intent and output contracts without running the proposal."""
    if proposal is None:
        return {"classification": "indeterminate", "reason": "No catalog-valid proposal was captured."}
    kind, nodes, outputs = proposal
    indexed = {node.id: (number, node) for number, node in enumerate(nodes, 1)}
    evidence = {
        "kind": kind,
        "nodes": [{"backend": node.backend, "operation": node.operation,
                   "parameter_names": sorted(node.parameters), "has_bindings": bool(node.bindings)}
                  for node in nodes],
        "output_nodes": [indexed[node][0] for node in outputs],
    }

    def verdict(classification, reason):
        return {"classification": classification, "reason": reason, "proposal": evidence}

    if "expected_refusal" in case:
        return verdict("true_positive", f"The reference requires refusal for {case['expected_refusal']} intent.")
    specs = case.get("reference", ())
    if len(outputs) != len(specs):
        return verdict("true_positive", "The proposal omits an expected output or adds an unrequested one.")
    if (kind == "single_plan" and len(nodes) != 1) or set(indexed) != set(outputs):
        return verdict("indeterminate", "The proposal includes steps or an output shape outside the direct reference.")
    if any(node.bindings for node in nodes):
        return verdict("indeterminate", "An independent reference for this dependency is unavailable.")
    argument_names = {"friends": ("source",), "shortest_path": ("source", "target")}
    compared = []
    for output, spec, expected in zip(outputs, specs, gold):
        _, node = indexed[output]
        names = argument_names.get(spec["operation"], ())
        operation_matches = node.operation == spec["operation"]
        arguments_match = set(node.parameters) == set(names) and [node.parameters[key] for key in names] == spec["args"]
        compared.append({"operation_matches": operation_matches, "arguments_match": arguments_match,
                         "independent_reference_confirmed": reference.matches(spec, reference.answer(spec), expected)})
    evidence["reference_comparison"] = compared
    if any(not item["independent_reference_confirmed"] for item in compared):
        return verdict("indeterminate", "The independent reference could not be reproduced.")
    if any(not item["operation_matches"] or not item["arguments_match"] for item in compared):
        return verdict("true_positive", "An output's operation or arguments differs from the independent reference.")
    return verdict("false_positive", "All ordered outputs use catalog-approved equivalent operations and exact reference arguments.")
