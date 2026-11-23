from pathlib import Path

import pytest

from benchmarks.intelligent_routing import read
from benchmarks.verifier_reliability import cases, review
from polyquery import Engine
from polyquery.evaluation import equivalent
from test_llm_dag import fake_chat

ROOT = Path(__file__).resolve().parents[1] / "benchmarks"


def test_original_ordered_false_positive_executes_with_independent_gold():
    fixture = read(ROOT / "ordered-verifier-regression.json")
    with Engine(source="chinook", data_path=ROOT/"Chinook_Sqlite.sqlite", planner_kind="llm-dag",
                model="offline", llm_api="chat", llm_client=fake_chat(fixture["plan"])) as engine:
        engine.dag_planner.enable_typed_bindings()
        answer = engine.query(fixture["question"])
        assert answer["status"] == "ok"
        assert equivalent(answer["result"]["value"], fixture["gold"])
        assert answer["planner_metadata"]["plan_diagnostics"]["semantic_evidence"] == "verified_match"


@pytest.mark.parametrize("case,source,question,payload,expected", list(cases()),
                         ids=[item[0] for item in cases()])
def test_reviewed_requirements_are_verified_independently(case, source, question, payload, expected):
    config = {"data_path": ROOT/"Chinook_Sqlite.sqlite"} if source == "chinook" else {}
    with Engine(source=source, **config, planner_kind="llm-dag", model="offline", llm_client=object()) as engine:
        planner = engine.dag_planner
        planner.enable_typed_bindings()
        accepted, evidence = review(planner, question, payload)
    assert accepted is expected
    if expected:
        assert evidence == "verified_match"
    elif case in {"count-not-sum", "wrong-row", "wrong-output-order", "omitted-order-edge"}:
        assert evidence == "verified_mismatch"
