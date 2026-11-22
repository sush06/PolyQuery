"""Physical SQL dispatch after logical planning and binding."""

from dataclasses import dataclass
from enum import StrEnum
from math import dist, isfinite, log1p

from .backends import UnsupportedPlan


class SQLBackendID(StrEnum):
    SQLITE = "sqlite"
    SQLSERVER = "sqlserver"


@dataclass(frozen=True)
class SQLFeatures:
    rows: int
    joins: int
    groups: int
    selectivity: float

    def vector(self):
        counts = (self.rows, self.joins, self.groups)
        if (any(type(x) is not int or x < 0 for x in counts)
                or type(self.selectivity) not in (int, float) or not 0 <= self.selectivity <= 1):
            raise ValueError("Invalid SQL workload features")
        return (log1p(self.rows), self.joins, log1p(self.groups), self.selectivity)


def fixed_sqlite(plan):
    return SQLBackendID.SQLITE


class PhysicalCostModel:
    """Estimate the log latency ratio from nearby development feature vectors."""

    def __init__(self, development):
        self.points = tuple((features.vector(), log1p(remote) - log1p(local))
                            for features, local, remote in development
                            if _valid_latency(local) and _valid_latency(remote))
        if not self.points or len(self.points) != len(development):
            raise ValueError("Cost calibration requires finite nonnegative paired latencies")

    def choose(self, features):
        vector = features.vector()
        neighbors = sorted((dist(vector, point), effect) for point, effect in self.points)[:5]
        score = sum(effect / (0.25 + distance**2) for distance, effect in neighbors)
        return SQLBackendID.SQLSERVER if score < 0 else SQLBackendID.SQLITE


def _valid_latency(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


class PhysicalSQLRouter:
    """Dispatch over a trusted, explicitly registered pair of equivalent adapters.

    Each adapter registers the same source snapshot and reviewed operation
    contracts. Adapter provisioning validates the underlying datasets and access.
    The selector receives a validated logical plan, never SQL or a connection.
    """

    def __init__(self, catalog, implementations, selector=fixed_sqlite):
        if set(implementations) != set(SQLBackendID) or not callable(selector):
            raise ValueError("Physical routing requires SQLite and SQL Server")
        local, remote = (implementations[name] for name in SQLBackendID)
        if (not local[0] or local[0] != remote[0] or not local[1] or local[1] != remote[1]
                or any(catalog.operations.get(name) != spec or "sql" not in spec.backends
                       for name, spec in local[1].items())):
            raise ValueError("Physical SQL mappings require equivalent snapshot and catalog contracts")
        self.catalog, self.selector = catalog, selector
        self.operations = frozenset(local[1])
        self.adapters = {name: registration[2] for name, registration in implementations.items()}
        if any(not callable(getattr(adapter, "execute", None)) for adapter in self.adapters.values()):
            raise ValueError("Physical SQL backend unavailable")

    def execute(self, plan):
        self.catalog.validate_plan(plan)
        if plan.backend != "sql" or plan.operation not in self.operations:
            raise UnsupportedPlan("Operation is outside the equivalent physical SQL mapping")
        try:
            backend = SQLBackendID(self.selector(plan))
        except (TypeError, ValueError):
            raise UnsupportedPlan("Invalid physical SQL backend selection") from None
        result = self.adapters[backend].execute(plan)
        self.catalog.validate_result(plan, result)
        return {**result, "physical_backend": backend.value}

    def close(self):
        # SQL Server's existing adapter owns the shared SQLite fallback.
        remote = self.adapters[SQLBackendID.SQLSERVER]
        if hasattr(remote, "close"):
            remote.close()
