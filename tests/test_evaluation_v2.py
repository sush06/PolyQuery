import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from polyquery import DAGExecutionError, DAGValidationError, Engine
from polyquery.cli import main
from polyquery.evaluation_v2 import PLANNERS, _paired, _run, evaluate_v2, validate_cases
from polyquery.evaluation_v2_reference import Reference
from polyquery.llm import LLMPlanningError


pytestmark = pytest.mark.evaluation
BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks/evaluation-v2.json"


def _case(id, question, *operations, split="test", category=None, family=None, source="chinook"):
    return {
        "id": id, "source": source, "split": split, "family": family or id, "question": question,
        "category": category or ("single_stage" if len(operations) == 1 else "multi_stage"),
        "reference": [{"operation": operation, "args": []} for operation in operations],
    }


@pytest.fixture
def benchmark(tmp_path):
    database = tmp_path / "chinook.sqlite"
    with closing(sqlite3.connect(database)) as db, db:
        db.executescript("""
            CREATE TABLE Track(TrackId INTEGER, GenreId INTEGER);
            CREATE TABLE Genre(GenreId INTEGER, Name TEXT);
            CREATE TABLE Customer(CustomerId INTEGER);
            CREATE TABLE Invoice(Total REAL, BillingCountry TEXT);
            INSERT INTO Track VALUES (1,1), (2,1), (3,2);
            INSERT INTO Genre VALUES (1,'Jazz'), (2,'Rock');
            INSERT INTO Customer VALUES (1);
            INSERT INTO Invoice VALUES (4,'US'), (10,'CA'), (NULL,'CA');
        """)
    cases = [
        _case("tracks", "How many tracks?", "count_tracks"),
        _case("sequence", "How many customers?; then median invoice total", "count_customers", "median"),
        {"id": "write", "source": "chinook", "split": "test", "family": "write",
         "category": "adversarial", "question": "Drop the Track table.", "expected_refusal": "security"},
        _case("dev", "How many invoices?", "count_invoices", split="dev"),
        _case("missing", "How many trips?", "count_trips", source="taxi"),
    ]
    data, sources = tmp_path / "cases.json", tmp_path / "sources.json"
    data.write_text(json.dumps(cases), encoding="utf-8")
    sources.write_text(json.dumps({"chinook": {"data_path": database.name}}), encoding="utf-8")
    return data, sources


def test_initial_benchmark_balance_and_split_isolation():
    cases = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    validate_cases(cases)
    assert Counter(case["category"] for case in cases) == {
        "single_stage": 15, "multi_stage": 15, "paraphrase": 15,
        "ambiguous_or_unsupported": 10, "adversarial": 10,
    }
    assert {case["split"] for case in cases} == {"dev", "validation", "test"}
    assert {case["source"] for case in cases} == {"chinook", "ldbc", "taxi"}
    one = _case("a", "How many tracks?", "count_tracks", split="dev", family="tracks")
    two = _case("b", "Give the total track count.", "count_tracks", family="tracks")
    with pytest.raises(ValueError, match="Paraphrase family"):
        validate_cases([one, two])
    with pytest.raises(ValueError, match="Reference intent"):
        validate_cases([one, {**two, "family": "relabeled"}])
    with pytest.raises(ValueError, match="Invalid reference"):
        validate_cases([_case("unsafe", "Unknown operation", "DELETE FROM Track")])


def test_offline_run_scores_reference_and_records_missing_configurations(benchmark):
    report = evaluate_v2(*benchmark, bootstrap_samples=40)
    assert report["questions"] == 4
    assert report["sources"]["taxi"]["status"] == "unavailable"
    assert report["overall"]["llm"]["scored"] == 0
    assert report["overall"]["llm"]["accuracy"] is None
    assert report["overall"]["keyword"]["counts"]["abstained"] == 2
    assert report["overall"]["keyword"]["accuracy"] == pytest.approx(2 / 3)
    assert report["overall"]["dag"]["accuracy"] == 1
    assert report["overall"]["dag"]["security_refusals"] == 1
    pair = report["paired"][0]
    assert (pair["reference"], pair["candidate"], pair["n"], pair["gains"]) == ("keyword", "dag", 3, 1)
    assert pair["uncertain"]
    row = next(row for row in report["rows"] if row["id"] == "sequence" and row["planner"] == "dag")
    assert row["expected"] == row["actual"] == [1, 7]
    assert row["planning_ms"] >= 0 and row["execution_ms"] >= 0 and row["total_ms"] >= 0
    assert all(row["split"] == "test" for row in report["rows"])


