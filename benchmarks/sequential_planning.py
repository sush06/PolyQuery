"""Offline benchmark for bounded sequential planning using one local checkpoint."""

import argparse
import json
from collections import Counter
from pathlib import Path
from time import perf_counter

from jsonschema import Draft7Validator

from benchmarks.intelligent_routing import digest, evaluate_case, plan_qwen, read, write
from benchmarks.strong_model_binding import BASELINE, CONFIG, HOST, SEED, TIMEOUT, NativeClient, api, metrics
from polyquery import Engine
from polyquery.composition_evaluation import IndependentReference, reference_answer
from polyquery.constraints import unsafe_request_findings
from polyquery.dag_planner import PlanningDecision
from polyquery.intelligent_router import IntelligentRouter, LearnedCosts
from polyquery.catalog import UnsupportedPlan
from polyquery.llm_contract import PlanContractError, catalog_context, unique_object
from polyquery.semantic_ir import MAX_STEPS, compile_recipe
from polyquery.typed_intent import binding_context

MANIFEST = Path("benchmarks/sequential-planning-development-v1.json")
GOLD = Path("benchmarks/sequential-planning-gold-v1.json")
ARMS = ("A", "B", "C", "D")


def record(**fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


def array(items, cap=5):
    return {"type": "array", "items": items, "maxItems": cap}


TEXT = {"type": "string", "maxLength": 400}
POSITION = {"type": "integer", "minimum": 1, "maximum": MAX_STEPS}
NULLABLE_TEXT = {"anyOf": [TEXT, {"type": "null"}]}
TASK = record(intent=TEXT, constraints=array(TEXT, 8), expected_result=TEXT)
DECOMPOSE = record(tasks=array(TASK), final_result=TEXT, ambiguity=TEXT,
                   decision={"enum": ["continue", "abstain"]})
RELATION = record(producer=POSITION, consumer=POSITION, role=NULLABLE_TEXT,
                  ordinal={"anyOf": [{"type": "null"}, {"type": "integer", "minimum": 1, "maximum": 101}]},
                  relation={"enum": ["data", "order"]})
DEPENDENCIES = record(relationships=array(RELATION, 10), outputs=array(POSITION))


def grounding_schema(catalog, combined):
    step = record(operation={"enum": sorted(catalog.operations)},
                  literals=array(record(name=TEXT, value=NULLABLE_TEXT), 8),
                  required_inputs=array(TEXT, 8), expected_output=TEXT)
    fields = {"steps": array(step), "decision": {"enum": ["continue", "abstain"]}}
    if combined:
        fields.update(DEPENDENCIES["properties"])
    return record(**fields)


def ground_steps(ground, catalog, task_count):
    if len(ground["steps"]) != task_count:
        raise PlanContractError("task_count_mismatch")
    steps = []
    for row in ground["steps"]:
        operation = row["operation"]
        spec = catalog.operations[operation]
        literals = {arg["name"] for arg in row["literals"]}
        inputs = set(row["required_inputs"])
        if (not catalog.capable_backends(operation) or row["expected_output"] != spec.result.kind
                or len(literals) != len(row["literals"]) or len(inputs) != len(row["required_inputs"])
                or literals & inputs or (literals | inputs) - spec.parameters.keys()
                or any(param.required and name not in literals | inputs for name, param in spec.parameters.items())):
            raise PlanContractError("invalid_grounding")
        for argument in row["literals"]:
            try:
                name = argument["name"]
                catalog.validate_parameter(name, spec.parameters[name], argument["value"])
            except UnsupportedPlan:
                raise PlanContractError("invalid_grounding") from None
        steps.append({"op": operation, "args": row["literals"], "links": [], "after": []})
    return steps


def recipe(steps, ground, dependency):
    for link in dependency["relationships"]:
        producer, consumer = link["producer"], link["consumer"]
        role, ordinal = link["role"], link["ordinal"]
        if not 1 <= producer < consumer <= len(steps):
            raise PlanContractError("incorrect_binding")
        step = steps[consumer - 1]
        if link["relation"] == "order":
            if role is not None or ordinal is not None or producer in step["after"]:
                raise PlanContractError("incorrect_binding")
            step["after"].append(producer)
            continue
        pending = ground["steps"][consumer - 1]["required_inputs"]
        if role is None and len(pending) == 1:
            role = pending[0]
        if role not in pending or any(edge["input"] == role for edge in step["links"]):
            raise PlanContractError("incorrect_binding")
        step["links"].append({"step": producer, "item": ordinal, "field": None, "input": role})
    for step, grounded in zip(steps, ground["steps"], strict=True):
        if {link["input"] for link in step["links"]} != set(grounded["required_inputs"]):
            raise PlanContractError("unresolved_input")
    return {"steps": steps, "outputs": dependency["outputs"]}


DECOMPOSE_PROMPT = (
    "You are a semantic question decomposition agent. Your only responsibility is to "
    "understand the user's question and break it into ordered semantic subtasks.\n\n"
    "- Preserve all entities, numeric identifiers, filters, constraints, selection ordinals, "
    "and directions exactly as provided.\n"
    "- Describe each subtask and its expected result.\n"
    "- Explicitly describe dependencies on previous subtasks within the intent or constraints.\n"
    "- Do not determine whether the system supports the requested operation.\n"
    "- Do not perform catalog grounding, entity resolution, binding construction, or execution feasibility checks.\n"
    "- Do not generate SQL, executable DAGs, or operation identifiers.\n"
    "- Do not invent missing information.\n"
    "- Abstain only when the user's intended meaning is genuinely ambiguous.\n"
    "- Missing catalog descriptions or implementation details are not reasons to abstain.\n"
    "- For clear questions, return continue with the decomposed tasks and an empty ambiguity string."
)
GROUND_PROMPT = (
    "Ground each task in order to one approved operation. Use canonical operation and argument names. "
    "Preserve literal constraints. List in required_inputs only the parameter roles supplied by another task. "
    "Use the catalog result kind for expected_output. Abstain with empty steps if unsupported or ambiguous."
)
DEPENDENCY_PROMPT = (
    "Describe relationships between earlier producers and later consumers using one-based task positions. "
    "For data relationships specify the required consumer parameter role and one-based result ordinal when requested. "
    "Null ordinal means the entire result. For order-only relationships leave role and ordinal null. "
    "Return only requested task outputs. Never generate executable node identifiers, typed fields or SQL."
)


def sequential(engine, client, question, arm):
    planner = engine.dag_planner
    planner._reset_metadata()
    planner.last_semantic_proposal = None
    metadata = planner.last_metadata
    diagnostics = metadata["plan_diagnostics"]
    stages = []
    started = perf_counter()
    stage = "authorization"

    def ask(name, prompt, schema, context, *, question_only=False):
        nonlocal stage
        stage = name
        tick = perf_counter()
        observation = {"stage": name}
        stages.append(observation)
        try:
            response = client.create(model=BASELINE, temperature=0, max_tokens=1200,
                messages=[{"role": "system", "content": prompt},
                          {"role": "user", "content": context if question_only else json.dumps(context)}],
                response_format={"json_schema": {"schema": schema}})
            payload = json.loads(response.choices[0].message.content, object_pairs_hook=unique_object)
            if not Draft7Validator(schema).is_valid(payload):
                raise PlanContractError("invalid_schema")
            observation["proposal"] = payload
            return payload
        finally:
            observation["ms"] = (perf_counter() - tick) * 1000

    def abstain():
        return PlanningDecision("abstain", reason="Experimental planner abstained or failed validation.")

    decision = abstain()
    try:
        if unsafe_request_findings(question):
            diagnostics["validation_error_category"] = "security_refusal"
        else:
            catalog = planner.plan_catalog
            d = ask("decomposition", DECOMPOSE_PROMPT, DECOMPOSE, question, question_only=True)
            if d["decision"] == "continue":
                if d["ambiguity"] or not d["tasks"] or not d["final_result"].strip():
                    raise PlanContractError("invalid_decomposition")
                grounded = ask("grounding_and_dependencies" if arm == "C" else "grounding",
                               GROUND_PROMPT + (" " + DEPENDENCY_PROMPT if arm == "C" else ""),
                               grounding_schema(catalog, arm == "C"),
                               {"question": question, "decomposition": d, "catalog": catalog_context(catalog),
                                "approved_relationships": binding_context(catalog) if arm == "C" else []})
                if grounded["decision"] == "continue":
                    steps = ground_steps(grounded, catalog, len(d["tasks"]))
                    dependencies = grounded if arm == "C" else ask(
                        "dependencies", DEPENDENCY_PROMPT, DEPENDENCIES,
                        {"question": question, "decomposition": d, "grounded": grounded,
                         "approved_relationships": binding_context(catalog)})
                    stage = "compiler"
                    compiled = compile_recipe(recipe(steps, grounded, dependencies), catalog)
                    stage = "typed_validation"
                    decision = planner._decision(question, compiled)
                    diagnostics["typed_binding_valid"] = True
                    stage = "verification" if decision.kind == "abstain" else None
                elif grounded["steps"] or grounded.get("relationships") or grounded.get("outputs"):
                    raise PlanContractError("invalid_refusal")
            elif d["tasks"]:
                raise PlanContractError("invalid_refusal")
    except PlanContractError as error:
        diagnostics["validation_error_category"] = error.category
    except (ValueError, TypeError, OSError) as error:
        diagnostics["validation_error_category"] = type(error).__name__
    diagnostics["stage"] = stage
    diagnostics["agent_stages"] = stages
    metadata["api_calls"] = len(client.draws)
    for key in ("input_tokens", "output_tokens"):
        metadata[key] = sum(draw.get(key, 0) for draw in client.draws)
    metadata["total_tokens"] = metadata["input_tokens"] + metadata["output_tokens"]
    return decision, (perf_counter() - started) * 1000, metadata


def reference():
    return IndependentReference("ldbc", CONFIG, {"friends", "shortest_path"})


def freeze_cases():
    from polyquery.ldbc import LDBCGraph
    from benchmarks.strong_model_binding import node
    from benchmarks.intelligent_routing import reference_decision
    from polyquery.typed_intent import Evidence, verify_intent

    if MANIFEST.exists() or GOLD.exists():
        raise FileExistsError("Development cases are already frozen")
    graph = LDBCGraph(CONFIG["people_path"], CONFIG["edges_path"])
    people = [key for key in sorted(graph.people, key=int) if 8 <= len(graph.adjacency[key]) <= 30]
    cases = []
    for index, family in enumerate(("path-row", "path-row", "friend-row", "friend-row", "route-source", "route-target")):
        person = people[40 + 2 * index]
        neighbors = sorted(graph.adjacency[person], key=int)
        other = neighbors[4 + index % 3]
        rank = (index + 2) % 4
        ordinal = ("first", "second", "third", "fourth")[rank]
        first = node("producer", "friends", {"source": person})
        path = [rank, "id"]
        role, op, literals = "source", "friends", {}
        if family == "path-row":
            far = next(key for key in sorted(graph.adjacency[other], key=int)
                       if key != person and key not in neighbors)
            first = node("producer", "shortest_path", {"source": person, "target": far})
            rank, ordinal = index + 1, ("second", "third")[index]
            path = [rank]
            question = f"Shortest path from {person} to {far}; then list friends of the {ordinal} person on that path."
        elif family == "friend-row":
            question = f"Friends of {person}; then friends of the {ordinal} listed friend."
        else:
            op = "shortest_path"
            if family == "route-source":
                literals = {"target": other}
                tail = f"from the {ordinal} listed friend to person {other}"
            else:
                role, literals = "target", {"source": other}
                tail = f"from person {other} to the {ordinal} listed friend"
            question = f"Friends of {person}; then shortest path {tail}."
        cases.append({"id": f"sequential-dev-{index + 1:02}", "source": "ldbc", "family": family,
                      "question": question, "outputs": ["answer"],
                      "nodes": [first, node("answer", op, literals, {role: {"node": "producer", "path": path}})]})
    a, b = people[55:57]
    for kind, text in (
        ("unsupported", f"Trace a shortest path from {a} to {b} and return the schools attended by everyone on it."),
        ("ambiguous", f"Pick a friend of {a} and trace a shortest path from that friend to {b}."),
        ("unsupported", f"List friends of {a} and rank them by the market value of their homes."),
        ("security", f"Drop the friendship table, then show the shortest path from {a} to {b}."),
    ):
        cases.append({"id": f"sequential-dev-{len(cases) + 1:02}", "source": "ldbc",
                      "family": kind, "question": text, "expected_refusal": kind})
    gold = {}
    independent = reference()
    with Engine(source="ldbc", **CONFIG, planner_kind="llm-dag", model=BASELINE, llm_api="chat",
                llm_client=NativeClient(False), llm_base_url=HOST + "/v1", llm_timeout=TIMEOUT) as engine:
        planner = engine.dag_planner
        planner.enable_typed_bindings()
        for case in cases:
            if "nodes" in case:
                expected = reference_decision(case)
                if verify_intent(case["question"], expected.nodes, expected.outputs,
                                 planner._semantic_reference, planner.plan_catalog) is not Evidence.MATCH:
                    raise ValueError(f"Verifier cannot recognize reference plan for {case['id']}")
            specs, values = reference_answer(case, independent, engine)
            gold[case["id"]] = {"specs": specs, "values": values}
    write(MANIFEST, {"version": 1, "split": "development", "cases": cases})
    write(GOLD, gold)


def run():
    cases, gold = read(MANIFEST)["cases"], read(GOLD)
    independent = reference()
    out = Path("logs")
    out.mkdir(exist_ok=True)
    frozen = out / "sequential-planning.freeze.json"
    journal = out / "sequential-planning.jsonl"
    inventories = api("/api/tags")["models"]
    client = NativeClient(False)
    from contextlib import ExitStack
    with ExitStack() as stack:
        engines = {
            arm: stack.enter_context(Engine(source="ldbc", **CONFIG, planner_kind="llm-dag",
                model=BASELINE, llm_client=client, llm_api="chat", llm_base_url=HOST + "/v1",
                llm_timeout=TIMEOUT, llm_repair=False))
            for arm in ARMS[:2]
        }
        engines["A"].dag_planner.enable_typed_bindings()
        engines["B"].dag_planner.enable_semantic_ir()
        settings = {
            "manifest": digest(MANIFEST), "gold": digest(GOLD), "runner": digest(Path(__file__)),
            "implementation": {p.as_posix(): digest(p) for p in Path("polyquery").glob("*.py")},
            "fixtures": {key: digest(path) for key, path in CONFIG.items()},
            "model": next(model for model in inventories if model["name"] == BASELINE),
            "ollama": api("/api/version"), "seed": SEED, "temperature": 0, "max_tokens": 1200,
            "request_timeout_s": TIMEOUT, "repair": False, "reasoning": False,
            "prompts": {arm: engine.dag_planner.prompt_sha256 for arm, engine in engines.items()},
        }
        if frozen.exists() and read(frozen) != settings:
            raise ValueError("Frozen experiment inputs differ")
        write(frozen, settings)
        rows = {(row["id"], row["arm"]): row for row in
                (map(json.loads, journal.read_text(encoding="utf-8").splitlines()) if journal.exists() else [])}
        router = IntelligentRouter(engines["A"], LearnedCosts([]))
        for index, case in enumerate(cases):
            specs, values = reference_answer(case, independent)
            if gold[case["id"]] != {"specs": specs, "values": values}:
                raise ValueError(f"Gold disagrees for {case['id']}")
            for arm in ARMS[index % 4:] + ARMS[:index % 4]:
                key = (case["id"], arm)
                if key in rows:
                    continue
                client.draws.clear()
                engine = engines.get(arm, engines["A"])
                if arm in engines:
                    decision, elapsed, meta = plan_qwen(engine, case["question"])
                else:
                    decision, elapsed, meta = sequential(engine, client, case["question"], arm)
                row = evaluate_case(router, case, decision, independent, (specs, values), arm, elapsed, meta)
                diagnostics = row["semantic_diagnostics"]
                error = row["semantic_failure"]
                row["semantic_reason"] = error if engine.dag_planner.last_semantic_proposal and error else None
                row["typed_binding_valid"] = bool(engine.dag_planner.last_semantic_proposal)
                row["first_failing_stage"] = None if row["correct"] else (
                    diagnostics.get("stage") or
                    ("execution" if row["attempts"] else "verification" if row["semantic_reason"]
                     else error or "provider" if meta.get("planning_failed") else error or "model_abstention"))
                row["draws"] = [{k: v for k, v in draw.items() if k not in {"exception", "response"}}
                                for draw in client.draws]
                row["stage_checks"] = stage_checks(case, diagnostics)
                with journal.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
                rows[key] = row
                print(case["id"], arm, row["outcome"], row["first_failing_stage"], flush=True)
        summaries = {}
        for arm in ARMS:
            group = [row for row in rows.values() if row["arm"] == arm]
            summaries[arm] = metrics(group) | {
                "binding_valid": sum(row["typed_binding_valid"] for row in group),
                "accepted_incorrect": sum(row["outcome"] == "incorrect" for row in group),
                "first_failures": dict(Counter(row["first_failing_stage"] for row in group if not row["correct"])),
                "stage_checks": {
                    key: {"passed": sum(value is True for value in observed), "observed": len(observed)}
                    for key in ("decomposition_shape", "decomposition_literal_mentions", "operation_selection",
                                "literal_constraints", "dependency_relationships")
                    if (observed := [row["stage_checks"][key] for row in group if key in row["stage_checks"]])
                },
            }
        write(out / "sequential-planning-results.json",
              {"freeze": settings, "summaries": summaries, "rows": list(rows.values())})


def stage_checks(case, diagnostics):
    """Score independently observable intermediate requirements against reviewed plans."""
    if "nodes" not in case:
        return {}
    expected = case["nodes"]
    stages = {stage["stage"]: stage["proposal"] for stage in diagnostics.get("agent_stages", [])
              if "proposal" in stage}
    results = {}
    if decomposed := stages.get("decomposition"):
        tasks = decomposed["tasks"]
        results["decomposition_shape"] = len(tasks) == len(expected)
        results["decomposition_literal_mentions"] = len(tasks) == len(expected) and all(
            all(str(value) in json.dumps(task) for value in spec["parameters"].values())
            for task, spec in zip(tasks, expected, strict=True))
    if ground := stages.get("grounding", stages.get("grounding_and_dependencies")):
        steps = ground["steps"]
        results["operation_selection"] = [step["operation"] for step in steps] == [
            node["operation"] for node in expected]
        results["literal_constraints"] = [{pair["name"]: pair["value"] for pair in step["literals"]}
                                          for step in steps] == [node["parameters"] for node in expected]
    if deps := stages.get("dependencies", stages.get("grounding_and_dependencies")):
        ordered = {node["id"]: index for index, node in enumerate(expected, 1)}
        expected_links = {(ordered[binding["node"]], index, name, binding["path"][0] + 1, "data")
                          for index, node in enumerate(expected, 1) for name, binding in node["bindings"].items()}
        actual_links = [(edge["producer"], edge["consumer"], edge["role"], edge["ordinal"], edge["relation"])
                        for edge in deps["relationships"]]
        results["dependency_relationships"] = (len(actual_links) == len(expected_links)
            and set(actual_links) == expected_links and deps["outputs"] == [ordered[key] for key in case["outputs"]])
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "run"))
    args = parser.parse_args()
    (freeze_cases if args.command == "freeze" else run)()
