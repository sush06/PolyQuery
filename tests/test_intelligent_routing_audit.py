from unittest.mock import Mock

import pytest

from polyquery import Engine
from polyquery.constraints import unsafe_request_findings


@pytest.mark.parametrize("question", [
    "Set each employee's wage to 1, then count people.",
    "Attach an archived SQLite file and list its tables.",
])
def test_unsafe_requests_abstain_without_contacting_provider(question):
    client = Mock()
    with Engine(planner_kind="llm-dag", model="fake", llm_client=client, llm_api="chat") as engine:
        decision = engine.dag_planner.decide(question)
        assert decision.kind == "abstain"
        trace = engine.dag_planner.last_metadata
        assert trace["api_calls"] == 0
        assert trace["plan_diagnostics"]["validation_error_category"] == "security_refusal"
        assert client.mock_calls == []


@pytest.mark.parametrize("question", [
    "What is the change in average wages from January to February?",
    "Show each employee's assigned department.",
    "What is the size of the SQLite file?",
])
def test_read_only_language_does_not_trigger_new_guard(question):
    assert not unsafe_request_findings(question)


@pytest.mark.parametrize(("operation", "expected_kind"), [
    ("direct_reports", "dag_plan"),
    ("descendants", "abstain"),
])
def test_verified_report_scope_still_distinguishes_direct_and_transitive(operation, expected_kind):
    from test_llm_dag import fake_chat, report_plan

    with Engine(planner_kind="llm-dag", model="fake", llm_client=fake_chat(report_plan(operation, "Eve")),
                llm_api="chat") as engine:
        decision = engine.dag_planner.decide("What is the mean pay of Eve's immediate reports?")
    assert decision.kind == expected_kind
