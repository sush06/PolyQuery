"""Summarize matched LDBC graph cases and render traversal latency plots."""
import argparse
import json
from collections import Counter
from pathlib import Path

from polyquery.evaluation import percentile

BACKENDS = ("bfs", "sqlite", "sqlserver", "sqlserver_frontier")


def present_backends(report):
    return [backend for backend in BACKENDS if any(
        backend in case.get("backends", {}) for frame in report["scales"].values() for case in frame["cases"])]


def summarize(report, json_name="ldbc-sql-graph.json", image_name="ldbc-sql-graph.png"):
    backends = present_backends(report)
    lines = ["# LDBC graph benchmark", "",
             "Warm single-node executions. Setup timings are separate. Latencies use completed cases.",
             "Coverage is completed cases divided by supported logical cases. Accuracy is conditional on completion.", "",
             "| People | Arm | Correct / supported | Query p50/p95 (ms) | DAG p50/p95 (ms) |",
             "| ---: | --- | ---: | ---: | ---: |"]
    wins = []
    depth_rows = []
    existing_rows = []
    setup_rows = []
    shortest_rows = []
    for size, frame in report["scales"].items():
        setup = frame["initialization"]
        metrics = ("bfs_load_ms", "sqlite_connection_ms", "sqlite_load_and_verify_ms",
                   "sqlserver_connection_ms", "sqlserver_reader_verify_ms")
        setup_values = [setup.get(key) for key in metrics]
        if "sqlserver_frontier" in backends:
            setup_values.extend(setup.get("frontier", {}).get(key) for key in metrics[-2:])
        setup_rows.append("| " + " | ".join([size, *[
            f"{value:.3f}" if value is not None else "—" for value in setup_values]]) + " |")
        supported = [c for c in frame["cases"] if c["operation"] != "unsupported_question"]
        paths = [c for c in supported if c["operation"] == "shortest_path"]
        for backend in backends:
            complete = [c for c in supported if c["backends"].get(backend, {}).get("status") == "correct"]
            if not any(backend in c["backends"] for c in supported):
                continue
            lines.append(f"| {size} | {backend} | {len(complete)}/{len(supported)} | "
                         f"{timing(complete, backend, 'query')} | {timing(complete, backend, 'dag')} |")
            complete_paths = [c for c in complete if c["operation"] == "shortest_path"]
            shortest_rows.append(f"| {size} | {backend} | {len(complete_paths)}/{len(paths)} | "
                                 f"{timing(complete_paths, backend, 'dag')} |")
        strata = {}
        for case in supported:
            if case["operation"] != "shortest_path":
                label = case["operation"]
            else:
                label = str(case["hops"]) if case["reachable"] else "unreachable"
            strata.setdefault(label, []).append(case)
        for label, cases in strata.items():
            row = [size, label, str(len(cases))]
            for backend in backends:
                complete = [c for c in cases if c["backends"].get(backend, {}).get("status") == "correct"]
                row.append(f"{len(complete)}/{len(cases)}; {timing(complete, backend, 'dag')}")
            depth_rows.append("| " + " | ".join(row) + " |")

        for backend in backends[1:]:
            paired = [c for c in supported if c["backends"].get(backend, {}).get("status") == "correct"]
            if not paired:
                continue
            counts = Counter()
            sql_wins = []
            for case in paired:
                a, b = [case["backends"][name]["dag"]["p50"] for name in ("bfs", backend)]
                outcome = "bfs" if a < b else backend if a > b else "tie"
                counts[outcome] += 1
                if outcome == backend:
                    sql_wins.append(case["id"])
            ids = ", ".join(sql_wins) or "none"
            wins.append(f"- {size} people, {backend}: BFS won {counts['bfs']}/{len(paired)}, "
                        f"SQL won {counts[backend]}/{len(paired)}, ties {counts['tie']}. SQL wins: {ids}.")

        if "sqlserver_frontier" in backends:
            matched = [case for case in supported if case["operation"] == "shortest_path" and
                       all(case["backends"][name]["status"] == "correct"
                           for name in ("sqlserver", "sqlserver_frontier"))]
            wins_frontier = sum(case["backends"]["sqlserver_frontier"]["dag"]["p50"] <
                                case["backends"]["sqlserver"]["dag"]["p50"] for case in matched)
            wins.append(f"- {size} people: SQL Server frontier beat the SQL Server CTE in "
                        f"{wins_frontier}/{len(matched)} matched shortest-path cases.")

        existing = [c for c in frame["cases"] if c["id"].startswith("existing-")]
        if existing:
            eligible = [c for c in existing if c["operation"] != "unsupported_question"]
            for backend in backends:
                completed = [c for c in eligible if c["backends"].get(backend, {}).get("status") == "correct"]
                existing_rows.append(f"| {size} | {backend} | {len(completed)}/{len(eligible)} | "
                                     f"{timing(completed, backend, 'dag')} | {len(existing)-len(eligible)} |")
    setup_headers = ["People", "BFS load", "SQLite connect", "SQLite load and verify", "SQL Server connect", "SQL Server verify"]
    if "sqlserver_frontier" in backends:
        setup_headers.extend(["Frontier connect", "Frontier verify"])
    return "\n".join([*lines, "", "## Shortest paths only", "",
                      "| People | Arm | Correct / supported | DAG p50/p95 (ms) |",
                      "| ---: | --- | ---: | ---: |", *shortest_rows,
                      "", "## Initialization (ms)", "",
                      "| " + " | ".join(setup_headers) + " |", "|"+ " ---: |" * len(setup_headers), *setup_rows,
                      "", "## Existing LDBC development questions", "",
                      "| People | Arm | Correct / supported | DAG p50/p95 (ms) | Unsupported questions |",
                      "| ---: | --- | ---: | ---: | ---: |", *existing_rows,
                      "", "## Depth and scale", "",
                      "Each backend cell shows completed / supported cases and DAG p50/p95 in ms.", "",
                      "| People | Hops or operation | Cases | " + " | ".join(backends) + " |",
                      "| ---: | --- | ---: |" + " --- |" * len(backends), *depth_rows,
                      "", "## Paired cases", "", *wins, "",
                      "A SQL timeout or unresolved search depth is a coverage limit. It is never scored as disconnected.",
                      "The smaller scales are induced subgraphs of the first IDs in numeric order. They differ in density as well as size.",
                      "Memory figures in JSON are Python allocations for BFS and database page footprint for SQL. SQL process and query working memory were not measured.",
                      "The local SQL Server run used an explicit database-only SELECT identity after Windows authentication. Direct Windows reader login remains unverified.",
                      "", f"[Raw results]({json_name}) · [Latency plot]({image_name})", ""])


