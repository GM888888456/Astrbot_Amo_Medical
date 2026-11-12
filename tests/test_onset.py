# -*- coding: utf-8 -*-
"""发病要像人：分季节、看环境，恶化只能相邻，住院级的病不会凭空出现。"""
from __future__ import annotations

import os
import random
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import (  # noqa: E402
    ILLNESSES, MEDICINES, TREATMENTS, environment_factors, get_illness,
    next_stage, roll_illness, season_of_month,
)

SEVERITY_RANK = {"light": 0, "medium": 1, "heavy": 2}


def sample(season: str, temp: float, weather: str, times: int = 3000, **env_kwargs):
    rng = random.Random(20261112)
    env = environment_factors(season=season, weather=weather, temp=temp, **env_kwargs)
    counter = Counter(roll_illness(rng, season=season, env=env).name for _ in range(times))
    return {name: count / times for name, count in counter.items()}


class CatalogCase(unittest.TestCase):
    def test_enough_illnesses_and_drugs(self):
        self.assertGreaterEqual(len(ILLNESSES), 30, "病种太少")
        self.assertGreaterEqual(len(MEDICINES), 30, "药太少")
        for key in ILLNESSES:
            self.assertIn(key, TREATMENTS, "每种病都要有治疗方案：%s" % key)

    def test_worsening_only_goes_up(self):
        for key, illness in ILLNESSES.items():
            nxt = next_stage(illness)
            if not illness.worsen_to:
                self.assertIsNone(nxt)
                continue
            self.assertIsNotNone(nxt, "恶化目标不存在：%s" % key)
            self.assertGreaterEqual(SEVERITY_RANK[nxt.severity],
                                    SEVERITY_RANK[illness.severity],
                                    "不能越拖越轻：%s → %s" % (key, nxt.key))

    def test_only_adjacent_ones_come_from_progression(self):
        """支气管炎、肺炎只能从上一档拖出来；阑尾炎/骨折这类可以自己发生，但要极少。"""
        rng = random.Random(7)
        heavy = 0
        for _ in range(1000):
            picked = roll_illness(rng, season="冬")
            self.assertFalse(picked.progression_only, "只能由恶化过来的病被直接抽到了：%s" % picked.key)
            if picked.severity == "heavy":
                heavy += 1
        # 抽样有波动，留到 2%：重点是「不能常得」，不是精确概率
        self.assertLess(heavy / 1000, 0.02, "重型病不该一上来就得，实际比例 %.1f%%" % (heavy / 10))

    def test_winter_is_cold_season(self):
        top = sample("冬", 3, "晴")
        first = max(top, key=top.get)
        self.assertIn(first, ("感冒", "咽炎", "轻微不适"), "冬天最常见的应该是感冒类，实际是 %s" % first)

    def test_summer_is_stomach_season(self):
        top = sample("夏", 33, "晴")
        first = max(top, key=top.get)
        self.assertIn(first, ("肠胃炎", "食物中毒", "中暑", "脱水", "皮肤过敏"),
                      "夏天不该是感冒为主，实际是 %s" % first)

    def test_hunger_shifts_towards_dehydration(self):
        normal = sample("夏", 33, "晴")
        hungry = sample("夏", 33, "晴", satiety=8, hydration=5)
        self.assertGreater(hungry.get("脱水", 0) + hungry.get("肠胃炎", 0),
                           normal.get("脱水", 0) + normal.get("肠胃炎", 0),
                           "又饿又渴应该更容易脱水/肠胃问题")

    def test_period_raises_dysmenorrhea(self):
        plain = sample("春", 20, "晴")
        period = sample("春", 20, "晴", period_phase="月经期")
        self.assertGreater(period.get("痛经", 0), plain.get("痛经", 0))

    def test_season_mapping(self):
        self.assertEqual(season_of_month(1), "冬")
        self.assertEqual(season_of_month(4), "春")
        self.assertEqual(season_of_month(7), "夏")
        self.assertEqual(season_of_month(10), "秋")

    def test_drugs_belong_to_their_illness(self):
        for key in ILLNESSES:
            for drug_key in TREATMENTS[key].get("medicines") or ():
                self.assertIn(drug_key, MEDICINES, "%s 用的药没定价：%s" % (key, drug_key))


if __name__ == "__main__":
    unittest.main()
