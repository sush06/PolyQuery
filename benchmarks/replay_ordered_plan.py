"""Replay the recorded ordered-plan false positive without consulting a model."""
from pathlib import Path

from benchmarks.intelligent_routing import read, write
from polyquery import Engine


def replay(record):
    case = next(row for row in record["rows"] if row["id"] == "test-01")
    proposal = case["recorded_payload_snapshot"]
    nodes = [{"id": f"node_{i}", **node, "depends_on": [f"node_{i-1}"] if i > 1 else []}
             for i, node in enumerate(proposal["nodes"], 1)]
    payload = {"kind": proposal["kind"], "nodes": nodes, "outputs": proposal["outputs"]}
    db = Path("benchmarks/Chinook_Sqlite.sqlite")
    with Engine(source="chinook", data_path=db, planner_kind="llm-dag", model="offline",
                llm_client=object(), llm_api="chat") as engine:
        planner = engine.dag_planner
        planner.enable_typed_bindings()
        exact = case["question"]
        control = "Report in order: average invoice total, followed by how many customers?"
        result = {"case": case["id"], "provider_calls": 0,
                  "scope": "Replayed ordered operations and canonical dependency from the redacted trace."}
        for label, question in (("recorded_wording", exact), ("canonical_control_wording", control)):
            planner._reset_metadata()
            decision = planner._decision(question, payload)
            diagnostic = planner.last_metadata["plan_diagnostics"].get("final_validation_error_category")
            row = {"kind": decision.kind, "reason": diagnostic}
            if decision.kind == "dag_plan":
                values = engine.execute_dag(decision.nodes)
                row["matches_independent_gold"] = [
                    values[key]["value"] for key in decision.outputs] == case["gold"]
            result[label] = row
    return result


if __name__ == "__main__":
    result = replay(read(Path("logs/intelligent-routing-failure-audit.json")))
    write(Path("logs/planner-model-ordered-replay.json"), result)
    print(result)
