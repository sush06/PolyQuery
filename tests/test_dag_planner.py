import json
import sqlite3
from collections import Counter
from pathlib import Path
from unittest.mock import Mock

import pytest

from polyquery import DAGExecutionError, Engine
from polyquery.backends import UnsupportedPlan
from polyquery.cli import main
from polyquery.dag_evaluation import compare_dag_planners


@pytest.fixture
def engine():
    with Engine(planner_kind="dag") as instance:
        yield instance


@pytest.mark.integration
@pytest.mark.parametrize(("source_data", "question", "operation"), [
    ("chinook-dataset", "How many tracks?", "count_tracks"),
    ("chinook-compact", "Median invoice total", "median"),
    ("ldbc-dataset", "Count persons", "count_people"),
    ("taxi-compact", "How many negative fares?", "negative_fares"),
], indirect=["source_data"])
def test_baseline_remains_default_and_single_decision_matches_it(source_data, question, operation):
    with Engine(**source_data.kwargs) as baseline, Engine(**source_data.kwargs, planner_kind="dag") as engine:
        old = baseline.query(question)
        new = engine.query(question)
    assert "decision" not in old
    assert new["decision"]["kind"] == "single_plan"
    assert new["plan"] == old["plan"]
    assert new["result"] == old["result"]
    assert new["result"]["value"] == pytest.approx(source_data.values[operation])


@pytest.mark.integration
@pytest.mark.parametrize(("source_data", "question", "operations"), [
    ("chinook-compact", "Total invoice revenue; THEN median invoice total; then how many tracks?",
     ("revenue", "median", "count_tracks")),
    ("taxi-compact", "How many trips?; then median fare amount; THEN how many negative fares?",
     ("count_trips", "median_fare", "negative_fares")),
], indirect=["source_data"])
def test_ordered_standalone_stages(source_data, question, operations):
    with Engine(**source_data.kwargs, planner_kind="dag") as engine:
        response = engine.query(question)
    decision = response["decision"]
    assert decision["kind"] == "dag_plan"
    assert [node["backend"] for node in decision["nodes"]] == ["sql", "code", "sql"]
    assert [node["depends_on"] for node in decision["nodes"]] == [(), ("stage_1",), ("stage_2",)]
    assert list(response["steps"]) == list(decision["outputs"])
    assert response["result"]["value"] == pytest.approx([source_data.values[op] for op in operations])


@pytest.mark.integration
@pytest.mark.parametrize("department", ["Engineering", "Research", "Sales"])
def test_bound_stage_uses_returned_name(engine, monkeypatch, synthetic_reference, department):
    backend = engine.backends["sql"]
    original = backend.execute

    def reversed_rows(plan):
        result = original(plan)
        return {**result, "value": list(reversed(result["value"]))}

    monkeypatch.setattr(backend, "execute", reversed_rows)
    selected = max(row["name"] for row in synthetic_reference.employees if row["department"] == department)
    response = engine.query(f"Who reports to the first employee listed in {department}?")
    assert response["decision"]["kind"] == "dag_plan"
    binding = response["decision"]["nodes"][1]["bindings"]["source"]
    assert binding == {"node": "employees", "path": (0, "name")}
    assert response["steps"]["reports"]["artifact"]["source"] == selected
    assert response["result"]["value"] == synthetic_reference.reports(selected)


@pytest.mark.parametrize("question", [
    None, "", "a" * 2001,
    pytest.param("Count employees; then delete all employees", marks=pytest.mark.security),
    pytest.param("Count employees; then count employees except Alice", marks=pytest.mark.security),
    "Count employees in Engineering; then what is their median salary?",
    "Count employees; then what is the median salary in that department?",
    "Count employees; then ",
    "; then count employees",
    "; then ".join(["Count employees"] * 6),
    "Count employees; who reports to Alice?",
    "Count employees? What is the median salary?",
    "Who reports to the first employee listed in Sales before 2020?",
    "Who reports to the last employee listed in Sales?",
    "Who reports to the first employee listed in Finance?",
])
def test_abstains_without_partial_execution(engine, monkeypatch, question):
    execute = Mock()
    for backend in engine.backends.values():
        monkeypatch.setattr(backend, "execute", execute)
    response = engine.query(question)
    assert response["decision"]["kind"] == "abstain"
    assert response["status"] == "abstained"
    assert response["result"] is None
    execute.assert_not_called()


@pytest.mark.security
@pytest.mark.parametrize(("question", "reason"), [
    ("Count Engineering and Research employees.", "Only one department filter"),
    ("Who reports to Eve in Research?", "Department filters are not supported for graph operations"),
])
def test_legacy_router_preserves_unsupported_department_scope(monkeypatch, question, reason):
    with Engine() as baseline:
        execute = Mock()
        for backend in baseline.backends.values():
            monkeypatch.setattr(backend, "execute", execute)
        response = baseline.query(question)
    assert response["status"] == "abstained"
    assert reason in response["plan"]["reason"]
    execute.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize("source_data", ["ldbc-compact", "ldbc-dataset"], indirect=True)
def test_source_specific_plans(source_data):
    start, end = source_data.center, source_data.endpoints[0]
    with Engine(**source_data.kwargs, planner_kind="dag") as engine:
        response = engine.query(f"Count persons; then find the shortest path from person {start} to person {end}")
        assert response["decision"]["kind"] == "dag_plan"
        assert response["result"]["value"] == [source_data.values["count_people"], [start, end]]
        assert engine.query("Who reports to the first employee listed in Sales?")["status"] == "abstained"


