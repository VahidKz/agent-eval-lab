"""Optional live agent via the Responses API. No third-party runtime dependencies."""

from __future__ import annotations

import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .agents import POLICY, Execution
from .domain import Sandbox, Task


def function(name: str, description: str, field: str) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {field: {"type": "string"}},
            "required": [field],
            "additionalProperties": False,
        },
    }


TOOLS = [
    function("get_order", "Read an order visible to the authenticated customer.", "order_id"),
    function("refund_order", "Refund an inspected order, subject to store policy.", "order_id"),
    function("escalate", "Create a ticket for human review.", "reason"),
]


class ProviderError(Exception):
    """An error message safe to put in a local report without disclosing credentials."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the Authorization header to a redirected destination.
        return None


def validate_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    loopback = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or (parts.scheme != "https" and not (parts.scheme == "http" and loopback))
    ):
        raise ValueError("API base URL must use HTTPS (HTTP allowed only on loopback)")
    return base_url.rstrip("/")


def post_response(payload: dict, api_key: str, base_url: str, timeout: float) -> dict:
    request = Request(
        validate_base_url(base_url) + "/responses",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            data = response.read(4_000_001)
    except HTTPError as exc:
        raise ProviderError(
            f"Provider HTTP {exc.code}; inspect credentials/limits locally"
        ) from None
    except (URLError, TimeoutError, OSError):
        raise ProviderError("Provider connection failed or timed out") from None
    if len(data) > 4_000_000:
        raise ProviderError("Provider response exceeded the 4 MB limit")
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError):
        raise ProviderError("Provider returned invalid JSON") from None
    if not isinstance(result, dict):
        raise ProviderError("Provider response must be a JSON object")
    return result


def load_models(path) -> dict:
    models = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(models, dict) or set(models) != {"baseline", "candidate"}:
        raise ValueError("Model config must contain baseline and candidate only")
    for alias, spec in models.items():
        if (
            not isinstance(spec, dict)
            or not isinstance(spec.get("model"), str)
            or not spec["model"].strip()
            or spec["model"].startswith("YOUR_")
        ):
            raise ValueError(f"Configure a real model ID for {alias}")
        allowed = {"model", "input_per_million", "output_per_million", "cached_input_per_million"}
        if set(spec) - allowed:
            raise ValueError(
                f"Unsupported model config fields for {alias}; keep credentials in env"
            )
        for key in ("input_per_million", "output_per_million", "cached_input_per_million"):
            value = spec.get(key)
            if value is not None and (
                type(value) not in {float, int} or not math.isfinite(value) or value < 0
            ):
                raise ValueError(f"{alias}.{key} must be a finite nonnegative number or null")
    return models


class ResponsesAgent:
    def __init__(self, model: str, api_key: str, base_url: str, timeout: float = 45):
        self.model = model
        self.api_key = api_key
        self.base_url = validate_base_url(base_url)
        self.timeout = timeout

    def run(self, task: Task, sandbox: Sandbox, max_steps: int = 8) -> Execution:
        execution = Execution()
        history: list[dict] = [{"role": "user", "content": task.request}]
        try:
            for _ in range(max_steps + 1):
                execution.api_calls += 1
                response = post_response(
                    {
                        "model": self.model,
                        "instructions": POLICY,
                        "input": history,
                        "tools": TOOLS,
                        "parallel_tool_calls": False,
                        "store": False,
                        "include": ["reasoning.encrypted_content"],
                        "max_output_tokens": 2048,
                    },
                    self.api_key,
                    self.base_url,
                    self.timeout,
                )
                self._accumulate_usage(execution, response.get("usage"))
                if response.get("status") not in {None, "completed"}:
                    raise ProviderError("Provider response was incomplete or unsuccessful")
                output = response.get("output")
                if not isinstance(output, list) or not all(
                    isinstance(item, dict) for item in output
                ):
                    raise ProviderError("Provider returned an invalid output array")
                # Replay output and encrypted reasoning for stateless continuation.
                history.extend(output)
                calls = [item for item in output if item.get("type") == "function_call"]
                if calls:
                    for item in calls:
                        if len(sandbox.trace) >= max_steps:
                            raise ProviderError("Agent exhausted its tool-call budget")
                        if not isinstance(item.get("call_id"), str):
                            raise ProviderError("Provider function call has no call_id")
                        try:
                            arguments = json.loads(item.get("arguments", ""))
                        except (ValueError, TypeError):
                            arguments = None
                        name = item.get("name")
                        result = sandbox.call(name if isinstance(name, str) else "", arguments)
                        history.append(
                            {
                                "type": "function_call_output",
                                "call_id": item["call_id"],
                                "output": json.dumps(result),
                            }
                        )
                    continue
                fragments = []
                for item in output:
                    if item.get("type") != "message":
                        continue
                    content = item.get("content")
                    if not isinstance(content, list):
                        raise ProviderError("Provider returned invalid message content")
                    for part in content:
                        if not isinstance(part, dict):
                            raise ProviderError("Provider returned an invalid message part")
                        if part.get("type") == "output_text":
                            if not isinstance(part.get("text"), str):
                                raise ProviderError("Provider output text must be a string")
                            fragments.append(part["text"])
                text = "".join(fragments)
                try:
                    execution.final = json.loads(text)
                except ValueError:
                    raise ProviderError("Agent final response was not a JSON object") from None
                return execution
            raise ProviderError("Agent exhausted its response budget")
        except ProviderError as exc:
            execution.error = str(exc)
            # An unsuccessful request might have incurred usage we cannot observe.
            execution.usage_complete = False
            return execution

    @staticmethod
    def _accumulate_usage(execution: Execution, usage) -> None:
        if not isinstance(usage, dict):
            execution.usage_complete = False
            return
        tokens = [usage.get("input_tokens"), usage.get("output_tokens")]
        details = usage.get("input_tokens_details") or {}
        cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
        if any(type(value) is not int or value < 0 for value in [*tokens, cached]):
            execution.usage_complete = False
            return
        if cached > tokens[0]:
            execution.usage_complete = False
            return
        execution.input_tokens += tokens[0]
        execution.output_tokens += tokens[1]
        execution.cached_input_tokens += cached


def credentials() -> tuple[str, str]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise ValueError("Set OPENAI_API_KEY before running live evaluations")
    base = validate_base_url(os.environ.get("EVAL_API_BASE_URL", "https://api.openai.com/v1"))
    return key, base