def timing(cases, backend, mode):
    samples = [value for case in cases for value in case["backends"][backend][mode]["samples_ms"]]
    return "—" if not samples else f"{percentile(samples, .5):.3f}/{percentile(samples, .95):.3f}"


def plot(report, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(report["scales"]), figsize=(4.5 * len(report["scales"]), 4.5),
                             squeeze=False, layout="constrained")
    for ax, (size, frame) in zip(axes.flat, report["scales"].items()):
        backends = present_backends(report)
        for i, backend in enumerate(backends):
            offset = (i - (len(backends) - 1) / 2) * .14
            samples = [(case["hops"] if case["reachable"] else 13,
                        case["backends"][backend]["dag"]["p50"]) for case in frame["cases"]
                       if case["operation"] == "shortest_path"
                       and case["backends"].get(backend, {}).get("status") == "correct"]
            if samples:
                ax.scatter([hops + offset for hops, _ in samples], [value for _, value in samples],
                           label=backend, alpha=.65)
        ax.set(title=f"{size} people", xlabel="Shortest path hops (U = unreachable)", ylabel="Typed DAG p50 (ms)",
               yscale="log")
        ax.set_xticks([0, 1, 2, 4, 6, 8, 10, 12, 13], ["0", "1", "2", "4", "6", "8", "10", "12", "U"])
        ax.grid(alpha=.25)
    axes.flat[0].legend(frameon=False)
    fig.suptitle("LDBC shortest paths | completed cases")
    fig.savefig(output, dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json", type=Path)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    report = json.loads(args.json.read_text(encoding="utf-8"))
    args.markdown.write_text(summarize(report, args.json.name, args.image.name), encoding="utf-8")
    plot(report, args.image)


if __name__ == "__main__":
    main()
