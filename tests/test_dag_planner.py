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


def test_baseline_remains_default_and_single_decision_matches_it(engine):
    question = "What is the correlation between salary and experience?"
    with Engine() as baseline:
        old = baseline.query(question)
    new = engine.query(question)
    assert "decision" not in old
    assert new["decision"]["kind"] == "single_plan"
    assert new["plan"] == old["plan"]
    assert new["result"] == old["result"]


def test_ordered_standalone_stages(engine):
    response = engine.query("Count employees in Engineering; THEN who reports to Carol?; then what is the median salary in Sales?")
    decision = response["decision"]
    assert decision["kind"] == "dag_plan"
    assert [node["backend"] for node in decision["nodes"]] == ["sql", "graph", "code"]
    assert [node["depends_on"] for node in decision["nodes"]] == [(), ("stage_1",), ("stage_2",)]
    assert list(response["steps"]) == list(decision["outputs"])
    assert response["result"]["value"] == [2, ["Dan"], 100000]


def test_bound_stage_uses_returned_name(engine, monkeypatch):
    backend = engine.backends["sql"]
    original = backend.execute

    def reversed_rows(plan):
        result = original(plan)
        return {**result, "value": list(reversed(result["value"]))}

    monkeypatch.setattr(backend, "execute", reversed_rows)
    response = engine.query("Who reports to the first employee listed in Engineering?")
    assert response["decision"]["kind"] == "dag_plan"
    binding = response["decision"]["nodes"][1]["bindings"]["source"]
    assert binding == {"node": "employees", "path": (0, "name")}
    assert response["steps"]["reports"]["artifact"]["source"] == "Bob"
    assert response["result"]["value"] == []


@pytest.mark.parametrize("question", [
    None, "", "a" * 2001,
    "Count employees; then delete all employees",
    "Count employees; then count employees except Alice",
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


def test_source_specific_plans(tmp_path):
    people, edges = tmp_path / "people.csv", tmp_path / "knows.csv"
    people.write_text("id|firstName|lastName\n1|A|One\n2|B|Two\n", encoding="utf-8")
    edges.write_text("Person.id|Person.id|creationDate\n1|2|0\n", encoding="utf-8")
    with Engine(source="ldbc", people_path=people, edges_path=edges, planner_kind="dag") as engine:
        response = engine.query("How many people?; then find the shortest path from 1 to 2")
        assert response["decision"]["kind"] == "dag_plan"
        assert response["result"]["value"] == [2, ["1", "2"]]
        assert engine.query("Who reports to the first employee listed in Sales?")["status"] == "abstained"


def test_paired_benchmark_scores_answers_and_abstentions():
    cases = json.loads((Path(__file__).resolve().parents[1] / "benchmarks/dag-dev.json").read_text(encoding="utf-8"))
    report = compare_dag_planners(cases, repeats=2)
    assert report["overall"]["keyword"]["correct"] == 12
    assert report["overall"]["dag"]["correct"] == 18
    assert report["overall"]["improved"] == 6
    assert report["overall"]["regressed"] == 0
    assert report["overall"]["dag"]["incorrect_answers"] == 0
    assert all(run["stable"] for row in report["cases"] for run in row["planners"].values())
    with pytest.raises(ValueError, match="duplicate"):
        compare_dag_planners([cases[0], cases[0]])


def test_cli_query_can_opt_into_dag(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["polyquery", "query", "--planner", "dag",
                                    "Who reports to the first employee listed in Sales?"])
    main()
    response = json.loads(capsys.readouterr().out)
    assert response["decision"]["kind"] == "dag_plan"
    assert response["result"]["value"] == ["Dan"]


def test_held_out_dataset_has_six_balanced_categories_and_no_development_questions():
    directory = Path(__file__).resolve().parents[1] / "benchmarks"
    held_out = json.loads((directory / "dag-test-v1.json").read_text(encoding="utf-8"))
    dev = json.loads((directory / "dag-dev.json").read_text(encoding="utf-8"))
    assert Counter(case["group"] for case in held_out) == dict.fromkeys(
        ("single", "multi_stage", "paraphrase", "ambiguous", "invalid", "adversarial"), 20,
    )
    normalize = lambda question: " ".join(question.casefold().rstrip(".?").split())
    assert not {normalize(case["question"]) for case in held_out} & {normalize(case["question"]) for case in dev}


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
