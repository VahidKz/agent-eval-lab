"""Self-contained reports that work locally without a server or third-party scripts."""

from __future__ import annotations

import json
from pathlib import Path


def render_html(report: dict) -> str:
    template = (Path(__file__).parent / "templates" / "report.html").read_text(encoding="utf-8")
    # JSON is inert script data, but a model could still emit a closing script tag.
    # Escape HTML-significant characters before embedding any untrusted text.
    data = (
        json.dumps(report, ensure_ascii=True, allow_nan=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return template.replace("__REPORT_JSON__", data)


def write_report(report: dict, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, html_path = directory / "results.json", directory / "index.html"
    json_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    return json_path, html_path


def compare_reports(baseline: dict, candidate: dict, strategy: str, max_drop: float = 0) -> dict:
    """Compare paired cases, failing closed when provenance/coverage differs."""
    fields = ("schema_version", "mode", "suite_sha256", "split", "trials_per_case", "max_steps")
    for field in fields:
        if baseline.get(field) != candidate.get(field):
            raise ValueError(f"Reports cannot be compared: {field} differs")
    if strategy == "routed" and baseline.get("router_version") != candidate.get("router_version"):
        raise ValueError("Routed comparisons require the same router version")
    left_rows = [row for row in baseline["results"] if row["strategy"] == strategy]
    right_rows = [row for row in candidate["results"] if row["strategy"] == strategy]
    left = {(row["case_id"], row["trial"]): row for row in left_rows}
    right = {(row["case_id"], row["trial"]): row for row in right_rows}
    if len(left) != len(left_rows) or len(right) != len(right_rows):
        raise ValueError("Reports contain duplicate case/trial pairs")
    if not left or left.keys() != right.keys():
        raise ValueError("Reports must contain the same case/trial pairs for the chosen strategy")
    old_rate = sum(row["passed"] for row in left.values()) / len(left)
    new_rate = sum(row["passed"] for row in right.values()) / len(right)
    regressions = sorted(
        {key[0] for key in left if left[key]["passed"] and not right[key]["passed"]}
    )
    safety_regressions = sorted(
        {
            key[0]
            for key in left
            if left[key]["checks"]["safe_tool_use"] and not right[key]["checks"]["safe_tool_use"]
        }
    )
    return {
        "passed": new_rate + 1e-12 >= old_rate - max_drop and not safety_regressions,
        "strategy": strategy,
        "baseline_pass_rate": old_rate,
        "candidate_pass_rate": new_rate,
        "delta": new_rate - old_rate,
        "regressed_cases": regressions,
        "safety_regressions": safety_regressions,
    }
