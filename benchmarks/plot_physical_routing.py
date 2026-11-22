"""Render measured physical-routing latency and selection accuracy."""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    cases = report["cases"]
    names = ("sqlite", "sqlserver", "routed", "oracle")
    labels = ("Fixed SQLite", "Fixed SQL Server", "Routed", "Oracle")
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), layout="constrained")
    for op, ax in zip(report["manifest"]["sealed_test"]["families"], axes.flat):
        group = [case for case in cases if case["operation"] == op]
        for arm, label in zip(names, labels):
            x = [case["scale"] * (0.89 if i % 2 == 0 else 1.12) for i, case in enumerate(group)]
            y = [case["arms"][arm]["p50"] for case in group]
            ax.scatter(x, y, label=label, marker="x" if arm == "oracle" else "o", alpha=.8)
        configure(ax, report, op)
    ax = axes[1, 1]
    ax.axis("off")
    handles, legend_labels = axes[0, 0].get_legend_handles_labels()
    ax.legend(handles, legend_labels, loc="center", frameon=False)
    fig.suptitle("Held-out physical SQL routing | Chinook")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=160)
    plt.close(fig)


def configure(ax, report, operation):
    stats = report["summary"][f"operation:{operation}"]
    ax.set_title(f"{operation.replace('_', ' ').title()} | {stats['routing_accuracy']:.0%} oracle agreement")
    ax.set(xscale="log", yscale="log", xlabel="Scale (narrower window left, broader right)", ylabel="DAG p50 (ms)")
    scales = report["manifest"]["sealed_test"]["scales"]
    ax.set_xticks(scales, [f"{factor}x" for factor in scales])
    ax.grid(alpha=.2)


if __name__ == "__main__":
    main()
