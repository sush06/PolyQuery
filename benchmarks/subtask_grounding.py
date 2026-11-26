"""Isolated Agent 2 experiment with one semantic subtask per model call."""

import json
from time import perf_counter

from jsonschema import Draft7Validator

from benchmarks.minimal_grounding import SYSTEM_PROMPT, adapt, protected_input, schema
from benchmarks.sequential_planning import BASELINE, NativeClient, record
from polyquery.constraints import unsafe_request_findings
from polyquery.llm_contract import catalog_context, unique_object

SUBTASK_PROMPT = SYSTEM_PROMPT.replace(
    "Select one approved operation for each task.",
    "Select one approved operation for the current subtask. Prerequisite references are context only."
).replace(
    '{"decision":"continue","steps":[{"operation":"median","arguments":{"department":{"source":"literal","value":"Research"}}}]}',
    '{"decision":"continue","step":{"operation":"median","arguments":{"department":{"source":"literal","value":"Research"}}}}'
)


def subtask_schema(catalog):
    continuing, _ = schema(catalog)["oneOf"]
    return {"oneOf": [
        record(decision={"const": "continue"}, step=continuing["properties"]["steps"]["items"]),
        record(decision={"const": "abstain"}, step={"type": "null"}),
    ]}


def task_context(tasks, position, prerequisite_positions, catalog):
    if any(type(index) is not int or not 1 <= index < position for index in prerequisite_positions):
        raise ValueError("Prerequisites must reference earlier tasks")
    return {
        "subtask": tasks[position - 1],
        "prerequisites": [
            {"task": index, "expected_result": tasks[index - 1]["expected_result"]}
            for index in prerequisite_positions
        ],
        "catalog": catalog_context(catalog),
    }


def combine(responses, catalog, task_count, protected_question, mapping):
    if len(responses) != task_count:
        raise ValueError("A grounding response is required for each subtask")
    validator = Draft7Validator(subtask_schema(catalog))
    steps = []
    for response in responses:
        validator.validate(response)
        if response["decision"] != "continue":
            raise ValueError("A subtask abstained")
        steps.append(response["step"])
    return adapt({"decision": "continue", "steps": steps}, catalog, task_count, protected_question, mapping)


def collect(question, decomposition, prerequisites, catalog):
    if unsafe_request_findings(question):
        raise ValueError("Unsafe request refused before inference")
    protected_question, decomposition, mapping = protected_input(question, decomposition)
    tasks = decomposition["tasks"]
    if len(prerequisites) != len(tasks):
        raise ValueError("Prerequisite context is required for each subtask")
    contract = subtask_schema(catalog)
    contexts = [task_context(tasks, index, positions, catalog)
                for index, positions in enumerate(prerequisites, 1)]
    client = NativeClient(False)
    rows = []
    started = perf_counter()
    for context in contexts:
        messages = [{"role": "system", "content": SUBTASK_PROMPT},
                    {"role": "user", "content": json.dumps(context)}]
        tick = perf_counter()
        row = {"messages": messages, "schema_valid": False}
        try:
            response = client.create(model=BASELINE, messages=messages, temperature=0, max_tokens=1200,
                                     response_format={"json_schema": {"schema": contract}})
            row["raw"] = response.choices[0].message.content
            row["parsed"] = json.loads(row["raw"], object_pairs_hook=unique_object)
            row["schema_errors"] = [error.message for error in Draft7Validator(contract).iter_errors(row["parsed"])]
            row["schema_valid"] = not row["schema_errors"]
        except (OSError, TypeError, ValueError) as error:
            row["error"] = str(error)
        row["wall_ms"] = (perf_counter() - tick) * 1000
        row["provider"] = {key: value for key, value in client.draws[-1].items()
                           if key not in {"response", "exception"}}
        rows.append(row)
    return {"rows": rows, "calls": len(client.draws), "wall_ms": (perf_counter() - started) * 1000,
            "schema": contract, "protected_question": protected_question, "identifier_map": mapping}
