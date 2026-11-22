"""Render fixed-engine Chinook benchmark measurements."""

import argparse
import json
from pathlib import Path


def plot(source, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    report = json.loads(Path(source).read_text(encoding="utf-8"))
    families = ("filtered", "grouped", "joined", "high_cardinality")
    scales = sorted(report["scales"], key=int)
    colors = {"sqlite": "#0072B2", "sqlserver": "#D55E00"}
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), layout="constrained")
    for axis, family in zip(axes.flat, families):
        cases = [next(case for case in report["scales"][scale]["cases"]
                      if case["operation"] == family and case["selectivity"] == "broad") for scale in scales]
        for backend, color in colors.items():
            if any(backend not in case["modes"]["query"]["backends"] for case in cases):
                continue
            for mode, style in (("query", "-"), ("dag", "--")):
                medians = [case["modes"][mode]["backends"][backend]["p50"] for case in cases]
                axis.plot([int(scale) for scale in scales], medians, style, color=color, marker="o",
                          label=f"{backend} / {mode}")
        axis.set(xscale="log", yscale="log", title=family.replace("_", " ").title(),
                 xlabel="Chinook scale", ylabel="Median latency (ms)")
        axis.set_xticks([int(scale) for scale in scales], [f"{scale}×" for scale in scales])
        axis.grid(alpha=.25)
    axes[0, 0].legend(frameon=False, fontsize=8)
    fig.suptitle("Chinook analytical workloads, broad invoice range")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    plot(args.input, args.output)
