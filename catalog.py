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


# ---------------------------------------------------------------------------
# 药品与治疗方案
# ---------------------------------------------------------------------------
#
# 以前只有一个「买药 60」的口子，她当然说不出自己吃了什么。现在药品是**有名字、
# 有单价、对症**的：感冒灵治感冒，蒙脱石散治拉肚子，阑尾炎要手术。钱仍然是
# 病种总额说了算——药品、挂号、检查按固定单价拆开，差额进「检查/处置费」，
# 所以**总额和医保报销口径和以前完全一致**，只是终于能说清楚钱花在哪。


@dataclass(frozen=True, slots=True)
class Medicine:
    """一种药。price 是常见规格的价格（元）。"""

    key: str
    name: str
    price: float
    note: str = ""
    #: oral 口服 / topical 外用 / infusion 输液
    kind: str = "oral"


#: 药品定价（元/盒或一次量）。改这里就是改全部药费。
MEDICINES: dict[str, Medicine] = {
    "ganmaoling": Medicine("ganmaoling", "感冒灵颗粒", 25.0, "一盒九袋，冲水喝", "oral"),
    "tuire": Medicine("tuire", "布洛芬缓释胶囊", 17.0, "退烧止痛，饭后吃", "oral"),
    "tuiretie": Medicine("tuiretie", "退热贴", 12.5, "贴额头，物理降温", "topical"),
    "mengtuo": Medicine("mengtuo", "蒙脱石散", 22.0, "拉肚子用，一次一袋", "oral"),
    "zuoyang": Medicine("zuoyang", "左氧氟沙星片", 25.0, "消炎，饭后", "oral"),
    "jianwei": Medicine("jianwei", "健胃消食片", 10.0, "吃撑了或者没胃口", "oral"),
    "toubao": Medicine("toubao", "头孢克肟胶囊", 32.0, "术后消炎", "oral"),
    "yunnan": Medicine("yunnan", "云南白药气雾剂", 35.5, "跌打损伤外用", "topical"),
    "putao": Medicine("putao", "葡萄糖注射液", 45.0, "挂水补液", "infusion"),
    "koufu": Medicine("koufu", "口服补液盐", 6.0, "腹泻脱水时补电解质", "oral"),
}

#: 每种病怎么治：mode 决定挂号/住院，medicines 是吃什么药。
TREATMENTS: dict[str, dict[str, Any]] = {
    "light_discomfort": {
        "mode": "self_care", "medicines": ("jianwei",),
        "advice": "先吃点消食片，早点睡，别硬撑",
    },
    "cold": {
        "mode": "self_care", "medicines": ("ganmaoling", "tuiretie"),
        "advice": "感冒灵按时喝，多喝热水，别再吹空调了",
    },
    "fever": {
        "mode": "outpatient", "medicines": ("tuire", "ganmaoling"),
        "advice": "先去门诊看看，退烧药饭后吃，温度没下来再复诊",
    },
    "gastro": {
        "mode": "outpatient", "medicines": ("mengtuo", "zuoyang", "koufu"),
        "advice": "蒙脱石散先止住，消炎药饭后，这两天只吃清淡的",
    },
    "appendix": {
        "mode": "surgery", "medicines": ("toubao", "putao"),
        "advice": "阑尾炎拖不得，得做手术，术后挂水消炎",
    },
    "fracture": {
        "mode": "inpatient", "medicines": ("yunnan", "tuire"),
        "advice": "先固定，住院观察几天，疼得厉害吃布洛芬",
    },
    "exhaustion": {
        "mode": "self_care", "medicines": (),
        "advice": "不用吃药，睡一觉就好",
    },
}

#: 挂号/急诊/住院床位的固定价（元）。
SERVICE_FEES = {
    "self_care": 0.0,      # 自己买药，不挂号
    "outpatient": 50.0,    # 门诊挂号
    "inpatient": 80.0,     # 急诊挂号 + 床位另算
    "surgery": 80.0,
}

#: 床位费（元/天），住院类按这个算。
BED_PER_DAY = 320.0

MODE_LABELS = {
    "self_care": "自己买药",
    "outpatient": "门诊",
    "inpatient": "住院",
    "surgery": "手术",
}


def medicine(key: str) -> Medicine | None:
    return MEDICINES.get(str(key or ""))


def treatment_of(illness: Illness | None) -> dict[str, Any]:
    """病情 → 治疗方案。没登记的按轻重给个保守默认。"""
    if illness is None:
        return {"mode": "self_care", "medicines": (), "advice": ""}
    found = TREATMENTS.get(illness.key)
    if found:
        return found
    if illness.admission:
        return {"mode": "inpatient", "medicines": (), "advice": "住院观察"}
    return {"mode": "self_care", "medicines": (), "advice": "先观察一下"}


def treatment_plan(illness: Illness, rng: random.Random | None = None) -> dict[str, Any]:
    """把病种总额拆成一条条明细：挂号、药、床位、检查/处置。

    总额仍然是 `Illness.price()` 说了算（和医保、历史记录的口径一致），
    药品和挂号按固定单价算，剩下的差额进「检查/处置费」——真实账单也是这样，
    检查费本来就不固定。
    """
    total = float(illness.price(rng))
    plan = treatment_of(illness)
    mode = str(plan.get("mode") or "self_care")
    items: list[dict[str, Any]] = []

    fee = float(SERVICE_FEES.get(mode, 0.0))
    if fee > 0:
        items.append({"name": "挂号费", "price": round(fee, 2), "kind": "fee"})

    meds: list[dict[str, Any]] = []
    med_total = 0.0
    for key in plan.get("medicines") or ():
        item = medicine(key)
        if item is None:
            continue
        meds.append({"key": item.key, "name": item.name, "price": item.price,
                     "kind": item.kind, "note": item.note})
        med_total += float(item.price)
    if med_total > 0:
        items.append({"name": "药费", "price": round(med_total, 2), "kind": "medicine"})

    days = 0
    if mode in ("inpatient", "surgery"):
        days = max(1, min(7, int(round(illness.hours / 24.0)) or 1))
        items.append({"name": "床位费（%d 天）" % days,
                      "price": round(BED_PER_DAY * days, 2), "kind": "bed"})

    if mode == "self_care":
        # 自己买药：账单上就是一笔「药费」，具体吃了哪几样写在 medicines 里，
        # 别拆成「药费 + 药费补差」那种看不出名堂的行。
        items = ([{"name": "药费", "price": round(total, 2), "kind": "medicine"}]
                 if total > 0 else [])

    used = sum(float(row["price"]) for row in items)
    if mode == "surgery":
        # 手术费给固定占比，剩下的算检查/耗材
        surgery = round(total * 0.55, 2)
        items.append({"name": "手术费", "price": surgery, "kind": "surgery"})
        used += surgery
    rest = round(total - used, 2)
    if rest > 0:
        label = "检查费" if mode in ("outpatient", "inpatient", "surgery") else "药费补差"
        items.append({"name": label, "price": rest, "kind": "exam"})
    elif rest < 0:
        # 明细比总额还贵（固定单价定的），按明细算，不让总额变负数
        total = used

    return {
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode),
        "items": items,
        "medicines": meds,
        "advice": str(plan.get("advice") or ""),
        "total": round(total, 2),
    }
