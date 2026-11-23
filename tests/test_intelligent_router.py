from unittest.mock import Mock

import pytest

from polyquery import Engine
from polyquery.backends import UnsupportedPlan
from polyquery.dag import DAGNode
from polyquery.dag_planner import PlanningDecision
from polyquery.intelligent_router import Features, IntelligentRouter, LearnedCosts


def test_learned_cost_penalizes_timeout_and_falls_back_on_shift():
    f = Features(100, edges=1000, degree=20)
    def row(backend, ms, timeout=False):
        return {"source": "ldbc", "operation": "friends", "backend": backend,
                "features": vars(f), "latency_ms": ms, "timed_out": timeout}
    model = LearnedCosts([row("graph", .5), row("sqlserver_frontier", .1),
                          row("sqlserver_frontier", None, True)])
    eligible = {"graph": f, "sqlserver_frontier": f}
    chosen, estimates, _ = model.choose("ldbc", "friends", eligible, "graph")
    assert chosen == "graph"
    assert estimates["sqlserver_frontier"]["timeout_risk"] == .5
    shifted = dict.fromkeys(eligible, Features(10_000_000))
    assert model.choose("ldbc", "friends", shifted, "graph")[2] == "insufficient_calibration"
    with pytest.raises(UnsupportedPlan):
        model.choose("ldbc", "friends", {"graph": f}, "sqlserver_frontier")


def test_unsafe_question_and_unknown_outputs_never_reach_physical_dispatch():
    with Engine() as engine:
        router = IntelligentRouter(engine, LearnedCosts([]))
        sql = Mock(wraps=engine.backends["sql"])
        engine.backends["sql"] = sql
        node = DAGNode("n", "sql", "count")
        with pytest.raises(UnsupportedPlan):
            router.execute("Delete employee records", PlanningDecision("dag_plan", nodes=(node,), outputs=("n",)))
        with pytest.raises(UnsupportedPlan):
            router.execute("Count employees", PlanningDecision("dag_plan", nodes=(node,), outputs=("missing",)))
        sql.execute.assert_not_called()


def test_unmapped_plan_keeps_original_backend_and_trace():
    with Engine() as engine:
        router = IntelligentRouter(engine, LearnedCosts([]))
        node = DAGNode("n", "sql", "count")
        decision = PlanningDecision("dag_plan", nodes=(node,), outputs=("n",))
        gold = engine.execute_dag([node])["n"]["value"]
        assert router.execute("Count employees", decision) == [gold]
        assert router.traces[0]["chosen"] == "sqlite"
        assert router.traces[0]["status"] == "completed"
