# Design decisions

## Grade outcomes before presentation

The environment is a fictional order/refund system because successful actions have
verifiable effects. An agent that emits the expected word without issuing a refund
must fail. The final JSON decision, refund state, write count, and escalation state
are separate assertions, so failures are attributable.

Tool errors such as `policy_denied` and `inspection_required` are unsafe attempted
behavior, even if the backend prevents the write. A `forbidden` lookup is expected
when a user supplies another customer's ID; a forbidden refund attempt fails safety.

Backend policy and agent behavior are deliberately independent. The sandbox enforces
authorization and eligibility; the grader still inspects what the agent tried to do.

## Keep the experiment inspectable

Each trial gets a deep copy of the initial orders and tool fault counters. Agents receive
only the user request and tool outputs. Expected outcomes stay in the grading process.
Paired strategies use the same cases and number of repeats. Seeded shuffling changes
execution order, not model randomness. No seed is claimed to make a remote model deterministic.

The versioned suite includes 10 development cases and 20 test cases. The split is a
workflow convention in a public repository. The router was not trained on test labels;
it is a fixed list of visible request markers. Test cases are small and cannot establish
general model performance, security robustness, or production readiness.

## Demonstrate a real failure, not a fabricated benchmark

The offline baseline intentionally reports refund success after rejected writes. It also
mishandles previously refunded orders and transient failures. The candidate uses tools
correctly under the documented policy. Both are deterministic code, not LLM simulations
whose scores could be mistaken for measured model ability.

The routed demo scores below the candidate because its cheap text heuristic cannot see
hidden business state before routing. This is an observable limitation and a starting
point for experiments, rather than evidence that routing always saves money.

## Separate cost observations from billing

Live usage is accumulated from each provider response, including input-cache counts.
Estimated cost uses configured rates, not hardcoded prices. A missing rate or incomplete
usage makes the trial cost unknown. Aggregate cost is unknown if any trial cost is unknown;
coverage fields expose missing measurements. Configured cached prices default to the
ordinary input rate when absent, which is conservative rather than billing-accurate.

There is a per-request timeout, tool budget, response-token cap, and response-size limit.
Provider failures are recorded; the transport does not automatically retry paid requests.
These are bounded-run controls, not a dollar-denominated spending guarantee.

## Use a local, portable report

One HTML artifact embeds the result JSON. It opens from disk, uses no CDN or external
scripts, and supports filtering, lazy trace inspection, and JSON download. Embedded JSON
escapes HTML delimiters; dynamic model text is rendered using `textContent`. A restrictive
content security policy prevents network requests and external resources.

No database, orchestration framework, or web server is needed for this small workbench.
The dependency-free runtime keeps it easy to review and run. Playwright and Python
lint/build tools are development-only dependencies.

## Regressions and provenance

Reports include prompt/dataset hashes, model IDs, configured rates, router version,
split, repeats, and tool budget. Regression comparisons reject incompatible coverage.
Prompt and model changes can be compared intentionally. The default aggregate quality
tolerance is zero, while any paired safety regression blocks regardless of tolerance.
Individual case losses are listed even when improvements keep the aggregate unchanged.

The JSON schema is versioned but not cryptographically signed. Reports are local
experiment artifacts; users can edit them. Do not treat report files as trusted evidence
from an independent benchmark authority.

## Unimplemented dimensions

The grader verifies structured decisions and state, not prose accuracy, empathy, or
explanation quality. Multi-customer conversations, real payment systems, concurrent
writes, trained routing, model-as-judge calibration, and adversarial dataset generation
are outside the first version. The live adapter has mocked protocol and HTTP tests;
actual account/model compatibility must be established with a live run.
