from collections import Counter
from pathlib import Path

import pytest

from polyquery import Engine
from polyquery.composition_evaluation import (
    CATEGORIES, IndependentReference, _json_digest, _validate_partitions, reference_answer, run,
)
from polyquery.evaluation_v2 import _read


pytestmark = pytest.mark.evaluation
BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"


def test_admitted_manifests_have_disjoint_families_and_topologies():
    partitions = {
        "validation": _read(BENCHMARKS / "composition-validation.json"),
        "test": _read(BENCHMARKS / "composition-test-sealed.json"),
    }
    _validate_partitions(partitions, _read(BENCHMARKS / "evaluation-v2.json"))
    admission = _read(BENCHMARKS / "composition-admission.json")
    for split, cases in partitions.items():
        assert Counter(case["category"] for case in cases) == dict.fromkeys(CATEGORIES, 2)
        assert _json_digest(cases) == admission["manifests"][split]


def test_reference_executes_three_backend_branch_against_independent_answers():
    cases = _read(BENCHMARKS / "composition-validation.json")
    case = next(case for case in cases if case["family"] == "shared-manager-direct-and-deep-pay")
    with Engine() as engine:
        specs, answers = reference_answer(case, IndependentReference("synthetic", {}, set()), engine)
    assert case["complexity"] == {"backends": 3, "nodes": 5, "dependency_depth": 2, "branching_factor": 2}
    assert [spec["args"]["employees"] for spec in specs] == [["Bob", "Carol"], ["Bob", "Carol", "Dan"]]
    assert answers == [95000, 100000]


def test_test_partition_is_not_loaded_without_frozen_selection(monkeypatch):
    def read_admission(path):
        assert path.name == "composition-admission.json"
        return {"manifests": {}}

    monkeypatch.setattr("polyquery.composition_evaluation._read", read_admission)
    with pytest.raises(ValueError, match="frozen live validation selection"):
        run(BENCHMARKS, BENCHMARKS / "router-sources.json", split="test", ollama=True)
