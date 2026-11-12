# -*- coding: utf-8 -*-
"""病情与医保：纯计算，不碰网络、不碰 AstrBot。

一条病有四个关键属性：**多久好、花多少钱、医保报多少、不治会不会变重**。
这四个决定了她的选择——小病硬扛还是去医院，大病要不要开口跟你借。

拆成纯函数是为了能测：给定病情和余额，结果必须是确定的。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Illness:
    """一种病。"""

    key: str
    name: str
    severity: str          # light / medium / heavy
    cost: tuple[int, int]  # 治疗费区间（元）
    hours: int             # 不去治也会好转的时间
    admission: bool = False  # 是否住院
    note: str = ""
    #: 拖着不治会不会变重
    worsen_to: str = ""

    def price(self, rng: random.Random | None = None) -> int:
        rng = rng or random.Random()
        low, high = self.cost
        return int(rng.randint(low, high) // 10 * 10)


#: 病情表。price 是「自付前的总费用」，医保按类型另算。
ILLNESSES: dict[str, Illness] = {
    "light_discomfort": Illness(
        "light_discomfort", "轻微不适", "light", (30, 80), 12,
        note="说不上哪里难受", worsen_to="cold",
    ),
    "cold": Illness(
        "cold", "感冒", "light", (150, 300), 48,
        note="鼻子堵、头有点沉", worsen_to="fever",
    ),
    "fever": Illness(
        "fever", "发烧", "medium", (300, 600), 24,
        note="身上发烫，没力气", worsen_to="gastro",
    ),
    "gastro": Illness(
        "gastro", "肠胃炎", "medium", (800, 1500), 36,
        note="上吐下泻，吃不下东西", worsen_to="appendix",
    ),
    "appendix": Illness(
        "appendix", "阑尾炎", "heavy", (8000, 15000), 72,
        admission=True, note="右下腹疼得直不起腰",
    ),
    "fracture": Illness(
        "fracture", "骨折", "heavy", (6000, 20000), 168,
        admission=True, note="摔了一跤，动不了",
    ),
    "exhaustion": Illness(
        "exhaustion", "过度疲劳", "light", (0, 0), 10,
        note="没由来的累，只想躺着",
    ),
}

#: 常见病按概率抽。日常最多的是轻微不适和感冒。
COMMON = (
    ("light_discomfort", 40),
    ("cold", 30),
    ("fever", 12),
    ("gastro", 8),
    ("exhaustion", 10),
)

#: 医保参数。门诊、住院两套起付线和比例，外加一个大病二次报销。
INSURANCE = {
    "outpatient_deductible": 300,   # 门诊起付线
    "outpatient_rate": 0.70,
    "inpatient_deductible": 1500,   # 住院起付线
    "inpatient_rate": 0.85,
    "critical_threshold": 20000,    # 大病二次报销门槛
    "critical_rate": 0.60,
    "annual_cap": 300000,           # 年度上限
}


@dataclass(slots=True)
class HealthState:
    """当前状态。持久化到插件数据目录。"""

    status: str = "健康"
    since: float = 0.0
    until: float = 0.0
    note: str = ""
    illness: str = ""
    treated: bool = False
    #: 本年度医保已用额度
    used_insurance: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "since": self.since, "until": self.until,
            "note": self.note, "illness": self.illness,
            "treated": self.treated, "used_insurance": self.used_insurance,
        }


def roll_illness(rng: random.Random | None = None) -> Illness:
    rng = rng or random.Random()
    keys = [k for k, _ in COMMON]
    weights = [w for _, w in COMMON]
    key = rng.choices(keys, weights=weights, k=1)[0]
    return ILLNESSES[key]


def get_illness(key: str) -> Illness | None:
    return ILLNESSES.get(str(key or ""))


def insurance_split(total: float, admission: bool) -> dict[str, Any]:
    """算医保报多少、自付多少。

    门诊和住院两套起付线；超过大病门槛的部分再报一次。
    年度上限用完之后就全自费。
    """
    total = max(0.0, float(total))
    if total <= 0:
        return {"total": 0.0, "reimbursed": 0.0, "self_pay": 0.0,
                "deductible": 0, "rate": 0.0, "critical": 0.0}

    if admission:
        deductible = INSURANCE["inpatient_deductible"]
        rate = INSURANCE["inpatient_rate"]
    else:
        deductible = INSURANCE["outpatient_deductible"]
        rate = INSURANCE["outpatient_rate"]

    insurable = max(0.0, total - deductible)
    reimbursed = round(insurable * rate, 2)

    critical = 0.0
    if total >= INSURANCE["critical_threshold"]:
        critical = round(
            (total - INSURANCE["critical_threshold"]) * INSURANCE["critical_rate"], 2
        )
        reimbursed = round(reimbursed + critical, 2)

    reimbursed = min(reimbursed, float(INSURANCE["annual_cap"]))
    self_pay = round(total - reimbursed, 2)
    return {
        "total": round(total, 2),
        "reimbursed": reimbursed,
        "self_pay": max(0.0, self_pay),
        "deductible": deductible,
        "rate": rate,
        "critical": critical,
    }


def will_tough_it_out(illness: Illness, balance: float, self_pay: float,
                      rng: random.Random | None = None) -> bool:
    """小病嫌贵先扛着——这比"一生病就去医院"像人。

    余额够、病又重，一定会去；余额紧张时，小病大概率先扛。
    """
    rng = rng or random.Random()
    if self_pay <= 0:
        return False
    if illness.severity == "heavy":
        return balance < self_pay * 0.5      # 大病只有真付不起才拖
    if balance >= self_pay * 3:
        return rng.random() < 0.25           # 有钱也可能懒得去
    if balance >= self_pay:
        return rng.random() < 0.55
    return True                              # 付不起，只能扛
