"""Verified Qwen DAG planning with opt-in learned physical execution."""
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from statistics import median
from time import perf_counter

from .backends import UnsupportedPlan
from .constraints import unsafe_request_findings
from .dag import DAGExecutor, DAGNode
from .dag_planner import validate_question_plan
from .ldbc_sql_frontier import LDBCSQLFrontier
from .ldbc_sql_graph import TraversalLimit
from .llm import LLMPlanningError
from .planner import Plan
from .sqlserver import ChinookSQLServer, fixture_hash

LDBC_OPS = frozenset({"count_people", "friends", "shortest_path"})
CHINOOK_TABLES = {"count_tracks": "Track", "count_customers": "Customer",
                  "count_invoices": "Invoice", "revenue": "Invoice", "mean": "Invoice"}
MODEL = "qwen3:4b-instruct"


@dataclass(frozen=True)
class Features:
    rows: int
    edges: int = 0
    selectivity: float | None = None
    joins: int = 0
    hop_estimate: int | None = None
    degree: int = 0
    indexed: bool = False
    resident: bool = True
    connected: bool = True

    def __post_init__(self):
        required = (self.rows, self.edges, self.joins, self.degree)
        for value in (*required, *(() if self.hop_estimate is None else (self.hop_estimate,))):
            if type(value) is not int or value < 0:
                raise ValueError("Invalid feature cardinality")
        if self.selectivity is not None and (type(self.selectivity) not in (int, float)
                                             or not 0 <= self.selectivity <= 1):
            raise ValueError("Invalid selectivity")
        if any(type(value) is not bool for value in (self.indexed, self.resident, self.connected)):
            raise ValueError("Invalid physical backend state")

    def vector(self):
        return (math.log1p(self.rows), math.log1p(self.edges), float(self.joins),
                math.log1p(self.degree), self.selectivity if self.selectivity is not None else -1,
                self.hop_estimate if self.hop_estimate is not None else -1,
                int(self.indexed), int(self.resident), int(self.connected))


def features(source, operation, *, rows, edges=0, degree=0, identical=False, backend):
    indexed = source == "ldbc" and backend == "sqlserver_frontier"
    return Features(rows, edges, (degree / rows if rows else 0) if operation == "friends" else None,
                    int(indexed and operation != "count_people"),
                    0 if operation == "shortest_path" and identical else
                    1 if operation == "friends" else None,
                    degree, indexed, backend != "sqlserver" and not indexed, True)


class LearnedCosts:
    """K-nearest development cases, with censored timeouts and bounded confidence."""

    def __init__(self, rows, *, timeout_penalty_ms=2000):
        self.points = defaultdict(list)
        if not math.isfinite(timeout_penalty_ms) or timeout_penalty_ms <= 0:
            raise ValueError("Invalid timeout penalty")
        self.timeout_penalty_ms = timeout_penalty_ms
        for row in rows:
            elapsed = row["latency_ms"]
            if (type(row["timed_out"]) is not bool
                    or (elapsed is None) != row["timed_out"]
                    or elapsed is not None and (not math.isfinite(elapsed) or elapsed < 0)):
                raise ValueError("Invalid development latency label")
            key = (row["source"], row["operation"], row["backend"])
            self.points[key].append((Features(**row["features"]).vector(), elapsed))

    def predict(self, source, operation, backend, feature):
        points = self.points.get((source, operation, backend), ())
        vector = feature.vector()
        nearest = sorted(((math.dist(point, vector), latency) for point, latency in points),
                         key=lambda item: item[0])[:5]
        if not nearest:
            return None
        timings = [ms for _, ms in nearest if ms is not None]
        risk = (len(nearest) - len(timings)) / len(nearest)
        latency = median(timings) if timings else None
        return {"latency_ms": latency, "timeout_risk": risk, "neighbors": len(nearest),
                "nearest_distance": nearest[0][0],
                "cost_ms": (1 - risk) * (latency or 0) + risk * self.timeout_penalty_ms}

    def choose(self, source, operation, eligible, default):
        if default not in eligible:
            raise UnsupportedPlan("No safe fixed implementation")
        predictions = {name: self.predict(source, operation, name, feature)
                       for name, feature in eligible.items()}
        if any(p is None or p["nearest_distance"] > 1.5 for p in predictions.values()):
            return default, predictions, "insufficient_calibration"
        ranked = sorted(predictions, key=lambda name: (predictions[name]["cost_ms"], name != default, name))
        if len(ranked) > 1:
            a, b = (predictions[name]["cost_ms"] for name in ranked[:2])
            if b - a <= max(.02, .1 * b):
                return default, predictions, "small_predicted_margin"
        return ranked[0], predictions, "predicted_cost"


