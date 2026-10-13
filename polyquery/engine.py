import time

from .backends import CodeBackend, GraphBackend, SQLBackend
from .planner import RulePlanner


class Engine:
    def __init__(self, planner=None, *, source="synthetic", people_path=None, edges_path=None, data_path=None, policy="router", cost_profile=None, planner_kind="keyword", model=None, llm_client=None, llm_fallback="none", llm_base_url=None, llm_api="responses", llm_timeout=30):
        started = time.perf_counter()
        self.source = source
        self.policy = policy
        if planner_kind not in ('keyword','llm') or (planner is not None and planner_kind != 'keyword'):
            raise ValueError('Invalid or conflicting planner configuration')
        if planner_kind == 'llm' and policy not in ('router', 'cost-aware'):
            raise ValueError('LLM planning requires policy=router or cost-aware')
        if llm_fallback not in ("none", "keyword") or (llm_fallback != "none" and planner_kind != "llm"):
            raise ValueError("LLM fallback requires planner_kind=llm and none or keyword")
        execution_policy = "router" if policy == "cost-aware" else policy
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
            self.planner = planner or LDBCPolicyPlanner(LDBCPlanner(graph), execution_policy)
            self.backends = {"graph": graph, "sql": LDBCSQL(graph)}
            self.source_metadata = graph.metadata
        elif source in ("chinook", "taxi"):
            from .tabular import TabularPlanner, chinook_backends, taxi_backends
            if data_path is None:
                raise ValueError(f"{source} requires data_path")
            self.planner = planner or TabularPlanner(source, execution_policy)
            self.backends, self.source_metadata = (chinook_backends if source == "chinook" else taxi_backends)(data_path)
        else:
            raise ValueError(f"Unknown data source: {source}")
        if source == "synthetic" and policy != "router":
            self.close()
            raise ValueError(f"Fixed policies are not implemented for {source}")
        from .catalog import build_catalog
        self.catalog = build_catalog(self)
        if planner_kind == 'llm':
            try:
                from .llm import LLMPlanner
                self.planner = LLMPlanner(self.catalog,model,client=llm_client,
                                          fallback=self.planner if llm_fallback == "keyword" else None,
                                          base_url=llm_base_url, api_mode=llm_api, timeout=llm_timeout)
            except Exception:
                self.close()
                raise
        if policy == "cost-aware":
            try:
                from .costs import CostPlanner, load_profile
                from .evaluation import source_fingerprint
                if cost_profile is None:
                    raise ValueError("Cost-aware policy requires cost_profile")
                self.cost_profile = load_profile(cost_profile)
                self.catalog.costs = {'status':'measured_training', 'units':'execution_ms',
                                      'operation_costs':self.cost_profile['operation_costs_ms'],
                                      'unseen_operation_fallback':self.cost_profile['fallback_costs_ms'],
                                      'training_dataset_sha256':self.cost_profile['train_dataset_sha256'],
                                      'source_hashes':self.cost_profile['source_hashes'],
                                      'calibrated_at':self.cost_profile['created_at'], 'monetary_cost':None}
                self.planner = CostPlanner(self.planner, self.cost_profile, source, source_fingerprint(self), self.backends, catalog=self.catalog)
            except Exception:
                self.close()
                raise
        self.initialization_ms = (time.perf_counter() - started) * 1000

    def query(self, question):
        started = time.perf_counter()
        plan = self.planner.plan(question)
        self.catalog.validate_plan(plan)
        planned = time.perf_counter()
        response = {"question": question, "source": self.source, "plan": plan.to_dict(), "status": "abstained", "result": None}
        if hasattr(self.planner,'last_metadata'):
            response['planner_metadata'] = dict(self.planner.last_metadata)
        if plan.backend != "abstain":
            if plan.backend not in self.backends:
                raise ValueError("Unknown backend")
            response["result"] = self.backends[plan.backend].execute(plan)
            self.catalog.validate_result(plan, response['result'])
            response["status"] = "ok"
        finished = time.perf_counter()
        response["planning_ms"] = (planned - started) * 1000
        response["execution_ms"] = (finished - planned) * 1000
        response["latency_ms"] = (finished - started) * 1000
        return response

    def close(self):
        if hasattr(self.planner,'close'):
            self.planner.close()
        for backend in self.backends.values():
            if hasattr(backend, "close"):
                backend.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
