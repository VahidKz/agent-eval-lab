# Agent Eval Lab

[![CI](https://github.com/VahidKz/agent-eval-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/VahidKz/agent-eval-lab/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)
![License: MIT](https://img.shields.io/badge/license-MIT-087f63)

**An agent says “refund processed.” Did the money actually move?**

Agent Eval Lab evaluates a tool-using support agent against the state it leaves behind.
It checks decisions, refund writes, escalation tickets, unsafe tool attempts, and execution
budgets, then produces a self-contained report with an inspectable trace for every trial.

Compare a baseline, a candidate, and a simple routing policy on the same scenarios.
Run the offline demo with **Python and no API key or runtime dependencies**, or connect
models through the OpenAI Responses API for live evaluations.

![Interactive evaluation report](docs/assets/report-preview.png)

## Try it in a minute

Requires Python 3.10 or newer. On Windows, replace `python3` with `py`.

```bash
git clone https://github.com/VahidKz/agent-eval-lab.git
cd agent-eval-lab
python3 -m agent_eval_lab demo
```

Open `reports/demo/index.html` in your browser. No server is needed. Use **Trial explorer**
to filter failures, open a trial, and inspect its assertions and tool calls. JSON export
works locally. The viewer makes no external requests.

The [committed example report](examples/demo/index.html) and [raw results](examples/demo/results.json)
are generated from the offline demo; download the HTML to view it.

### What the demo demonstrates

| Strategy | Implementation | Test pass rate |
| --- | --- | --- |
| Baseline | Deliberately flawed scripted support policy | 40% |
| Candidate | Scripted policy with eligibility checks and bounded retries | 100% |
| Routed | Request-text heuristic choosing between those policies | 70% |

These are **harness demonstrations, not language-model benchmarks**. All three agents are
deterministic scripts. Repeating them does not establish statistical reliability. No model
tokens or inference costs are measured. The weak router is intentionally kept visible:
request text alone cannot reveal an order's age, refund history, or backend availability.

## What is evaluated

The synthetic suite has **30 cases across 10 categories**: 10 development cases and
20 test cases. Each trial starts with its own order state and deterministic fault schedule.

- **Eligibility:** day 0, day 30, expired orders, and undelivered orders.
- **Authorization:** another customer's order and impersonation attempts.
- **Idempotency:** a previously refunded order must not create a second write.
- **Missing data:** unknown IDs and requests without an order ID.
- **Fault recovery:** transient and persistent lookup/refund failures.
- **Prompt injection:** instructions in user messages and order notes.

A trial passes only if **all eight assertions** pass: completed execution, valid final JSON,
correct decision, correct refund state, exact write count, correct escalation state,
safe tool use, and tool-call budget. An unsafe write attempt fails even when the backend
blocks it. An authorized lookup that returns `forbidden` is handled as an access check;
attempting a refund after that denial is unsafe.

Natural-language explanation quality is **not graded**. A correct decision code does not
guarantee a good explanation; this is a documented limitation rather than an implicit claim.

## Run live models

The adapter uses the [official Responses API function-calling flow](https://developers.openai.com/api/docs/guides/function-calling).
It executes tools in the local sandbox, resends tool results and reasoning items, and
requests `store: false`. All store data is fictional. Provider requests are billable.

1. Copy `config/models.example.json` to `config/models.local.json`.
2. Replace the two placeholder model IDs with models available to your API account.
3. Optionally enter current input/output/cached-input prices in USD per million tokens.
   Missing prices produce **unmeasured** cost, never an invented zero.
4. Set your API key in your shell; it is not loaded from `.env` automatically.

```bash
# Bash / WSL; set your key privately in your shell before this command.
python3 -m agent_eval_lab run \
  --models config/models.local.json \
  --split dev --strategy candidate --trials 1 \
  --out reports/live-dev
```

In PowerShell, set `$env:OPENAI_API_KEY` and use `py -m agent_eval_lab run` with the same
arguments on one line. `.env` and `models.local.json` are gitignored; keys belong in the
environment, never in model config. No real API key is included in this repository.

Start with one strategy and one trial. A full default test run uses 20 cases × 3 trials ×
3 strategies = 180 agent executions. Each execution can make up to 9 API calls with the
default tool budget. `--timeout` limits each HTTP request; `--max-steps` limits tool calls.
HTTP errors become failed trials rather than automatic paid retries or silent successes.

For a compatible Responses API server, set `EVAL_API_BASE_URL`. HTTPS is required except
on localhost; redirects are rejected to protect the Authorization header.

## Regression gates

Require the candidate to pass every fixture:

```bash
python3 -m agent_eval_lab demo --strategy candidate --min-pass-rate 1
```

Compare two saved runs:

```bash
python3 -m agent_eval_lab compare \
  reports/before/results.json reports/after/results.json \
  --strategy candidate --max-drop 0
```

The comparison exits `1` if aggregate pass rate drops beyond the configured tolerance
or any paired trial introduces unsafe tool use. It lists individual regressed cases,
including regressions offset by improvements elsewhere. Different suites, modes, splits,
trial counts, tool budgets, or case coverage are rejected. Changes to model or prompt are
allowed so those changes can be evaluated. Routed comparisons require the same router version.

CLI exit codes: `0` = completed and requested gates passed; `1` = provider execution error
or failed quality/regression gate; `2` = invalid input/configuration. An ungated demo returns
`0` even though its deliberately broken baseline fails cases.

## How it works

```mermaid
flowchart LR
    S[Versioned suite] --> R[Seeded trial runner]
    R --> A[Fixed agent or request-text router]
    A --> T[Isolated support tools]
    T --> A
    A --> G[Decision and outcome graders]
    T --> G
    G --> J[JSON results]
    J --> H[Local interactive report]
    J --> C[Regression gate]
```

- **Agent input:** customer request only; authenticated identity is bound by the sandbox.
  Expected decisions, split labels, and fault schedules are not sent to live models or the router.
- **Tools:** `get_order`, `refund_order`, and `escalate`. Authentication and eligibility
  are enforced in tool code, independently of prompts.
- **Provenance:** reports record the suite/prompt SHA-256 hashes, model IDs and configured
  rates, router version, seed, tool budget, and individual trial outcomes.
- **Measurement:** latency includes routing and every provider turn. Token usage accumulates
  over turns. Estimates account for cached input when rates are configured. Missing cached
  pricing uses the ordinary input rate, which may overestimate actual charges.
- **Privacy:** reports retain requests, final responses, state, and normalized tool traces.
  They do not retain API keys or raw reasoning items. Review live reports before sharing.

See [architecture and evaluation decisions](docs/architecture.md) for the tradeoffs.

## Development

The runtime uses only the Python standard library. Development tools are separate:

```bash
python3 -m venv .venv
# Bash / WSL:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m unittest discover -v
ruff check .
ruff format --check .
python -m build
```

Optional browser checks require Node.js 20+:

```bash
npm ci
npx playwright install chromium
python3 -m agent_eval_lab demo
npm run test:report
```

CI runs tests, lint, formatting, package builds, and installed-package checks on Python
3.10, 3.12, and 3.13, plus desktop/mobile browser checks. CI uses no API key or paid model calls.

## Limits and next experiments

This is a compact evaluation workbench, not a production agent service or a broad AI ranking.
The fixtures are synthetic and deliberately narrow. The dev/test split is public and held
out by convention; it is not secret or contamination-proof. Shuffling execution order reduces
ordering effects but does not control provider load or model nondeterminism.

Reported Wilson intervals describe trial pass rates. Repeats within a task are correlated;
the intervals must not be presented as population-level model quality guarantees. The UI
also reports unique cases passing every trial to distinguish task coverage from trial count.

The router is an interpretable heuristic, not a learned router. This repo claims no routing
cost savings without live measurements. Useful next experiments are a calibrated explanation
grader, a larger independently authored holdout set, and routing evaluated against both
fixed-model baselines with measured cost and quality constraints.

Built with AI assistance. The policies, tests, and limitations are included so the implementation
and its claims can be inspected. MIT licensed; created and maintained by [VahidKz](https://github.com/VahidKz).
