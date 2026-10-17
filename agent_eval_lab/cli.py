"""CLI for offline demos, live evaluation, and regression gates."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .providers import credentials, load_models
from .reporting import compare_reports, write_report
from .runner import run_suite
from .suite import DEFAULT_SUITE, load_suite


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be a positive integer")
    return number


def fraction(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("Must be between 0 and 1")
    return number


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Outcome-first evaluations for tool-using agents")
    commands = cli.add_subparsers(dest="command", required=True)
    for name in ("demo", "run"):
        command = commands.add_parser(
            name,
            help="Offline scripted demo"
            if name == "demo"
            else "Live Responses API evaluation (billable)",
        )
        command.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
        command.add_argument("--split", choices=("dev", "test", "all"), default="test")
        command.add_argument("--trials", type=positive_int, default=3)
        command.add_argument("--seed", type=int, default=42)
        command.add_argument("--max-steps", type=positive_int, default=8)
        command.add_argument(
            "--strategy", choices=("baseline", "candidate", "routed", "all"), default="all"
        )
        command.add_argument("--out", type=Path, default=Path("reports") / name)
        command.add_argument(
            "--min-pass-rate",
            type=fraction,
            help="Exit 1 if any selected strategy is below this threshold",
        )
        if name == "run":
            command.add_argument("--models", type=Path, required=True)
            command.add_argument("--timeout", type=positive_int, default=45)
    compare = commands.add_parser("compare", help="Compare reports; exit 1 on regression")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument(
        "--strategy", choices=("baseline", "candidate", "routed"), default="candidate"
    )
    compare.add_argument("--max-drop", type=fraction, default=0)
    return cli


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.command == "compare":
            result = compare_reports(
                json.loads(args.baseline.read_text(encoding="utf-8")),
                json.loads(args.candidate.read_text(encoding="utf-8")),
                args.strategy,
                args.max_drop,
            )
            print(json.dumps(result, indent=2))
            return 0 if result["passed"] else 1
        suite, cases, digest = load_suite(args.suite, args.split)
        strategies = (
            ("baseline", "candidate", "routed") if args.strategy == "all" else (args.strategy,)
        )
        live = args.command == "run"
        models = load_models(args.models) if live else None
        api_key, base_url = credentials() if live else ("", "https://api.openai.com/v1")
        if live:
            print(
                f"LIVE / billable: {len(cases)} cases × {args.trials} trials × "
                f"{len(strategies)} strategies. Up to {args.max_steps + 1} API calls per trial.",
                file=sys.stderr,
            )
        else:
            print("OFFLINE DEMO / scripted policies. No model quality, token or cost claims.")

        def progress(index, total, result):
            if live or index == total:
                print(
                    f"[{index}/{total}] {result['strategy']} {result['case_id']} "
                    f"{'PASS' if result['passed'] else 'FAIL'}",
                    file=sys.stderr,
                )

        report = run_suite(
            cases=cases,
            suite_name=suite["name"],
            suite_hash=digest,
            split=args.split,
            mode="live" if live else "demo",
            strategies=strategies,
            trials=args.trials,
            seed=args.seed,
            max_steps=args.max_steps,
            models=models,
            api_key=api_key,
            base_url=base_url,
            timeout=args.timeout if live else 45,
            progress=progress,
        )
        _, html_path = write_report(report, args.out)
        print(f"\n{'Strategy':<14} {'Passed':<12} {'Rate':<10} {'Unsafe':<9} {'Cost (est.)'}")
        for name, group in sorted(report["summary"].items()):
            cost = group["estimated_cost_usd"]
            print(
                f"{name:<14} {str(group['passed']) + '/' + str(group['trials']):<12} "
                f"{group['pass_rate']:<10.1%} {group['unsafe_trials']:<9} "
                f"{'unmeasured' if cost is None else f'${cost:.6f}'}"
            )
        print(f"\nReport: {html_path.resolve()}")
        print(f"Results: {(args.out / 'results.json').resolve()}")
        if any(group["execution_errors"] for group in report["summary"].values()):
            return 1
        if args.min_pass_rate is not None and any(
            group["pass_rate"] < args.min_pass_rate for group in report["summary"].values()
        ):
            return 1
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
