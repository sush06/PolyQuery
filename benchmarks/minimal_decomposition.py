"""Standalone, opt-in Agent 1 experiment using the existing local Qwen client."""

import argparse
import json
import re
from decimal import Decimal
from time import perf_counter

from jsonschema import Draft7Validator

from benchmarks.sequential_planning import BASELINE, NativeClient, POSITION, TEXT, array, record
from polyquery.constraints import unsafe_request_findings
from polyquery.llm_contract import unique_object

# Recognize identity syntax without deciding what operation should execute or
# whether an entity exists. Bare numeric values outside these slots stay intact.
INTEGER_ID = r"[0-9]+(?![\w/-]|[.,][0-9])"
PATH_ENDPOINTS = re.compile(
    rf"\bshortest path from (?:person )?(?P<source>{INTEGER_ID})"
    rf" to (?:person )?(?P<target>{INTEGER_ID})",
    re.IGNORECASE,
)
LABELED_ID = re.compile(
    rf"\b(?:person(?:\s+id)?|id)\s*[:#]?\s+(?P<identifier>{INTEGER_ID})"
    rf"|\bid[:#]\s*(?P<compact>{INTEGER_ID})",
    re.IGNORECASE,
)
PLACEHOLDER = re.compile(r"(?<!\w)ENTITY_[\w-]*(?:[.,/][0-9]+)?", re.IGNORECASE)
NUMBER = re.compile(r"(?<![\w.])[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?!\w|\.\d)")
TASK_REFERENCE = re.compile(r"\b(?:task|step)\s*(?:#\s*)?[1-5]\b", re.IGNORECASE)
ORDINAL_WORDS = ("first", "second", "third", "fourth", "fifth")
PERSON_POSITION = re.compile(
    r"\bperson\s+[0-9]+\s+(?:on|in|along|from|of)\s+(?:(?:the|that)\s+)?(?:path|list)\b",
    re.IGNORECASE,
)


def identifier_spans(text):
    # A position in an explicitly named result is not a person identifier.
    positions = [match.span() for match in PERSON_POSITION.finditer(text)]
    return sorted({match.span(group) for pattern in (PATH_ENDPOINTS, LABELED_ID)
                   for match in pattern.finditer(text) for group, value in match.groupdict().items()
                   if value is not None and not any(
                       start <= match.start(group) < end for start, end in positions)})

SCHEMA = record(
    decision={"enum": ["continue", "abstain"]},
    tasks=array(record(intent=TEXT, depends_on=array(POSITION, 4))),
    final_task={"anyOf": [POSITION, {"type": "null"}]},
    ambiguity=TEXT,
)

SYSTEM_PROMPT = """You decompose analytical questions into ordered semantic tasks.

Include only tasks explicitly requested or logically necessary to obtain the requested result.

Preserve all entities, placeholders, filters, ordinals, constraints, and directions.

Use one-based task references for dependencies.

Do not discover databases, verify entity existence, check system capabilities, or explain execution feasibility.

Identify the task that produces the requested final answer.

Abstain only when the intended meaning is genuinely ambiguous.

Example question: Find the artist with the highest total sales revenue, then list that artist's albums.
Example response:
{"decision":"continue","tasks":[{"intent":"Find the artist with the highest total sales revenue.","depends_on":[]},{"intent":"List the albums of the artist found in the previous task.","depends_on":[1]}],"final_task":2,"ambiguity":""}"""

NESTED_PROMPT = (
    "Decompose analytical questions into tasks ordered by logical prerequisites, not by the order "
    "in which actions appear in the sentence.\n\n"
    + SYSTEM_PROMPT.split("\n\n", 1)[1].split("\n\nExample question:", 1)[0]
    + "\n\nExample question: List the albums of the artist who generated the highest total sales revenue.\n"
    'Example response:\n{"decision":"continue","tasks":[{"intent":"Find the artist with the highest total sales revenue.",'
    '"depends_on":[]},{"intent":"List albums of the artist found in task 1.","depends_on":[1]}],'
    '"final_task":2,"ambiguity":""}'
)