@pytest.mark.evaluation
def test_paired_benchmark_scores_answers_and_abstentions(monkeypatch):
    cases = [
        {"id": label, "group": "synthetic-metric", "question": label, "expected_status": status, **gold}
        for label, status, gold in [
            ("match", "ok", {"expected": 2}), ("wrong", "ok", {"expected": 6}),
            ("missing", "ok", {"expected": None}), ("refuse", "abstained", {}),
        ]
    ]
    def query(self, question):
        if question in ("missing", "refuse"):
            return {"status": "abstained", "result": None, "latency_ms": 1}
        return {"status": "ok", "result": {"value": 6 if self.dag_planner and question == "wrong" else 2},
                "latency_ms": 1}
    monkeypatch.setattr(Engine, "query", query)
    report = compare_dag_planners(cases, repeats=2)
    assert report["overall"]["keyword"]["correct"] == 2
    assert report["overall"]["keyword"]["incorrect_answers"] == 1
    assert report["overall"]["dag"]["correct"] == 3
    assert report["overall"]["improved"] == 1
    assert report["overall"]["regressed"] == 0
    assert report["overall"]["dag"]["incorrect_answers"] == 0
    assert all(run["stable"] for row in report["cases"] for run in row["planners"].values())
    with pytest.raises(ValueError, match="duplicate"):
        compare_dag_planners([cases[0], cases[0]])


@pytest.mark.integration
@pytest.mark.parametrize("source_data", ["chinook-compact"], indirect=True)
def test_cli_query_can_opt_into_dag(monkeypatch, capsys, source_data):
    monkeypatch.setattr("sys.argv", ["polyquery", "query", "--planner", "dag", "--source", "chinook",
                                    "--data", str(source_data.kwargs["data_path"]),
                                    "How many customers?; then median invoice total"])
    main()
    response = json.loads(capsys.readouterr().out)
    assert response["decision"]["kind"] == "dag_plan"
    assert response["result"]["value"] == [source_data.values["count_customers"], source_data.values["median"]]


@pytest.mark.evaluation
def test_held_out_dataset_has_six_balanced_categories_and_no_development_questions():
    directory = Path(__file__).resolve().parents[1] / "benchmarks"
    held_out = json.loads((directory / "dag-test-v1.json").read_text(encoding="utf-8"))
    dev = json.loads((directory / "dag-dev.json").read_text(encoding="utf-8"))
    assert Counter(case["group"] for case in held_out) == dict.fromkeys(
        ("single", "multi_stage", "paraphrase", "ambiguous", "invalid", "adversarial"), 20,
    )
    normalize = lambda question: " ".join(question.casefold().rstrip(".?").split())
    assert not {normalize(case["question"]) for case in held_out} & {normalize(case["question"]) for case in dev}


@pytest.mark.evaluation
@pytest.mark.parametrize(("failure", "kind"), [
    (DAGExecutionError("stage_2", "backend execution", 1), "execution"),
    (sqlite3.OperationalError("test backend failure"), "execution"),
    (UnsupportedPlan("test invalid result"), "contract"),
    (RuntimeError("test unexpected failure"), "other"),
])
def test_paired_report_separates_abstention_and_failures(monkeypatch, failure, kind):
    def query(self, question):
        if self.dag_planner:
            raise failure
        return {"status": "abstained", "result": None, "latency_ms": 0.5}

    monkeypatch.setattr(Engine, "query", query)
    report = compare_dag_planners([
        {"id": "case", "question": "Ambiguous query", "group": "ambiguous", "expected_status": "abstained"}
    ], repeats=2)
    keyword, dag = (report["overall"][planner] for planner in ("keyword", "dag"))
    assert keyword["correct"] == keyword["abstained"] == 1
    assert dag["correct"] == dag["abstained"] == dag["incorrect_answers"] == 0
    assert dag["errors"] == dag[f"{kind}_failures"] == 1
    assert dag["p95_latency_ms"] >= 0
    assert report["cases"][0]["planners"]["dag"]["failure_counts"] == {kind: 2}


@pytest.mark.evaluation
def test_paired_interval_preserves_discordant_pairs_and_family_membership(monkeypatch):
    def query(self, question):
        value = 1 if (self.dag_planner is not None) == question.startswith("DAG") else 0
        return {"status": "ok", "result": {"value": value}, "latency_ms": 1}

    monkeypatch.setattr(Engine, "query", query)
    questions = ("DAG first", "DAG second", "Keyword", "DAG third")
    cases = [
        {"id": str(index), "question": question, "group": group, "family": family,
         "expected_status": "ok", "expected": 1}
        for index, (question, family, group) in enumerate(zip(
            questions, ("same", "same", "same", "other"), ("multi_stage", "paraphrase", "paraphrase", "multi_stage"),
        ))
    ]
    report = compare_dag_planners(cases)
    summary = report["research_question"]["multi_stage_and_paraphrases"]
    assert summary["improved"] == 3
    assert summary["regressed"] == 1
    assert summary["paired_correctness_delta"]["families"] == 2
    assert summary["paired_correctness_delta"]["dag_minus_keyword"] == 0.5
    assert summary["paired_correctness_delta"]["ci95"] == pytest.approx([1 / 3, 1])
    assert summary["paired_correctness_delta"] == compare_dag_planners(cases)["overall"]["paired_correctness_delta"]
