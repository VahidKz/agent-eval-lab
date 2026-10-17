"""Load and validate a versioned evaluation dataset before any paid API calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

DEFAULT_SUITE = Path(__file__).parent / "data" / "support.json"
DECISIONS = {"refunded", "denied", "already_refunded", "not_found", "escalated", "needs_order"}


def load_suite(path: Path = DEFAULT_SUITE, split: str = "test") -> tuple[dict, list[dict], str]:
    raw = path.read_bytes()
    suite = json.loads(raw)
    if (
        not isinstance(suite, dict)
        or suite.get("schema_version") != 1
        or not isinstance(suite.get("cases"), list)
        or not isinstance(suite.get("name"), str)
        or not suite["name"]
    ):
        raise ValueError("Suite must have schema_version=1 and a cases array")
    seen: set[str] = set()
    for case in suite["cases"]:
        required = {"id", "split", "category", "customer_id", "request", "orders", "expected"}
        if not isinstance(case, dict) or not required.issubset(case):
            raise ValueError("A case is missing required fields")
        if not isinstance(case["id"], str) or case["id"] in seen:
            raise ValueError("Case IDs must be unique strings")
        seen.add(case["id"])
        if case["split"] not in {"dev", "test"}:
            raise ValueError(f"Invalid split in {case['id']}")
        for key in ("category", "customer_id", "request"):
            if not isinstance(case[key], str) or not case[key]:
                raise ValueError(f"Invalid {key} in {case['id']}")
        expected = case["expected"]
        if (
            not isinstance(expected, dict)
            or not isinstance(expected.get("decision"), str)
            or expected["decision"] not in DECISIONS
        ):
            raise ValueError(f"Invalid expected decision in {case['id']}")
        if not isinstance(expected.get("refunded"), list) or not all(
            isinstance(item, str) for item in expected["refunded"]
        ):
            raise ValueError(f"Invalid expected refunds in {case['id']}")
        for key in ("refund_events", "escalations"):
            if type(expected.get(key)) is not int or expected[key] < 0:
                raise ValueError(f"Invalid expected {key} in {case['id']}")
        if not isinstance(case["orders"], list):
            raise ValueError(f"Invalid orders in {case['id']}")
        order_ids: set[str] = set()
        for order in case["orders"]:
            fields = {"id", "customer_id", "age_days", "status", "amount_cents", "refunded"}
            if not isinstance(order, dict) or not fields <= set(order):
                raise ValueError(f"Incomplete order in {case['id']}")
            if not isinstance(order["id"], str) or order["id"] in order_ids:
                raise ValueError(f"Duplicate/invalid order ID in {case['id']}")
            order_ids.add(order["id"])
            if not isinstance(order["customer_id"], str) or not order["customer_id"]:
                raise ValueError(f"Invalid order customer in {case['id']}")
            if not isinstance(order["status"], str) or not order["status"]:
                raise ValueError(f"Invalid order status in {case['id']}")
            if type(order["age_days"]) is not int or order["age_days"] < 0:
                raise ValueError(f"Invalid order age in {case['id']}")
            if type(order["amount_cents"]) is not int or order["amount_cents"] < 0:
                raise ValueError(f"Invalid order amount in {case['id']}")
            if type(order["refunded"]) is not bool:
                raise ValueError(f"Invalid refunded flag in {case['id']}")
        faults = case.get("faults", {})
        if not isinstance(faults, dict) or any(
            key not in {"get_order", "refund_order", "escalate"}
            or type(value) is not int
            or value < 0
            for key, value in faults.items()
        ):
            raise ValueError(f"Invalid faults in {case['id']}")
    cases = [case for case in suite["cases"] if split == "all" or case["split"] == split]
    if not cases:
        raise ValueError(f"Suite has no cases for split {split!r}")
    return suite, cases, hashlib.sha256(raw).hexdigest()
