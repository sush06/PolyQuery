"""Add calibration and latency analyses to the M7.1 measurement journal."""
import math
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean

from benchmarks.intelligent_routing import read, write
from benchmarks.m7_physical import cost
from polyquery.evaluation import percentile, timing_summary

RAW = Path("logs/m7-physical-results.json")


def add_metrics(report):
    penalty = report["manifest"]["failure_penalty_ms"]
    for key, result in report["summary"].items():
        split, mode = key.split("/")
        rows = [r for r in report["rows"] if r["split"] == split]
        completion_deltas = defaultdict(list)
        sql_errors, graph_errors, brier, risks, observed = [], [], [], [], []
        for row, entry in zip(rows, result["cases"], strict=True):
            runs = [t for t in row["trials"] if t["mode"] == mode]
            for name, policy in entry["policies"].items():
                backend = policy["backend"]
                samples = [t["observed"][backend] for t in runs]
                selection = 0 if name == "oracle" else policy["selection_ms"]
                policy["end_to_end_ms"] = timing_summary([s["setup_ms"]+s["execution_ms"]+selection for s in samples])
                policy["setup_ms"] = timing_summary([s["setup_ms"] for s in samples])
                policy["timeouts"] = sum(s["status"] == "timeout" for s in samples)
                policy["failures"] = sum(s["status"] == "failure" for s in samples)
                if name not in ("learned", "oracle"):
                    completion_deltas[name].append(
                        (entry["policies"]["learned"]["completed"]-policy["completed"])/len(runs))
            if mode != "warm":
                continue
            for trial in runs:
                estimates = trial["selections"]["learned"]["predictions"]
                samples = trial["observed"]
                if row["source"] == "chinook_scale":
                    local, remote = samples["sqlite"], samples["sqlserver"]
                    if local["status"] == remote["status"] == "completed":
                        actual = math.log1p(remote["execution_ms"])-math.log1p(local["execution_ms"])
                        sql_errors.append(estimates["log_latency_ratio"]-actual)
                    continue
                for backend, sample in samples.items():
                    for pred, timing in zip(estimates, sample["nodes"]):
                        prediction = pred["values"].get(backend)
                        if prediction is None:
                            continue
                        if timing["completed"] and prediction["latency_ms"] is not None:
                            graph_errors.append(prediction["latency_ms"]-timing["ms"])
                        if timing["completed"] or sample["status"] == "timeout":
                            risk, actual = prediction["timeout_risk"], int(not timing["completed"])
                            brier.append((risk-actual)**2)
                            risks.append(risk)
                            observed.append(actual)
        rng = random.Random(721)
        for name, deltas in completion_deltas.items():
            values = [mean(rng.choices(deltas,k=len(deltas))) for _ in range(2000)]
            result["paired"][name]["completion_rate_delta"] = mean(deltas)
            result["paired"][name]["completion_bootstrap_95"] = [
                percentile(values,.025),percentile(values,.975)]
        result["prediction_errors"] = {
            "sql_log_ratio": error_summary(sql_errors), "graph_latency_ms": error_summary(graph_errors),
            "graph_timeout_risk": {"samples":len(brier), "brier":mean(brier) if brier else None,
                                   "mean_predicted":mean(risks) if risks else None,
                                   "observed":mean(observed) if observed else None}}
        for name, policy in result["policies"].items():
            end_to_end, setups, regret, overhead = [], [], [], []
            for row, entry in zip(rows,result["cases"],strict=True):
                chosen = entry["policies"][name]
                for trial in row["trials"]:
                    if trial["mode"] != mode:
                        continue
                    obs = trial["observed"][chosen["backend"]]
                    select = trial["selections"][name]["selection_ms"] if name != "oracle" else 0
                    setups.append(obs["setup_ms"])
                    overhead.append(select)
                    end_to_end.append(obs["setup_ms"]+obs["execution_ms"]+select)
                regret.append(chosen.get("regret_ms",0))
            policy.update({"end_to_end_ms":timing_summary(end_to_end), "setup_ms":timing_summary(setups),
                           "median_regret_ms":mean(regret) if len(regret)==1 else percentile(regret,.5),
                           "selection_ms":timing_summary(overhead),
                           "timeouts":sum(x["policies"][name]["timeouts"] for x in result["cases"]),
                           "failures":sum(x["policies"][name]["failures"] for x in result["cases"])})
    return report


def error_summary(errors):
    return {"samples":len(errors), "bias":mean(errors) if errors else None,
            "mae":mean(map(abs,errors)) if errors else None}


if __name__ == "__main__":
    r = add_metrics(read(RAW))
    write(Path("logs/m7-physical-analysis.json"),r)
    write(Path("benchmarks/m7-physical-summary.json"),
          {key:value for key,value in r.items() if key!="rows"})
