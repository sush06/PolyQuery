import time

from .backends import CodeBackend, GraphBackend, SQLBackend
from .planner import RulePlanner


class Engine:
    def __init__(self, planner=None, *, source="synthetic", people_path=None, edges_path=None, data_path=None, policy="router"):
        started = time.perf_counter()
        self.source = source
        self.policy = policy
        if source == "synthetic":
            self.planner = planner or RulePlanner()
            self.backends = {"sql": SQLBackend(), "graph": GraphBackend(), "code": CodeBackend()}
            self.source_metadata = {"name": "synthetic"}
        elif source == "ldbc":
            from .ldbc import LDBCGraph, LDBCPlanner
            from .ldbc_sql import LDBCSQL, LDBCPolicyPlanner
            if people_path is None or edges_path is None:
                raise ValueError("LDBC requires people_path and edges_path")
            graph = LDBCGraph(people_path, edges_path)
            self.planner = planner or LDBCPolicyPlanner(LDBCPlanner(graph), policy)
            self.backends = {"graph": graph, "sql": LDBCSQL(graph)}
            self.source_metadata = graph.metadata
        elif source in ("chinook", "taxi"):
            from .tabular import TabularPlanner, chinook_backends, taxi_backends
            if data_path is None:
                raise ValueError(f"{source} requires data_path")
            self.planner = planner or TabularPlanner(source, policy)
            self.backends, self.source_metadata = (chinook_backends if source == "chinook" else taxi_backends)(data_path)
        else:
            raise ValueError(f"Unknown data source: {source}")
        if source == "synthetic" and policy != "router":
            self.close()
            raise ValueError(f"Fixed policies are not implemented for {source}")
        self.initialization_ms = (time.perf_counter() - started) * 1000

    def query(self, question):
        started = time.perf_counter()
        plan = self.planner.plan(question)
        planned = time.perf_counter()
        response = {"question": question, "source": self.source, "plan": plan.to_dict(), "status": "abstained", "result": None}
        if plan.backend != "abstain":
            if plan.backend not in self.backends:
                raise ValueError("Unknown backend")
            response["result"] = self.backends[plan.backend].execute(plan)
            response["status"] = "ok"
        finished = time.perf_counter()
        response["planning_ms"] = (planned - started) * 1000
        response["execution_ms"] = (finished - planned) * 1000
        response["latency_ms"] = (finished - started) * 1000
        return response

    def close(self):
        for backend in self.backends.values():
            if hasattr(backend, "close"):
                backend.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