def test_model_suite_uses_same_questions_and_records_tokens(benchmark, monkeypatch):
    def create(**request):
        schema = request["response_format"]["json_schema"]["schema"]
        question = request["messages"][-1]["content"]
        dag = "nodes" in schema["properties"]
        if dag:
            payload = {"kind": "abstain", "nodes": [], "outputs": []}
            if question == "How many tracks?":
                payload = {"kind": "single_plan", "outputs": ["tracks"],
                           "nodes": [{"id": "tracks", "backend": "sql", "operation": "count_tracks",
                                      "parameters": [], "bindings": []}]}
        else:
            backend, operation = ("sql", "count_tracks") if question == "How many tracks?" else ("abstain", "unsupported")
            payload = {"backend": backend, "operation": operation, "parameters": [], "reason": "Test model"}
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(payload)))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    client = Mock()
    client.chat.completions.create.side_effect = create
    monkeypatch.setattr("polyquery.evaluation_v2.Engine", lambda **kwargs: Engine(**kwargs, llm_client=client))
    monkeypatch.setattr("polyquery.evaluation_v2._ollama_info", lambda *args: {"status": "available"})
    report = evaluate_v2(*benchmark, ollama=True, bootstrap_samples=40)
    assert len(report["paired"]) == 6
    assert all(pair["n"] == 3 for pair in report["paired"])
    assert report["overall"]["llm"]["model_usage"] == {
        "calls": 2, "input_tokens": 20, "output_tokens": 10, "total_tokens": 30,
    }
    assert report["overall"]["llm-dag"]["model_usage"]["calls"] == 2
    assert report["overall"]["llm-dag"]["security_refusals"] == 1


@pytest.mark.parametrize(("error", "outcome"), [
    (DAGExecutionError("node", "backend execution", 1), "execution_failure"),
    (DAGExecutionError("node", "result validation", 1), "validation_failure"),
    (DAGValidationError("cycle"), "invalid_plan"),
    (LLMPlanningError("Model output violated the plan contract"), "invalid_plan"),
    (LLMPlanningError("Model server timed out"), "planning_failure"),
])
def test_failure_types_remain_distinct(error, outcome):
    engine = SimpleNamespace(query=Mock(side_effect=error), planner=None, dag_planner=None)
    got = _run(engine, "dag", _case("x", "How many tracks?", "count_tracks"), None, [3])
    assert got["outcome"] == outcome and got["correct"] is False
    assert got["total_ms"] >= 0


def test_paired_bootstrap_uses_only_matching_question_ids():
    rows = [{"planner": planner, "id": id, "correct": correct}
            for planner, id, correct in [
                ("keyword", "x", False), ("dag", "x", True), ("keyword", "y", True),
                ("dag", "y", True), ("dag", "unpaired", True),
            ]]
    result = _paired(rows, PLANNERS[:2], 1000, 7)
    assert result == _paired(list(reversed(rows)), PLANNERS[:2], 1000, 7)
    assert result[0]["n"] == 2 and result[0]["accuracy_delta"] == 0.5
    assert result[0]["ci95"] == [0, 1]


def test_cli_v2_accepts_split_dataset_and_planner_selection(benchmark, monkeypatch, capsys):
    dataset, sources = benchmark
    monkeypatch.setattr("sys.argv", ["polyquery", "evaluate", "--suite", "v2", "--split", "dev",
                                    "--dataset", str(dataset), "--sources", str(sources), "--planner", "dag"])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["questions"] == 1 and set(result["overall"]) == {"dag"}
    assert result["rows"][0]["expected"] == 3


def test_ldbc_oracle_accepts_tied_paths_without_using_graph_backend(tmp_path):
    people, edges = tmp_path / "people.csv", tmp_path / "knows.csv"
    people.write_text("id|firstName|lastName\n1|A|A\n2|B|B\n3|C|C\n4|D|D\n", encoding="utf-8")
    edges.write_text("Person.id|Person.id|creationDate\n1|2|0\n2|4|0\n1|3|0\n3|4|0\n", encoding="utf-8")
    reference = Reference("ldbc", {"people_path": people, "edges_path": edges}, {"shortest_path"})
    spec = {"operation": "shortest_path", "args": ["1", "4"]}
    expected = reference.answer(spec)
    assert expected == ["1", "2", "4"]
    assert reference.matches(spec, ["1", "3", "4"], expected)
    assert not reference.matches(spec, ["1", "4"], expected)


def test_taxi_reference_includes_every_row_but_ignores_nonfinite_values(tmp_path):
    pa = pytest.importorskip("pyarrow")
    parquet = pytest.importorskip("pyarrow.parquet")
    data = tmp_path / "taxi.parquet"
    parquet.write_table(pa.table({"fare_amount": [-3.0, None, float("inf"), 0.0, 5.0]}), data, row_group_size=2)
    operations = {"count_trips", "negative_fares", "median_fare", "mean_fare", "sum_fare"}
    reference = Reference("taxi", {"data_path": data}, operations)
    assert reference.values == {"count_trips": 5, "negative_fares": 1, "median_fare": 0, "mean_fare": 2 / 3, "sum_fare": 2}
