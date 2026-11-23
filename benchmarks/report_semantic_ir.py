"""Summarize the three-arm development experiment and frozen IR held-out run."""
from collections import Counter
from pathlib import Path

from benchmarks.intelligent_routing import digest, read, write
from benchmarks.report_typed_bindings import heldout

LOG = Path("logs")


def main():
    dev = read(LOG / "semantic-ir-development.json")
    sealed = read(LOG / "intelligent-routing-semantic-ir-heldout.json")
    typed = read(LOG / "intelligent-routing-typed-heldout.json")
    if (sealed["manifest_sha256"] != typed["manifest_sha256"]
            or sealed["model"]["digest"] != typed["model"]["digest"]
            or sealed["model"]["digest"] != dev["model"]["digest"]):
        raise ValueError("Expected matching held-out manifest and model digest")
    transitions = {}
    for row in dev["rows"]:
        transitions.setdefault(row["id"], {})[row["arm"]] = {
            "outcome": row["outcome"], "failure": row["semantic_failure"],
            "compiled": "compiled_plan" in row["details"], "plan_match": row["semantic_plan_match"],
        }
    write(Path("benchmarks/semantic-ir-summary.json"), {
        "model": sealed["model"], "heldout_manifest_sha256": sealed["manifest_sha256"],
        "development_manifest_sha256": digest(Path("benchmarks/semantic-ir-development.json")),
        "development": dev["summary"], "development_transitions": transitions,
        "development_failure_taxonomy": {
            arm: dict(Counter(row["semantic_failure"] for row in dev["rows"]
                              if row["arm"] == arm and row["semantic_failure"]))
            for arm in dev["summary"]
        },
        "direct_typed_heldout": heldout(typed), "semantic_ir_heldout": heldout(sealed),
    })


if __name__ == "__main__":
    main()
