"""Publish the typed-binding development and held-out comparison."""
from collections import Counter
from pathlib import Path

from benchmarks.intelligent_routing import digest, read, write

LOG = Path("logs")


def heldout(report):
    rows = [r for r in report["rows"] if r["arm"] == "qwen_learned"]
    return {
        "summary": {name: report["summary"][name]
                    for name in ("qwen_fixed", "qwen_learned", "qwen_oracle")},
        "accepted": sum(row["decision"]["kind"] != "abstain" for row in rows),
        "correct_answers": sum(row["outcome"] == "correct" for row in rows),
        "shared_model_calls": sum(row["tokens"]["api_calls"] or 0 for row in rows),
        "verification_diagnostics": dict(Counter(row["semantic_failure"] for row in rows
                                                  if row["semantic_failure"])),
        "reference_plan_control": report["reference_plan_control"]["routing"],
        "routing": report["routing"],
        "possible_false_positive_literals_match": next(
            (r.get("proposal_operations_and_literals_match") for r in rows if r["id"] == "test-04"), None),
    }


def main():
    before = read(LOG / "intelligent-routing-after-audit.json")
    after = read(LOG / "intelligent-routing-typed-heldout.json")
    dev = read(LOG / "typed-bindings-development.json")
    if before["manifest_sha256"] != after["manifest_sha256"] or before["model"]["digest"] != after["model"]["digest"]:
        raise ValueError("Held-out comparisons require the frozen manifest and model")
    write(Path("benchmarks/typed-bindings-summary.json"), {
        "model": after["model"], "manifest_sha256": after["manifest_sha256"],
        "development_manifest_sha256": digest(Path("benchmarks/typed-bindings-development.json")),
        "development": dev["summary"], "negative_controls": dev["negative_controls"],
        "legacy_heldout": heldout(before), "typed_heldout": heldout(after),
    })


if __name__ == "__main__":
    main()