def protect_identifiers(question):
    if PLACEHOLDER.search(question):
        raise ValueError("Question contains reserved placeholder syntax")
    pieces = []
    position = 0
    by_value = {}
    for start, end in identifier_spans(question):
        value = question[start:end]
        token = by_value.setdefault(value, f"ENTITY_{len(by_value) + 1}")
        pieces.extend((question[position:start], token))
        position = end
    pieces.append(question[position:])
    return "".join(pieces), {token: value for value, token in by_value.items()}


def numeric_values(text):
    return {Decimal(match.group()) for match in NUMBER.finditer(text)}


def validate_identifiers(protected_question, intent):
    if identifier_spans(intent):
        raise ValueError("Entity identifier must use its protected placeholder")
    # Structured task references never enter this check. In prose allow task
    # labels and numeric restatements of a selection already in the question.
    values = numeric_values(protected_question)
    for index, word in enumerate(ORDINAL_WORDS, 1):
        suffix = ("st", "nd", "rd", "th", "th")[index - 1]
        if re.search(rf"\b(?:{word}|{index}{suffix})\b", protected_question, re.IGNORECASE):
            values.add(Decimal(index))
    prose = TASK_REFERENCE.sub("", intent)
    if numeric_values(prose) - values:
        raise ValueError("Unverified numeric literal or identifier")


def restore(payload, question):
    Draft7Validator(SCHEMA).validate(payload)
    protected_question, mapping = protect_identifiers(question)
    tasks = payload["tasks"]
    if payload["decision"] == "abstain":
        if tasks or payload["final_task"] is not None or not payload["ambiguity"].strip():
            raise ValueError("Invalid abstention")
    elif not tasks or payload["ambiguity"] or payload["final_task"] not in range(1, len(tasks) + 1):
        raise ValueError("Invalid continuation")

    seen = set()

    def replace(match):
        token = match.group()
        if token not in mapping:
            raise ValueError(f"Unknown identifier placeholder: {token}")
        seen.add(token)
        return mapping[token]

    restored = []
    for index, task in enumerate(tasks, 1):
        deps = task["depends_on"]
        if not task["intent"].strip() or len(deps) != len(set(deps)) or any(dep >= index for dep in deps):
            raise ValueError("Task intents must be nonempty and dependencies must reference earlier tasks")
        intent = task["intent"]
        # Check reserved tokens first so malformed placeholders get a precise
        # error and cannot be masked by the remaining literal checks.
        text = PLACEHOLDER.sub(replace, intent)
        validate_identifiers(protected_question, intent)
        if {text[start:end] for start, end in identifier_spans(text)}.difference(mapping.values()):
            raise ValueError("Restored entity identifier is absent from the original question")
        restored.append(task | {"intent": text})
    if tasks and seen != mapping.keys():
        raise ValueError("A protected identifier is missing from the tasks")
    ambiguity = PLACEHOLDER.sub(replace, payload["ambiguity"])
    validate_identifiers(protected_question, payload["ambiguity"])
    return payload | {"tasks": restored, "ambiguity": ambiguity}


def run(question, *, system_prompt=SYSTEM_PROMPT):
    if unsafe_request_findings(question):
        raise ValueError("Unsafe request refused before inference")
    protected, mapping = protect_identifiers(question)
    client = NativeClient(False)
    started = perf_counter()
    reply = client.create(
        model=BASELINE, temperature=0, max_tokens=1200,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": protected}],
        response_format={"json_schema": {"schema": SCHEMA}},
    )
    elapsed = (perf_counter() - started) * 1000
    raw = reply.choices[0].message.content
    report = {
        "system_prompt": system_prompt, "user_prompt": protected, "identifier_map": mapping,
        "raw": raw, "wall_ms": elapsed, "calls": len(client.draws),
        "provider": {key: value for key, value in client.draws[0].items() if key != "response"},
    }
    try:
        parsed = json.loads(raw, object_pairs_hook=unique_object)
        report["parsed"] = parsed
        report["schema_valid"] = Draft7Validator(SCHEMA).is_valid(parsed)
        if not report["schema_valid"]:
            raise ValueError("Response did not match the minimal decomposition schema")
        report["restored"] = restore(parsed, question)
    except (ValueError, TypeError) as error:
        report["validation_error"] = str(error)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--nested", action="store_true", help="Use the nested-prerequisite experimental prompt.")
    args = parser.parse_args()
    print(json.dumps(run(args.question, system_prompt=NESTED_PROMPT if args.nested else SYSTEM_PROMPT), indent=2))
