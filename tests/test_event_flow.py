# -*- coding: utf-8 -*-
"""就诊的事件链路：提案 → 审批 → 扣款 → 回执。"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

import main as M  # noqa: E402
from catalog import HealthState  # noqa: E402


class FakeCompanion:
    def __init__(self, decision=None, charge_result=None, with_receipt=True):
        self.decision = decision or {"approved": True, "decision": "allow",
                                     "reason": "心流判定通过"}
        self.charge_result = charge_result or {"ok": True, "code": "ok", "balance": 900}
        self.with_receipt = with_receipt
        self.proposals = []
        self.charged = []
        self.receipts = []

    def submit_event(self, raw):
        self.proposals.append(raw)
        return dict(self.decision)

    async def charge(self, amount, **kwargs):
        self.charged.append((amount, kwargs))
        return dict(self.charge_result)

    def event_receipt(self, raw):
        if not self.with_receipt:
            raise AttributeError("no receipt")
        self.receipts.append(raw)
        return {"accepted": True, "ack": True}


class LegacyCompanion:
    """老终端：只有 charge，没有事件总线。"""

    def __init__(self):
        self.charged = []

    async def charge(self, amount, **kwargs):
        self.charged.append((amount, kwargs))
        return {"ok": True, "code": "ok", "balance": 900}


def build(companion, config=None):
    plugin = object.__new__(M.AmoMedicalPlugin)
    plugin.config = dict(config or {})
    plugin._health = HealthState(status="阑尾炎", since=1.0, until=10 ** 12,
                                 note="右下腹疼", illness="appendix", treated=False)
    plugin._history = []
    plugin._rng = random.Random(7)
    plugin._save = lambda: None
    plugin._companion = lambda: companion
    plugin._companion_balance = lambda: 10 ** 6
    return plugin


class EventFlowTests(unittest.TestCase):
    def test_allow_charges_and_receipts(self):
        companion = FakeCompanion()
        result = asyncio.run(build(companion).treat(auto=False))
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(companion.charged), 1)
        self.assertEqual(companion.receipts[0]["status"], "success")
        self.assertEqual(companion.receipts[0]["amount"], companion.charged[0][0])

    def test_proposal_payload(self):
        companion = FakeCompanion()
        asyncio.run(build(companion).treat(auto=False))
        proposal = companion.proposals[0]
        self.assertEqual(proposal["event_type"], "medical_treat")
        self.assertEqual(proposal["source"], "medical")
        self.assertEqual(proposal["scene"], "private")
        self.assertTrue(proposal["event_id"].startswith("med-"))
        self.assertGreater(proposal["payload"]["cost"], 0)

    def test_deny_skips_charge(self):
        companion = FakeCompanion(decision={"approved": False, "decision": "defer",
                                            "reason": "群聊资金类行为暂缓"})
        result = asyncio.run(build(companion).treat(auto=False))
        self.assertFalse(result.get("ok"))
        self.assertIn("群聊", result.get("reason", ""))
        self.assertEqual(companion.charged, [])
        self.assertEqual(companion.receipts, [])

    def test_duplicate_still_charges_idempotently(self):
        # 重复提案说明上次已经批过：扣款本身幂等，继续治
        companion = FakeCompanion(decision={"approved": False, "decision": "deny",
                                            "reason": "duplicate"})
        result = asyncio.run(build(companion).treat(auto=False))
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(companion.charged), 1)

    def test_charge_failure_sends_fail_receipt(self):
        companion = FakeCompanion(charge_result={"ok": False, "code": "no_money",
                                                 "reason": "余额不足", "balance": 10})
        result = asyncio.run(build(companion).treat(auto=False))
        self.assertFalse(result.get("ok"))
        self.assertEqual(companion.receipts[0]["status"], "fail")
        self.assertEqual(companion.receipts[0]["code"], "no_money")

    def test_old_terminal_without_bus(self):
        companion = LegacyCompanion()
        result = asyncio.run(build(companion).treat(auto=False))
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(companion.charged), 1)

    def test_receipt_error_does_not_break_treatment(self):
        companion = FakeCompanion(with_receipt=False)
        result = asyncio.run(build(companion).treat(auto=False))
        self.assertTrue(result.get("ok"), result)


if __name__ == "__main__":
    unittest.main()
