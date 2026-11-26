"""Isolated Agent 2 experiment with catalog-specific argument assignments."""

import json
import re
from time import perf_counter

from jsonschema import Draft7Validator

from benchmarks.minimal_decomposition import PLACEHOLDER, protect_identifiers
from benchmarks.sequential_planning import BASELINE, NativeClient, array, ground_steps, record
from polyquery.constraints import unsafe_request_findings
from polyquery.llm_contract import catalog_context, unique_object
from polyquery.typed_intent import binding_context

SYSTEM_PROMPT = """You map semantic subtasks to approved catalog operations.

Select one approved operation for each task.

Assign each required parameter either a literal from the question or an upstream result.

Use only canonical parameter names from the catalog.

Preserve all identifiers, placeholders, constraints, and parameter roles.

Never substitute an explicit literal for a value that must come from another task.

Do not generate bindings, result types, SQL, or executable DAGs.

Abstain if the requested operation or constraints cannot be represented by the catalog.

Example from the employee statistics catalog:
Question: Find the median salary for Research employees.
Response: {"decision":"continue","steps":[{"operation":"median","arguments":{"department":{"source":"literal","value":"Research"}}}]}"""

UPSTREAM = record(source={"const": "upstream"})


def schema(catalog):
    variants = []
    for operation, spec in sorted(catalog.operations.items()):
        if not catalog.capable_backends(operation):
            continue
        arguments = {}
        for name, parameter in spec.parameters.items():
            literal = {"type": "string", "maxLength": 400}
            if parameter.kind == "entity_ids":
                literal = array(literal, len(catalog.domains[parameter.domain]))
            if parameter.nullable:
                literal = {"anyOf": [literal, {"type": "null"}]}
            if parameter.kind == "null":
                literal = {"type": "null"}
            arguments[name] = {"oneOf": [record(source={"const": "literal"}, value=literal), UPSTREAM]}
        variants.append(record(
            operation={"const": operation},
            arguments={"type": "object", "properties": arguments, "additionalProperties": False,
                       "required": [name for name, param in spec.parameters.items() if param.required]},
        ))
    return {
        "oneOf": [
            record(decision={"const": "continue"}, steps=array({"oneOf": variants} if variants else False) | {"minItems": 1}),
            record(decision={"const": "abstain"}, steps={"type": "array", "maxItems": 0}),
        ]
    }


def protected_input(question, decomposition):
    protected, mapping = protect_identifiers(question)
    tokens = {value: key for key, value in mapping.items()}
    ids = "|".join(re.escape(value) for value in sorted(tokens, key=len, reverse=True))
    pattern = re.compile(rf"(?<![\w./+-])(?:{ids})(?![\w/-]|[.,][0-9])") if tokens else None

    def replace(value):
        if isinstance(value, str):
            if PLACEHOLDER.search(value):
                raise ValueError("Decomposition contains reserved placeholder syntax")
            return pattern.sub(lambda match: tokens[match[0]], value) if pattern else value
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    return protected, replace(decomposition), mapping


def adapt(payload, catalog, task_count, protected_question, mapping):
    Draft7Validator(schema(catalog)).validate(payload)
    routes = {(producer, consumer, role) for producer, _, consumer, role in binding_context(catalog)}

    def literal(value):
        if isinstance(value, list):
            return [literal(item) for item in value]
        if value is None:
            return None
        if value in mapping:
            if not re.search(rf"(?<!\w){re.escape(value)}(?!\w)", protected_question):
                raise ValueError("Identifier placeholder is absent from the question")
            return mapping[value]
        if PLACEHOLDER.search(value):
            raise ValueError("Unknown or malformed identifier placeholder")
        boundary = rf"(?<![\w./+-]){re.escape(value)}(?![\w/-]|[.,][0-9])"
        if not value or value in mapping.values() or not re.search(boundary, protected_question, re.IGNORECASE):
            raise ValueError("Literal is not present in the protected question")
        return value

    steps = []
    for row in payload["steps"]:
        literals, pending = [], []
        for name, assignment in row["arguments"].items():
            if assignment["source"] == "upstream":
                if not any((step["operation"], row["operation"], name) in routes for step in steps):
                    raise ValueError("No earlier operation can supply this upstream argument")
                pending.append(name)
            else:
                literals.append({"name": name, "value": literal(assignment["value"])})
        steps.append({"operation": row["operation"], "literals": literals, "required_inputs": pending,
                      "expected_output": catalog.operations[row["operation"]].result.kind})
    adapted = {"steps": steps, "decision": payload["decision"]}
    if adapted["decision"] == "continue":
        ground_steps(adapted, catalog, task_count)
    return adapted


def run(question, decomposition, catalog):
    if unsafe_request_findings(question):
        raise ValueError("Unsafe request refused before inference")
    protected, tasks, mapping = protected_input(question, decomposition)
    user = json.dumps({"question": protected, "decomposition": tasks, "catalog": catalog_context(catalog)})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    contract = schema(catalog)
    client = NativeClient(False)
    started = perf_counter()
    reply = client.create(model=BASELINE, messages=messages, temperature=0, max_tokens=1200,
                          response_format={"json_schema": {"schema": contract}})
    report = {"messages": messages, "schema": contract, "identifier_map": mapping,
              "wall_ms": (perf_counter() - started) * 1000, "calls": len(client.draws),
              "provider": {key: value for key, value in client.draws[0].items() if key != "response"},
              "raw": reply.choices[0].message.content, "schema_valid": False, "catalog_valid": None}
    try:
        payload = json.loads(report["raw"], object_pairs_hook=unique_object)
        report["parsed"] = payload
        report["schema_errors"] = [error.message for error in Draft7Validator(contract).iter_errors(payload)]
        report["schema_valid"] = not report["schema_errors"]
        if report["schema_valid"]:
            report["adapted"] = adapt(payload, catalog, len(decomposition["tasks"]), protected, mapping)
            if payload["decision"] == "continue":
                report["catalog_valid"] = True
    except (ValueError, TypeError) as error:
        report["validation_error"] = str(error)
        if report["schema_valid"]:
            report["catalog_valid"] = False
    return report
