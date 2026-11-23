"""Publish a compact, dataset-free summary of the intelligent-routing report."""
import argparse
from pathlib import Path

from benchmarks.intelligent_routing import read, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("summary", type=Path)
    args = parser.parse_args()
    report = read(args.report)
    keys = ("manifest_sha256", "model", "grounding", "authentication", "remote_available", "summary", "routing")
    compact = {key: report[key] for key in keys}
    compact["reference_plan_control"] = {key: report["reference_plan_control"][key]
                                         for key in ("summary", "routing")}
    write(args.summary, compact)


if __name__ == "__main__":
    main()
