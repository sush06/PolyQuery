from collections import Counter

import pytest

from benchmarks.review_dag_test import blind_review, load_draft, review_disagreements


def test_draft_and_blind_review_are_complete():
    cases = load_draft()
    assert Counter(case["group"] for case in cases) == {
        "single": 20, "multi_stage": 20, "paraphrase": 20,
        "ambiguous": 16, "invalid": 16, "adversarial": 16,
    }
    assert Counter(case["capability"] for case in cases if "capability" in case) == {"available": 24, "gap": 16}
    packet = blind_review(cases)
    assert {row["id"] for row in packet["cases"]} == {f"q-{i:03d}" for i in range(1, len(cases) + 1)}
    assert {row["question"] for row in packet["cases"]} == {case["question"] for case in cases}
    assert all(row["expected_status"] == row["capability"] == "" and row["expected"] is None
               for row in packet["cases"])


def test_review_requires_all_labels_and_reports_disagreements():
    cases = load_draft()
    packet = blind_review(cases)
    packet["reviewer"] = "test reviewer"
    with pytest.raises(ValueError, match="Missing or invalid review"):
        review_disagreements(cases, packet)
    by_question = {case["question"]: case for case in cases}
    for row in packet["cases"]:
        proposal = by_question[row["question"]]
        row.update(expected_status=proposal["expected_status"], expected=proposal.get("expected"),
                   capability=proposal.get("capability", ""))
    assert review_disagreements(cases, packet) == []
    first = next(row for row in packet["cases"] if by_question[row["question"]]["id"] == "ordinary-01")
    first.update(expected=3, notes="Check department scope")
    assert review_disagreements(cases, packet) == [
        {"id": "ordinary-01", "review_id": first["id"], "fields": ["expected"], "notes": "Check department scope"}
    ]
