from __future__ import annotations

import json
import os
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from agent_eval_lab.domain import Sandbox, Task
from agent_eval_lab.providers import (
    ProviderError,
    ResponsesAgent,
    credentials,
    load_models,
    post_response,
    validate_base_url,
)

from .test_evaluations import order


def response(output, **changes):
    return {
        "status": "completed",
        "output": output,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 10},
        },
        **changes,
    }


def call(name, arguments, identifier="call-1"):
    return {
        "type": "function_call",
        "call_id": identifier,
        "name": name,
        "arguments": json.dumps(arguments),
    }


def final(decision="refunded"):
    return {
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "output_text", "text": json.dumps({"decision": decision, "message": "Done"})}
        ],
    }


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.agent = ResponsesAgent("fixture-model", "test-token", "https://api.example.test/v1")
        self.task = Task("customer-1", "Refund ORD-1001")
        self.sandbox = Sandbox("customer-1", [order()])

    def test_multi_turn_tools_and_encrypted_reasoning_are_replayed(self):
        reasoning = {"type": "reasoning", "id": "reason-1", "encrypted_content": "opaque"}
        replies = [
            response([reasoning, call("get_order", {"order_id": "ORD-1001"})]),
            response([call("refund_order", {"order_id": "ORD-1001"}, "call-2")]),
            response([final()]),
        ]
        payloads = []

        def fake_post(payload, *args):
            payloads.append(json.loads(json.dumps(payload)))
            return replies.pop(0)

        with patch("agent_eval_lab.providers.post_response", side_effect=fake_post):
            execution = self.agent.run(self.task, self.sandbox)
        self.assertIsNone(execution.error)
        self.assertEqual(self.sandbox.refund_events, ["ORD-1001"])
        self.assertEqual(execution.input_tokens, 300)
        self.assertEqual(execution.cached_input_tokens, 30)
        self.assertEqual(execution.api_calls, 3)
        self.assertIn(reasoning, payloads[1]["input"])
        self.assertEqual(payloads[0]["input"], [{"role": "user", "content": self.task.request}])
        for payload in payloads:
            self.assertFalse(payload["store"])
            self.assertNotIn("expected", payload)
            self.assertIn("reasoning.encrypted_content", payload["include"])
        self.assertTrue(
            any(item.get("type") == "function_call_output" for item in payloads[1]["input"])
        )

    def test_tool_loop_is_bounded(self):
        with patch(
            "agent_eval_lab.providers.post_response",
            return_value=response([call("get_order", {"order_id": "ORD-1001"})]),
        ):
            execution = self.agent.run(self.task, self.sandbox, max_steps=2)
        self.assertIsNotNone(execution.error)
        self.assertEqual(len(self.sandbox.trace), 2)
        self.assertEqual(execution.api_calls, 3)

    def test_provider_failure_is_reported_without_invented_usage(self):
        with patch("agent_eval_lab.providers.post_response", side_effect=ProviderError("HTTP 429")):
            execution = self.agent.run(self.task, self.sandbox)
        self.assertEqual(execution.error, "HTTP 429")
        self.assertFalse(execution.usage_complete)

    def test_missing_usage_is_not_treated_as_measured_zero(self):
        with patch(
            "agent_eval_lab.providers.post_response", return_value=response([final()], usage=None)
        ):
            execution = self.agent.run(self.task, self.sandbox)
        self.assertFalse(execution.usage_complete)

    def test_malformed_final_json_fails(self):
        reply = response(
            [{"type": "message", "content": [{"type": "output_text", "text": "Refund completed!"}]}]
        )
        with patch("agent_eval_lab.providers.post_response", return_value=reply):
            execution = self.agent.run(self.task, self.sandbox)
        self.assertIsNotNone(execution.error)

    def test_incomplete_response_fails(self):
        with patch(
            "agent_eval_lab.providers.post_response",
            return_value=response([final()], status="incomplete"),
        ):
            self.assertIsNotNone(self.agent.run(self.task, self.sandbox).error)

    def test_malformed_provider_message_fails_without_aborting_a_run(self):
        for content in [None, "text", [None], [{"type": "output_text", "text": 123}]]:
            with (
                self.subTest(content=content),
                patch(
                    "agent_eval_lab.providers.post_response",
                    return_value=response([{"type": "message", "content": content}]),
                ),
            ):
                self.assertIsNotNone(self.agent.run(self.task, self.sandbox).error)

    def test_http_and_embedded_credentials_are_rejected(self):
        for url in [
            "http://api.example.test/v1",
            "https://user:password@example.test/v1",
            "https://example.test/v1?key=secret",
            "file:///tmp/model",
            "",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_base_url(url)
        self.assertEqual(validate_base_url("http://127.0.0.1:1234/v1/"), "http://127.0.0.1:1234/v1")

    def test_missing_credentials_fail_before_network_calls(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"),
        ):
            credentials()

    def test_placeholder_models_and_invalid_rates_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            for models in [
                {"baseline": {"model": "YOUR_MODEL"}, "candidate": {"model": "test"}},
                {
                    "baseline": {"model": "test", "input_per_million": -1},
                    "candidate": {"model": "test"},
                },
                {
                    "baseline": {"model": "test", "api_key": "never-write-this-to-a-report"},
                    "candidate": {"model": "test"},
                },
            ]:
                path.write_text(json.dumps(models))
                with self.assertRaises(ValueError):
                    load_models(path)

    def test_real_http_transport_and_redirect_protection(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                if self.path == "/redirect/responses":
                    self.send_response(302)
                    self.send_header("Location", "/stolen")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response([final()])).encode())

            def do_GET(self):
                requests.append("redirect-followed")
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            self.assertEqual(
                post_response({"model": "fixture"}, "test-only", base, 2)["status"], "completed"
            )
            with self.assertRaisesRegex(ProviderError, "HTTP 302"):
                post_response({"model": "fixture"}, "test-only", base + "/redirect", 2)
            self.assertNotIn("redirect-followed", requests)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
