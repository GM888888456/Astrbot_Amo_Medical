# -*- coding: utf-8 -*-
"""就医的两种入口：她自己决定 vs 面板手动点。

规则很简单，但必须说清楚：

- ``auto=True``（终端后台循环）= 她自己决定：尊重 ``auto_treat`` / ``tough_it_out``；
- ``auto=False``（面板按钮）= 用户催她：绕开这两个开关，直接去治。
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main as M  # noqa: E402
import asyncio  # noqa: E402
import random  # noqa: E402
from catalog import HealthState  # noqa: E402


def build(config=None, balance=0.0):
    plugin = object.__new__(M.AmoMedicalPlugin)
    plugin.config = dict(config or {})
    plugin._health = HealthState(
        status="阑尾炎", since=1.0, until=10 ** 12, note="右下腹疼",
        illness="appendix", treated=False,
    )
    plugin._history = []
    plugin._rng = random.Random(7)
    plugin._save = lambda: None
    plugin._companion = lambda: None
    plugin._companion_balance = lambda: balance
    return plugin


class AutoTreatTests(unittest.TestCase):
    def test_auto_skipped_when_auto_treat_off(self):
        plugin = build({"auto_treat": False})
        result = asyncio.run(plugin.treat(auto=True))
        self.assertTrue(result.get("skipped"), result)
        self.assertIn("手动", result.get("reason", ""))

    def test_manual_button_ignores_auto_off(self):
        plugin = build({"auto_treat": False})
        result = asyncio.run(plugin.treat(auto=False))
        self.assertFalse(result.get("skipped"), "手动点了就该去治")
        self.assertFalse(result.get("ok"), "没终端扣款入口，应报没连上")

    def test_tough_it_out_skips_when_broke(self):
        plugin = build({"auto_treat": True, "tough_it_out": True}, balance=0.0)
        result = asyncio.run(plugin.treat(auto=True))
        self.assertTrue(result.get("skipped"), result)
        self.assertTrue(result.get("tough"))

    def test_manual_button_ignores_tough(self):
        plugin = build({"auto_treat": True, "tough_it_out": True}, balance=0.0)
        result = asyncio.run(plugin.treat(auto=False))
        self.assertFalse(result.get("skipped"))
        self.assertFalse(result.get("tough"))

    def test_auto_treats_when_rich(self):
        plugin = build({"auto_treat": True, "tough_it_out": False}, balance=10 ** 6)
        result = asyncio.run(plugin.treat(auto=True))
        self.assertFalse(result.get("skipped"), result)

    def test_healthy_nothing_to_do(self):
        plugin = build()
        plugin._health = HealthState()
        result = asyncio.run(plugin.treat(auto=True))
        self.assertFalse(result.get("ok"))
        self.assertIn("没病", result.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
