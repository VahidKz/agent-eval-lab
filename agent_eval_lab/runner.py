"""Paired trial orchestration, provenance, and honest quality/cost aggregation."""

from __future__ import annotations

import hashlib
import math
import random
import statistics
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from .agents import POLICY, ROUTER_VERSION, DemoAgent, route
from .domain import Sandbox, Task
from .grading import grade
from .providers import ResponsesAgent


def estimate_cost(execution, spec: dict | None) -> float | None:
    if not spec or not execution.usage_complete:
        return None
    input_price = spec.get("input_per_million")
    output_price = spec.get("output_per_million")
    if input_price is None or output_price is None:
        return None
    cached_price = spec.get("cached_input_per_million")
    if cached_price is None:
        cached_price = input_price
    return (
        (execution.input_tokens - execution.cached_input_tokens) * input_price
        + execution.cached_input_tokens * cached_price
        + execution.output_tokens * output_price
    ) / 1_000_000


def wilson_interval(passes: int, total: int) -> list[float]:
    """Descriptive 95% interval over trials; not a claim of independent task coverage."""
    z = 1.96
    p = passes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return [max(0, centre - margin), min(1, centre + margin)]


def summarize(results: list[dict]) -> dict:
    groups = defaultdict(list)
    for result in results:
        groups[result["strategy"]].append(result)
    summary = {}
    for strategy, rows in groups.items():
        passes = sum(row["passed"] for row in rows)
        latencies = sorted(row["latency_ms"] for row in rows)
        categories = sorted({row["category"] for row in rows})
        costs_known = all(row["estimated_cost_usd"] is not None for row in rows)
        cases = defaultdict(list)
        for row in rows:
            cases[row["case_id"]].append(row["passed"])
        summary[strategy] = {
            "trials": len(rows),
            "unique_cases": len(cases),
            "passed": passes,
            "pass_rate": passes / len(rows),
            "pass_rate_ci95": wilson_interval(passes, len(rows)),
            "all_trials_passed_cases": sum(all(values) for values in cases.values()),
            "unsafe_trials": sum(not row["checks"]["safe_tool_use"] for row in rows),
            "execution_errors": sum(row["error"] is not None for row in rows),
            "latency_p50_ms": statistics.median(latencies),
            "latency_p95_ms": latencies[math.ceil(0.95 * len(latencies)) - 1],
            "input_tokens": sum(row["input_tokens"] for row in rows),
            "output_tokens": sum(row["output_tokens"] for row in rows),
            "usage_coverage": sum(row["usage_complete"] for row in rows) / len(rows),
            "estimated_cost_usd": (
                sum(row["estimated_cost_usd"] for row in rows) if costs_known else None
            ),
            "cost_coverage": sum(row["estimated_cost_usd"] is not None for row in rows) / len(rows),
            "categories": {
                category: {
                    "passed": sum(row["passed"] for row in rows if row["category"] == category),
                    "trials": sum(row["category"] == category for row in rows),
                }
                for category in categories
            },
        }
    return summary


def run_suite(
    *,
    cases: list[dict],
    suite_name: str,
    suite_hash: str,
    split: str,
    mode: str = "demo",
    strategies: tuple[str, ...] = ("baseline", "candidate", "routed"),
    trials: int = 3,
    seed: int = 42,
    max_steps: int = 8,
    models: dict | None = None,
    api_key: str = "",
    base_url: str = "https://api.openai.com/v1",
    timeout: float = 45,
    progress=None,
) -> dict:
    jobs = [
        (case, trial, strategy)
        for case in cases
        for trial in range(1, trials + 1)
        for strategy in strategies
    ]
    random.Random(seed).shuffle(jobs)
    results = []
    for index, (case, trial, strategy) in enumerate(jobs, 1):
        route_start = time.perf_counter()
        selected, reason = route(case["request"]) if strategy == "routed" else (strategy, "fixed")
        routing_ms = (time.perf_counter() - route_start) * 1000
        sandbox = Sandbox(case["customer_id"], case["orders"], case.get("faults"))
        task = Task(case["customer_id"], case["request"])
        spec = models[selected] if models else None
        agent = (
            DemoAgent(guarded=selected == "candidate")
            if mode == "demo"
            else ResponsesAgent(spec["model"], api_key, base_url, timeout)
        )
        started = time.perf_counter()
        execution = agent.run(task, sandbox, max_steps)
        latency = (time.perf_counter() - started) * 1000 + routing_ms
        grading = grade(case, execution, sandbox, max_steps)
        result = {
            "case_id": case["id"],
            "category": case["category"],
            "trial": trial,
            "strategy": strategy,
            "selected_agent": selected,
            "model": spec["model"] if spec else f"scripted-{selected}",
            "routing_reason": reason,
            "routing_ms": round(routing_ms, 4),
            "request": task.request,
            "expected": case["expected"],
            "final": execution.final,
            "final_state": sandbox.state(),
            "trace": sandbox.trace,
            "error": execution.error,
            "latency_ms": round(latency, 4),
            "input_tokens": execution.input_tokens if mode == "live" else None,
            "output_tokens": execution.output_tokens if mode == "live" else None,
            "cached_input_tokens": execution.cached_input_tokens if mode == "live" else None,
            "usage_complete": execution.usage_complete if mode == "live" else False,
            "estimated_cost_usd": estimate_cost(execution, spec),
            "api_calls": execution.api_calls,
            **grading,
        }
        results.append(result)
        if progress:
            progress(index, len(jobs), result)
    # None means unmeasured, never zero-cost/zero-token model inference.
    aggregate_rows = [
        {
            **row,
            "input_tokens": row["input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
        }
        for row in results
    ]
    summary = summarize(aggregate_rows)
    if mode == "demo":
        for group in summary.values():
            group["input_tokens"] = None
            group["output_tokens"] = None
    return {
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "suite": suite_name,
        "suite_sha256": suite_hash,
        "prompt_sha256": hashlib.sha256(POLICY.encode()).hexdigest(),
        "router_version": ROUTER_VERSION,
        "split": split,
        "trials_per_case": trials,
        "seed": seed,
        "max_steps": max_steps,
        "model_config": models,
        "summary": summary,
        "results": results,
    }
