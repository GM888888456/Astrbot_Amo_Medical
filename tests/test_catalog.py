# -*- coding: utf-8 -*-
"""病情与医保测试。"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import (  # noqa: E402
    ILLNESSES,
    INSURANCE,
    HealthState,
    get_illness,
    insurance_split,
    roll_illness,
    will_tough_it_out,
)


class IllnessTests(unittest.TestCase):
    def test_all_have_cost_and_hours(self):
        for key, item in ILLNESSES.items():
            self.assertTrue(item.name, key)
            self.assertGreaterEqual(item.cost[1], item.cost[0], key)
            self.assertGreater(item.hours, 0, key)

    def test_severity_values(self):
        for item in ILLNESSES.values():
            self.assertIn(item.severity, ("light", "medium", "heavy"))

    def test_price_in_range_and_rounded(self):
        illness = get_illness("cold")
        for _ in range(50):
            price = illness.price(random.Random())
            self.assertGreaterEqual(price, illness.cost[0])
            self.assertLessEqual(price, illness.cost[1])
            self.assertEqual(price % 10, 0, "价格要凑整，别出现 173 元这种")

    def test_unknown_key(self):
        self.assertIsNone(get_illness("nope"))

    def test_roll_returns_known(self):
        for _ in range(30):
            illness = roll_illness(random.Random())
            self.assertIn(illness.key, ILLNESSES)


class InsuranceTests(unittest.TestCase):
    def test_zero(self):
        result = insurance_split(0, False)
        self.assertEqual(result["self_pay"], 0)

    def test_outpatient_reimbursement(self):
        """门诊 320：起付线 300，可报 20，报 70% = 14，自付 306。"""
        result = insurance_split(320, False)
        self.assertAlmostEqual(result["reimbursed"], 14.0, places=2)
        self.assertAlmostEqual(result["self_pay"], 306.0, places=2)

    def test_inpatient_reimbursement(self):
        """住院 12000：起付 1500，可报 10500，报 85% = 8925。"""
        result = insurance_split(12000, True)
        self.assertAlmostEqual(result["reimbursed"], 8925.0, places=2)
        self.assertAlmostEqual(result["self_pay"], 3075.0, places=2)

    def test_critical_illness_second_reimbursement(self):
        """超过 2 万的部分再报 60%。"""
        result = insurance_split(50000, True)
        self.assertGreater(result["critical"], 0)
        self.assertLess(result["self_pay"], 15000)

    def test_annual_cap(self):
        result = insurance_split(1000000, True)
        self.assertLessEqual(result["reimbursed"], INSURANCE["annual_cap"])

    def test_inpatient_pays_less_than_outpatient(self):
        out = insurance_split(10000, False)
        inp = insurance_split(10000, True)
        self.assertLess(inp["self_pay"], out["self_pay"])


class ToughItOutTests(unittest.TestCase):
    def test_free_treatment_never_refused(self):
        self.assertFalse(
            will_tough_it_out(get_illness("exhaustion"), 0, 0, random.Random(1))
        )

    def test_broke_refuses_small_illness(self):
        illness = get_illness("cold")
        rng = random.Random(7)
        results = [will_tough_it_out(illness, 50, 200, rng) for _ in range(50)]
        self.assertTrue(all(results), "付不起的小病一定要扛")

    def test_rich_often_goes(self):
        illness = get_illness("cold")
        rng = random.Random(3)
        results = [will_tough_it_out(illness, 5000, 200, rng) for _ in range(200)]
        went = results.count(False)
        self.assertGreater(went, 100, "有钱时多数会去看")

    def test_heavy_illness_goes_even_when_tight(self):
        illness = get_illness("appendix")
        self.assertFalse(will_tough_it_out(illness, 2000, 3000, random.Random(1)))


class HealthStateTests(unittest.TestCase):
    def test_default_healthy(self):
        state = HealthState()
        self.assertEqual(state.status, "健康")
        self.assertFalse(state.treated)

    def test_roundtrip(self):
        state = HealthState(status="感冒", illness="cold", treated=True)
        data = state.as_dict()
        self.assertEqual(data["status"], "感冒")
        self.assertTrue(data["treated"])


if __name__ == "__main__":
    unittest.main()

