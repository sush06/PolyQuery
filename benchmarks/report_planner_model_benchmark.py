"""Publish aggregate metrics and paired development outcomes without proposal literals."""
from pathlib import Path

from benchmarks.intelligent_routing import read, write


def compact(report, replay):
    development = report["development"]
    arms = [*development, report["controlled_decoding"]]
    canonical = {(row["id"], row["source"]): (row["prompt_sha256"], row["schema_sha256"])
                 for row in development[0]["rows"]}
    for arm in arms[1:]:
        comparison = {(row["id"], row["source"]): (row["prompt_sha256"], row["schema_sha256"])
                      for row in arm["rows"]}
        if comparison != canonical or arm["dataset_sha256"] != development[0]["dataset_sha256"]:
            raise ValueError("Development comparisons require matched prompts, schemas, and fixtures")
    transitions = {}
    for arm in arms:
        label = arm["model"] + ("/native" if arm["native"] else "/compatible")
        for row in arm["rows"]:
            transitions.setdefault(row["id"], {})[label] = {
                key: row[key] for key in ("outcome", "schema_valid", "catalog_valid", "binding_valid",
                                          "semantically_accepted", "failure_category")}
    model_names = {arm["model"] for arm in arms}
    return {"models": {model: report["inventory"][model] for model in sorted(model_names)},
            "ollama": report["ollama"], "hardware": report["hardware"], "settings": report["settings"],
            "manifest_sha256": report["manifest_sha256"], "unavailable": report["unavailable"],
            "development": [{"model": arm["model"], "native": arm["native"], "summary": arm["summary"]}
                            for arm in arms],
            "development_transitions": transitions, "ordered_verifier_replay": replay,
            "frozen": report["frozen"], "final": report["final"]["summary"]}


if __name__ == "__main__":
    output = compact(read(Path("logs/planner-model-benchmark.json")),
                     read(Path("logs/planner-model-ordered-replay.json")))
    write(Path("benchmarks/planner-model-summary.json"), output)