class _Slot:
    def __init__(self, router):
        self.router = router

    def execute(self, plan):
        return self.router.dispatch(plan)


class IntelligentRouter:
    """Use a Qwen Engine for planning and independently provisioned read adapters."""

    def __init__(self, engine, model, *, remote=None):
        self.engine, self.model, self.remote = engine, model, remote
        self.source, self.catalog = engine.source, engine.dag_executor.catalog
        self.graph = engine.backends.get("graph") if self.source == "ldbc" else None
        self.edge_count = sum(map(len, self.graph.adjacency.values())) if self.graph else 0
        self.chinook_rows = engine.source_metadata.get("rows", {}) if self.source == "chinook" else {}
        if remote is not None:
            chinook = (self.source == "chinook" and isinstance(remote, ChinookSQLServer)
                       and remote.fixture_hash == fixture_hash(engine.source_metadata["database_file"]))
            ldbc = (self.source == "ldbc" and isinstance(remote, LDBCSQLFrontier)
                    and remote.catalog is self.catalog)
            if not (chinook or ldbc):
                raise ValueError("Remote adapter is outside the verified source mapping")
        self.executor = DAGExecutor(self.catalog, {name: _Slot(self) for name in engine.backends})
        self.traces = []
        self.mode = "learned"

    def candidates(self, plan):
        original = {"graph": "graph", "sql": "sqlite", "code": "code"}[plan.backend]
        native = (plan.backend, self.engine.backends[plan.backend])
        if self.source == "ldbc" and plan.operation in LDBC_OPS and plan.backend in ("graph", "sql"):
            # The original logical backend is preserved for baseline controls.
            local = {"graph": ("graph", self.engine.backends["graph"])}
            if self.remote is not None:
                local["sqlserver_frontier"] = ("sql", self.remote)
            return local, "graph"
        if self.source == "chinook" and plan.backend == "sql" and plan.operation in CHINOOK_TABLES:
            candidates = {"sqlite": native}
            if self.remote is not None:
                candidates["sqlserver"] = ("sql", self.remote)
            return candidates, "sqlite"
        return {original: native}, original

    def dispatch(self, plan):
        self.catalog.validate_plan(plan)
        started = perf_counter()
        options, default = self.candidates(plan)
        mapped = (self.source == "ldbc" and plan.operation in LDBC_OPS
                  or self.source == "chinook" and plan.backend == "sql" and plan.operation in CHINOOK_TABLES)
        rationale, predictions = "single_eligible", {}
        if self.mode == "native":
            chosen = {"graph": "graph", "sql": "sqlite", "code": "code"}[plan.backend]
            options = {chosen: (plan.backend, self.engine.backends[plan.backend])}
            rationale = "original_logical_backend"
        elif self.mode == "fixed" and mapped:
            chosen = "sqlserver_frontier" if self.source == "ldbc" else "sqlserver"
            if chosen not in options:
                self.traces.append({"operation": plan.operation, "status": "unavailable",
                                    "chosen": None, "eligible": list(options)})
                raise UnsupportedPlan("Fixed SQL Server implementation unavailable")
            rationale = "fixed_sqlserver"
        elif len(options) == 1:
            chosen = default
        else:
            eligible = {name: self._features(plan, name) for name in options}
            if self.mode == "oracle":
                chosen, rationale, predictions = self._oracle(plan, options, default)
            else:
                chosen, predictions, rationale = self.model.choose(self.source, plan.operation, eligible, default)
        if chosen not in options:
            raise UnsupportedPlan("Ineligible physical implementation")
        trace = {"operation": plan.operation, "logical_backend": plan.backend, "chosen": chosen,
                 "eligible": list(options), "predictions": predictions, "reason": rationale,
                 "selection_ms": (perf_counter() - started) * 1000}
        if mapped:
            trace["features"] = asdict(self._features(plan, chosen))
        self.traces.append(trace)
        return self._execute(plan, options[chosen], trace)

    def _features(self, plan, name):
        if self.graph is not None:
            source_id = plan.parameters.get("source")
            return features("ldbc", plan.operation, rows=len(self.graph.people), edges=self.edge_count,
                            degree=len(self.graph.adjacency.get(source_id, ())),
                            identical=source_id is not None and source_id == plan.parameters.get("target"), backend=name)
        return features("chinook", plan.operation, rows=self.chinook_rows.get(CHINOOK_TABLES[plan.operation], 0),
                        backend=name)

    def _execute(self, plan, implementation, trace):
        backend, adapter = implementation
        physical = replace(plan, backend=backend)
        self.catalog.validate_plan(physical)
        start = perf_counter()
        try:
            result = adapter.execute(physical)
            self.catalog.validate_result(physical, result)
        except TraversalLimit:
            trace["status"] = "timeout"
            raise
        except Exception:
            trace["status"] = "failure"
            raise
        else:
            trace["status"] = "completed"
            return result
        finally:
            trace["actual_ms"] = (perf_counter() - start) * 1000

    def _oracle(self, plan, options, default):
        # Only the explicit evaluator calls this lane. These observations
        # are generated after the learned lane and never enter its features.
        probes = {}
        for name, implementation in options.items():
            traces = [{} for _ in range(3)]
            for trace in traces:
                try:
                    self._execute(plan, implementation, trace)
                except TraversalLimit:
                    break
            samples = [t["actual_ms"] for t in traces if t.get("status") == "completed"]
            probes[name] = {"samples_ms": samples,
                            "p50": median(samples) if len(samples) == len(traces) else None}
        eligible = [name for name in probes if probes[name]["p50"] is not None]
        if not eligible:
            raise TraversalLimit("No oracle candidate completed")
        return min(eligible, key=lambda name: (probes[name]["p50"], name != default)), "evaluation_oracle", probes

    @staticmethod
    def nodes(decision):
        if decision.kind == "single_plan":
            plan = decision.plan
            return (DAGNode("result", plan.backend, plan.operation, plan.parameters),), ("result",)
        return decision.nodes, decision.outputs

    def execute(self, question, decision, *, mode="learned"):
        if mode not in ("learned", "fixed", "oracle", "native"):
            raise ValueError("Invalid execution mode")
        self.mode, self.traces = mode, []
        if decision.kind == "abstain":
            return None
        if unsafe_request_findings(question):
            raise UnsupportedPlan("Request safety check blocks physical dispatch")
        if decision.kind == "single_plan":
            lexical, canonical = validate_question_plan(question, decision.plan, self.engine.catalog)
            if not lexical.allowed or canonical["preserved"] is False:
                raise UnsupportedPlan("Question constraints block physical dispatch")
        nodes, outputs = self.nodes(decision)
        if not outputs or len(set(outputs)) != len(outputs) or not set(outputs) <= {node.id for node in nodes}:
            raise UnsupportedPlan("Invalid requested outputs")
        results = self.executor.execute(nodes)
        return [results[node]["value"] for node in outputs]

    def query(self, question):
        if self.engine.dag_planner is None or not hasattr(self.engine.dag_planner, "configure_grounding"):
            raise ValueError("Intelligent routing requires the grounded Qwen DAG planner")
        start = perf_counter()
        try:
            decision = self.engine.dag_planner.decide(question)
        except LLMPlanningError:
            return {"status": "semantic_failure", "outputs": None, "physical_trace": [],
                    "planning_ms": (perf_counter() - start) * 1000,
                    "model": self.engine.dag_planner.last_metadata}
        planned = perf_counter()
        diagnostic = self.engine.dag_planner.last_metadata.get("plan_diagnostics", {})
        validation = diagnostic.get("final_validation_error_category") or diagnostic.get("validation_error_category")
        status = ("semantic_failure" if validation and validation != "security_refusal"
                  else "abstained") if decision.kind == "abstain" else "ok"
        try:
            values = self.execute(question, decision)
        except Exception:
            values = None
            status = "timeout" if any(trace.get("status") == "timeout" for trace in self.traces) else "execution_failure"
        return {"status": status,
                "decision": decision.to_dict(), "outputs": values, "physical_trace": self.traces,
                "planning_ms": (planned - start) * 1000, "total_ms": (perf_counter() - start) * 1000,
                "model": self.engine.dag_planner.last_metadata}
