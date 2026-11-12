# -*- coding: utf-8 -*-
"""病情、药价与医保：纯计算，不碰网络、不碰 AstrBot。

一条病有四个关键属性：**多久好、花多少钱、医保报多少、不治会不会变重**。
这四个决定了她的选择——小病硬扛还是去医院，大病要不要开口跟你借。

**发病不是抽签。** 感冒不会变成癌症：每种病有自己的「谁会得」（季节、天气、
饿不饿、累不累、经期），恶化也只能走到**相邻的下一档**（感冒→咽炎→支气管炎→
肺炎），跳级是不可能的。

拆成纯函数是为了能测：给定病情、环境和余额，结果必须是确定的。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
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
    #: 拖着不治会不会变重（只能写相邻的下一档）
    worsen_to: str = ""
    #: 什么季节容易得（空 = 四季都行）
    seasons: tuple[str, ...] = ()
    #: 发病权重：越大越常见。日常小病 10~16，住院级 1~2
    weight: float = 6.0
    #: 触发条件，见 _TRIGGER_FACTORS
    triggers: tuple[str, ...] = ()
    #: 只能由「拖着不治」恶化过来，不会突然得：支气管炎、肺炎这类
    progression_only: bool = False

    def price(self, rng: random.Random | None = None) -> int:
        rng = rng or random.Random()
        low, high = self.cost
        return int(rng.randint(low, high) // 10 * 10)


#: 季节名，和 day_types / 天气插件口径一致
S_PRING = "春"
S_SUMMER = "夏"
S_AUTUMN = "秋"
S_WINTER = "冬"

#: 病情表。price 是「自付前的总费用」，医保按类型另算。
ILLNESSES: dict[str, Illness] = {
    # ---------------- 上呼吸道：感冒→咽炎→支气管炎→肺炎 ----------------
    "light_discomfort": Illness(
        "light_discomfort", "轻微不适", "light", (30, 80), 12,
        note="说不上哪里难受", worsen_to="cold",
        weight=16.0, triggers=("cold", "tired"),
    ),
    "cold": Illness(
        "cold", "感冒", "light", (150, 300), 48,
        note="鼻子堵、头有点沉", worsen_to="pharyngitis",
        seasons=(S_AUTUMN, S_WINTER, S_PRING), weight=15.0,
        triggers=("cold", "rain", "tired"),
    ),
    "pharyngitis": Illness(
        "pharyngitis", "咽炎", "light", (200, 420), 60,
        note="嗓子像有刀片，吞口水都疼", worsen_to="bronchitis",
        seasons=(S_AUTUMN, S_WINTER), weight=5.0,
        triggers=("cold",),
    ),
    "bronchitis": Illness(
        "bronchitis", "支气管炎", "medium", (600, 1200), 96,
        note="咳得停不下来，胸口发闷", worsen_to="pneumonia",
        seasons=(S_WINTER, S_PRING), weight=5.0,
        triggers=("cold", "dust"), progression_only=True,
    ),
    "pneumonia": Illness(
        "pneumonia", "肺炎", "heavy", (4000, 9000), 168,
        admission=True, note="发高烧、喘不上气", worsen_to="",
        seasons=(S_WINTER,), weight=1.5, triggers=("cold", "tired"),
        progression_only=True,
    ),
    # ---------------- 发热类 ----------------
    "fever": Illness(
        "fever", "发烧", "medium", (300, 600), 24,
        note="身上发烫，没力气", worsen_to="flu",
        weight=8.0, triggers=("cold", "tired"),
    ),
    "flu": Illness(
        "flu", "流感", "medium", (500, 1100), 120,
        note="浑身酸疼，冷得发抖", worsen_to="pneumonia",
        seasons=(S_WINTER, S_PRING), weight=6.0,
        triggers=("cold", "tired"),
    ),
    # ---------------- 消化：肠胃炎→胃炎→胃溃疡 ----------------
    "gastro": Illness(
        "gastro", "肠胃炎", "medium", (800, 1500), 36,
        note="上吐下泻，吃不下东西", worsen_to="dehydration",
        seasons=(S_SUMMER,), weight=9.0, triggers=("food", "hot", "hungry"),
    ),
    "gastritis": Illness(
        "gastritis", "胃炎", "light", (300, 700), 72,
        note="胃里一阵一阵地烧", worsen_to="ulcer",
        weight=7.0, triggers=("hungry", "stress"),
    ),
    "ulcer": Illness(
        "ulcer", "胃溃疡", "medium", (1500, 3200), 120,
        note="饿的时候胃疼，吃点东西缓一会儿", worsen_to="",
        weight=3.0, triggers=("hungry", "stress"),
    ),
    "food_poisoning": Illness(
        "food_poisoning", "食物中毒", "medium", (900, 2000), 48,
        note="拉得站不稳，肚子绞着疼", worsen_to="",
        seasons=(S_SUMMER,), weight=5.0, triggers=("food", "hot"),
    ),
    "constipation": Illness(
        "constipation", "便秘", "light", (40, 120), 36,
        note="肚子胀，蹲半天没结果", worsen_to="hemorrhoid",
        weight=8.0, triggers=("thirsty", "hungry"),
    ),
    "hemorrhoid": Illness(
        "hemorrhoid", "痔疮", "light", (200, 600), 96,
        note="坐下去就疼", worsen_to="",
        weight=5.0, triggers=(),
    ),
    "appendix": Illness(
        "appendix", "阑尾炎", "heavy", (8000, 15000), 72,
        admission=True, note="右下腹疼得直不起腰", worsen_to="",
        weight=0.4, triggers=(),
    ),
    # ---------------- 妇科 / 泌尿 ----------------
    "dysmenorrhea": Illness(
        "dysmenorrhea", "痛经", "light", (30, 150), 24,
        note="小腹坠着疼，腰也酸", worsen_to="",
        weight=10.0, triggers=("period",),
    ),
    "cystitis": Illness(
        "cystitis", "尿路感染", "light", (300, 900), 72,
        note="总想上厕所，去了又疼", worsen_to="",
        seasons=(S_SUMMER,), weight=4.5, triggers=("thirsty", "period"),
    ),
    # ---------------- 头面 / 五官 ----------------
    "migraine": Illness(
        "migraine", "偏头痛", "light", (100, 350), 24,
        note="太阳穴一跳一跳地疼，见光更难受", worsen_to="",
        weight=7.0, triggers=("tired", "stress", "period"),
    ),
    "insomnia": Illness(
        "insomnia", "失眠", "light", (60, 260), 48,
        note="躺到三点还睁着眼", worsen_to="exhaustion",
        weight=7.0, triggers=("stress", "tired"),
    ),
    "conjunctivitis": Illness(
        "conjunctivitis", "结膜炎", "light", (120, 380), 60,
        note="眼睛红、痒、有眼屎", worsen_to="",
        seasons=(S_SUMMER, S_PRING), weight=4.0, triggers=("dust", "hot"),
    ),
    "stye": Illness(
        "stye", "麦粒肿", "light", (80, 300), 72,
        note="眼皮上起个小包，眨眼就疼", worsen_to="",
        weight=3.0, triggers=("tired", "dust"),
    ),
    "rhinitis": Illness(
        "rhinitis", "过敏性鼻炎", "light", (150, 500), 48,
        note="打喷嚏打个不停，鼻子痒", worsen_to="",
        seasons=(S_PRING, S_AUTUMN), weight=6.0, triggers=("dust", "cold"),
    ),
    "otitis": Illness(
        "otitis", "中耳炎", "medium", (400, 1100), 96,
        note="耳朵里胀痛，听东西发闷", worsen_to="",
        weight=2.5, triggers=("cold", "rain"),
    ),
    "mouth_ulcer": Illness(
        "mouth_ulcer", "口腔溃疡", "light", (30, 120), 60,
        note="嘴里破了个口，吃东西像受刑", worsen_to="",
        weight=7.0, triggers=("tired", "stress"),
    ),
    "wisdom_tooth": Illness(
        "wisdom_tooth", "智齿发炎", "medium", (500, 1800), 96,
        note="腮帮子肿了，张不开嘴", worsen_to="",
        weight=3.0, triggers=("tired",),
    ),
    # ---------------- 皮肤 ----------------
    "dermatitis": Illness(
        "dermatitis", "皮肤过敏", "light", (100, 400), 48,
        note="身上起了片红疹，越挠越痒", worsen_to="",
        seasons=(S_PRING, S_SUMMER), weight=5.0, triggers=("dust", "hot"),
    ),
    "eczema": Illness(
        "eczema", "湿疹", "light", (200, 600), 120,
        note="皮肤干裂起皮，痒得睡不着", worsen_to="",
        seasons=(S_WINTER,), weight=4.0, triggers=("dust",),
    ),
    # ---------------- 外伤 / 劳损 ----------------
    "sprain": Illness(
        "sprain", "扭伤", "light", (100, 500), 72,
        note="脚踝肿了一圈，踩地发虚", worsen_to="fracture",
        weight=4.0, triggers=(),
    ),
    "fracture": Illness(
        "fracture", "骨折", "heavy", (6000, 20000), 168,
        admission=True, note="摔了一跤，动不了", worsen_to="",
        weight=0.2, triggers=(),
    ),
    "scald": Illness(
        "scald", "烫伤", "medium", (400, 1600), 96,
        note="手上烫起个泡，碰一下钻心疼", worsen_to="",
        weight=2.0, triggers=(),
    ),
    "cervical": Illness(
        "cervical", "颈椎病", "light", (200, 800), 120,
        note="脖子僵，转头费劲，肩上也酸", worsen_to="",
        weight=5.0, triggers=("tired", "stress"),
    ),
    "lumbar": Illness(
        "lumbar", "腰肌劳损", "light", (150, 700), 120,
        note="腰上像灌了铅，弯腰起不来", worsen_to="",
        weight=5.0, triggers=("tired",),
    ),
    # ---------------- 全身 ----------------
    "exhaustion": Illness(
        "exhaustion", "过度疲劳", "light", (0, 0), 10,
        note="没由来的累，只想躺着", worsen_to="insomnia",
        weight=10.0, triggers=("tired", "hungry", "thirsty"),
    ),
    "dehydration": Illness(
        "dehydration", "脱水", "medium", (300, 900), 12,
        note="头晕口干，站起来眼前发黑", worsen_to="",
        seasons=(S_SUMMER,), weight=4.0, triggers=("thirsty", "hot"),
    ),
    "heatstroke": Illness(
        "heatstroke", "中暑", "medium", (400, 1500), 24,
        note="恶心、头晕，浑身冒汗又发冷", worsen_to="",
        seasons=(S_SUMMER,), weight=3.5, triggers=("hot", "thirsty"),
    ),
}

#: 常见病按概率抽（兼容旧接口；新逻辑走 roll_illness 的权重与环境）
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


# ---------------------------------------------------------------------------
# 发病：像人一样，不是抽签
# ---------------------------------------------------------------------------

#: 触发条件 → 判定函数所需的上下文键。满足的 ×1.7，声明了但没满足的 ×0.6。
_TRIGGER_KEYS = {
    "cold": "cold",         # 低温 / 雨雪
    "rain": "rain",         # 下雨下雪
    "hot": "hot",           # 高温
    "dust": "dust",         # 干燥 / 雾霾 / 空调
    "hungry": "hungry",
    "thirsty": "thirsty",
    "tired": "tired",
    "stress": "stress",
    "period": "period",
    "food": "food",
    "constipation": "constipation",
}

#: 权重下限。别设太大：设成 0.5 会把「阑尾炎 / 骨折」这种本该极罕见的病
#: 抬到 1% 以上，等于每天都有机会平白无故住院。
MIN_WEIGHT = 0.05


def season_of_month(month: int) -> str:
    month = int(month or 1)
    if month in (3, 4, 5):
        return S_PRING
    if month in (6, 7, 8):
        return S_SUMMER
    if month in (9, 10, 11):
        return S_AUTUMN
    return S_WINTER


def environment_factors(*, season: str = "", weather: str = "", temp: float | None = None,
                        satiety: float | None = None, hydration: float | None = None,
                        tired: float | None = None, stress: float | None = None,
                        period_phase: str = "") -> dict[str, bool]:
    """把环境翻译成「今天的身体处在什么状态」。"""
    text = str(weather or "")
    rainy = any(word in text for word in ("雨", "雪", "雷"))
    foggy = any(word in text for word in ("雾", "霾", "沙"))
    try:
        degree = float(temp) if temp is not None else 20.0
    except (TypeError, ValueError):
        degree = 20.0
    return {
        "cold": degree <= 10 or rainy,
        "rain": rainy,
        "hot": degree >= 30,
        "dust": foggy or degree <= 5,
        "hungry": satiety is not None and float(satiety) < 30,
        "thirsty": hydration is not None and float(hydration) < 30,
        "tired": tired is not None and float(tired) >= 60,
        "stress": stress is not None and float(stress) >= 60,
        "period": str(period_phase or "") == "月经期",
        # 夏天吃坏东西的概率天然更高
        "food": degree >= 26 or rainy,
        "constipation": False,
    }


def onset_score(illness: Illness, *, season: str = "", env: dict[str, bool] | None = None) -> float:
    """某种病今天找上门的可能性（相对值）。"""
    env = env or {}
    score = max(MIN_WEIGHT, float(illness.weight))
    if illness.seasons:
        score *= 2.0 if str(season or "") in illness.seasons else 0.35
    for trigger in illness.triggers:
        hit = bool(env.get(_TRIGGER_KEYS.get(trigger, trigger), False))
        score *= 1.7 if hit else 0.6
    return round(score, 3)


def roll_illness(rng: random.Random | None = None, *, season: str = "",
                 env: dict[str, bool] | None = None,
                 allow_heavy: bool = True) -> Illness:
    """按季节和环境抽一个病。

    默认**不会**一上来就抽到住院级的病：重型病（肺炎、阑尾炎、骨折）只有
    ``allow_heavy`` 才进候选——它们应该由「拖着不治」一步步恶化过去，
    而不是某天忽然得了个肺炎。
    """
    rng = rng or random.Random()
    candidates: list[tuple[Illness, float]] = []
    for illness in ILLNESSES.values():
        if illness.progression_only:
            continue          # 只能从上一档恶化过来，不会突然得
        if illness.severity == "heavy" and not allow_heavy:
            continue
        candidates.append((illness, onset_score(illness, season=season, env=env)))
    if not candidates:
        return ILLNESSES["light_discomfort"]
    keys = [item[0].key for item in candidates]
    weights = [max(0.01, item[1]) for item in candidates]
    key = rng.choices(keys, weights=weights, k=1)[0]
    return ILLNESSES[key]


def next_stage(illness: Illness | None) -> Illness | None:
    """拖着不治的下一档；没有下一档返回 None（只能相邻推进）。"""
    if illness is None or not illness.worsen_to:
        return None
    return ILLNESSES.get(illness.worsen_to)


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
# 药品是**有名字、有单价、对症**的：感冒灵治感冒，蒙脱石散治拉肚子，阑尾炎要手术。
# 钱仍然由病种总额说了算——药品、挂号、检查按固定单价拆开，差额进「检查/处置费」，
# 所以总额和医保报销口径不变，只是终于能说清楚钱花在哪。


@dataclass(frozen=True, slots=True)
class Medicine:
    """一种药。price 是常见规格的价格（元）。"""

    key: str
    name: str
    price: float
    note: str = ""
    #: oral 口服 / topical 外用 / eye 眼用 / infusion 输液 / dental 口腔
    kind: str = "oral"


#: 药品定价（元/盒或一次量）。改这里就是改全部药费。
MEDICINES: dict[str, Medicine] = {
    # 感冒发烧
    "ganmaoling": Medicine("ganmaoling", "感冒灵颗粒", 25.0, "一天三次，一袋冲水", "oral"),
    "tuire": Medicine("tuire", "布洛芬缓释胶囊", 17.0, "38.5 度以上才吃，间隔 6 小时", "oral"),
    "tuiretie": Medicine("tuiretie", "退热贴", 12.5, "贴额头，四小时换一次", "topical"),
    "zhike": Medicine("zhike", "复方甘草片", 12.0, "含服，咳嗽厉害时再来一片", "oral"),
    "huatan": Medicine("huatan", "氨溴索口服液", 24.0, "化痰，喝完多喝水", "oral"),
    "azithro": Medicine("azithro", "阿奇霉素片", 38.0, "三天疗程，必须吃完", "oral"),
    # 消炎
    "amox": Medicine("amox", "阿莫西林胶囊", 33.0, "处方药，吃完一个疗程", "oral"),
    "toubao": Medicine("toubao", "头孢克肟胶囊", 32.0, "术后消炎", "oral"),
    "zuoyang": Medicine("zuoyang", "左氧氟沙星片", 25.0, "消炎，饭后", "oral"),
    # 肠胃
    "mengtuo": Medicine("mengtuo", "蒙脱石散", 22.0, "一袋冲水，饭前空腹", "oral"),
    "koufu": Medicine("koufu", "口服补液盐", 6.0, "拉肚子脱水时补电解质", "oral"),
    "jianwei": Medicine("jianwei", "健胃消食片", 10.0, "吃撑了嚼两片", "oral"),
    "aomei": Medicine("aomei", "奥美拉唑肠溶胶囊", 28.0, "早饭前一片", "oral"),
    "alut": Medicine("alut", "铝碳酸镁咀嚼片", 26.0, "烧心时嚼两片", "oral"),
    "ruyuan": Medicine("ruyuan", "乳果糖口服液", 22.0, "早晨空腹一支，多喝水", "oral"),
    "zhichuang": Medicine("zhichuang", "复方角菜酸酯栓", 26.0, "睡前用，一天一次", "topical"),
    # 妇科 / 泌尿
    "buoluo": Medicine("buoluo", "布洛芬片（止痛）", 12.0, "疼得厉害时吃一片", "oral"),
    "sanJin": Medicine("sanJin", "三金片", 28.0, "一天三次，多喝水", "oral"),
    # 五官
    "loratadine": Medicine("loratadine", "氯雷他定片", 18.0, "一天一片，晚上吃", "oral"),
    "nasal": Medicine("nasal", "糠酸莫米松鼻喷雾", 45.0, "每侧鼻孔一喷，一天一次", "topical"),
    "saline": Medicine("saline", "生理性海水鼻腔喷雾", 20.0, "洗鼻子，一天两次", "topical"),
    "eye_drop": Medicine("eye_drop", "左氧氟沙星滴眼液", 16.0, "一天四次，一次一滴", "eye"),
    "eye_oint": Medicine("eye_oint", "红霉素眼膏", 8.0, "睡前涂，别揉眼睛", "eye"),
    "mouth_patch": Medicine("mouth_patch", "口腔溃疡贴", 14.0, "贴住溃疡面，止痛", "dental"),
    "metronidazole": Medicine("metronidazole", "甲硝唑片", 12.0, "治牙周发炎，忌酒", "oral"),
    "ear_drop": Medicine("ear_drop", "氧氟沙星滴耳液", 18.0, "一天三次，滴完侧躺五分钟", "topical"),
    # 皮肤
    "calamine": Medicine("calamine", "炉甘石洗剂", 15.0, "摇匀涂，止痒", "topical"),
    "mupirocin": Medicine("mupirocin", "莫匹罗星软膏", 21.0, "薄涂，一天两次", "topical"),
    # 外伤 / 劳损
    "yunnan": Medicine("yunnan", "云南白药气雾剂", 35.5, "喷患处，一天三次", "topical"),
    "iodine": Medicine("iodine", "碘伏", 9.0, "每天消毒一次", "topical"),
    "bandaid": Medicine("bandaid", "创可贴", 6.5, "擦干净再贴", "topical"),
    "scald_oint": Medicine("scald_oint", "烧烫伤膏", 18.0, "薄涂，别包太紧", "topical"),
    "diclofenac": Medicine("diclofenac", "双氯芬酸二乙胺乳胶剂", 28.0, "揉开，一天三次", "topical"),
    "jingfukang": Medicine("jingfukang", "颈复康颗粒", 32.0, "一天两次，冲服", "oral"),
    # 全身
    "huoxiang": Medicine("huoxiang", "藿香正气水", 15.0, "一次一支，含酒精", "oral"),
    "putao": Medicine("putao", "葡萄糖注射液", 45.0, "挂水补液", "infusion"),
}

#: 每种病怎么治：mode 决定挂号/住院，medicines 是吃什么药。
TREATMENTS: dict[str, dict[str, Any]] = {
    "light_discomfort": {"mode": "self_care", "medicines": ("jianwei",),
                         "advice": "先吃点消食片，早点睡，别硬撑"},
    "cold": {"mode": "self_care", "medicines": ("ganmaoling", "tuiretie"),
             "advice": "感冒灵按时喝，多喝热水，别再吹空调了"},
    "pharyngitis": {"mode": "self_care", "medicines": ("zhike", "amox"),
                    "advice": "少说话，含片别当糖吃，消炎药要吃完"},
    "bronchitis": {"mode": "outpatient", "medicines": ("huatan", "azithro"),
                   "advice": "咳得这么厉害别再拖了，化痰药加消炎药，别吸烟"},
    "pneumonia": {"mode": "inpatient", "medicines": ("azithro", "putao"),
                  "advice": "要住院，挂水消炎，别自己下床乱走"},
    "fever": {"mode": "outpatient", "medicines": ("tuire", "ganmaoling"),
              "advice": "先去门诊看看，退烧药饭后吃，温度没下来再复诊"},
    "flu": {"mode": "outpatient", "medicines": ("tuire", "zhike", "putao"),
            "advice": "流感要好好躺两天，退烧药别叠着吃"},
    "gastro": {"mode": "outpatient", "medicines": ("mengtuo", "zuoyang", "koufu"),
               "advice": "蒙脱石散先止住，消炎药饭后，这两天只吃清淡的"},
    "gastritis": {"mode": "self_care", "medicines": ("aomei", "alut"),
                  "advice": "奥美拉唑早饭前吃，别再空着肚子喝咖啡"},
    "ulcer": {"mode": "outpatient", "medicines": ("aomei", "alut"),
              "advice": "胃溃疡得吃满疗程，辣的凉的都先停"},
    "food_poisoning": {"mode": "outpatient", "medicines": ("mengtuo", "koufu", "zuoyang"),
                       "advice": "补水最重要，止泻药加消炎药，观察有没有发烧"},
    "constipation": {"mode": "self_care", "medicines": ("ruyuan",),
                     "advice": "多喝水多吃菜，乳果糖早上空腹喝，别硬撑"},
    "hemorrhoid": {"mode": "self_care", "medicines": ("zhichuang",),
                   "advice": "别久坐，辛辣的先戒，栓剂睡前用"},
    "appendix": {"mode": "surgery", "medicines": ("toubao", "putao"),
                 "advice": "阑尾炎拖不得，得做手术，术后挂水消炎"},
    "dysmenorrhea": {"mode": "self_care", "medicines": ("buoluo",),
                     "advice": "热敷小腹，疼得受不了吃片止痛药，别硬扛"},
    "cystitis": {"mode": "outpatient", "medicines": ("sanJin", "zuoyang"),
                 "advice": "多喝水勤上厕所，消炎药吃完，别憋尿"},
    "migraine": {"mode": "self_care", "medicines": ("buoluo",),
                 "advice": "找个暗一点的房间躺一会儿，止痛药别连着吃好几天"},
    "insomnia": {"mode": "self_care", "medicines": (),
                 "advice": "别刷手机了，喝杯热的，早点躺下"},
    "conjunctivitis": {"mode": "self_care", "medicines": ("eye_drop",),
                       "advice": "别揉眼睛，毛巾分开用，滴眼液按时点"},
    "stye": {"mode": "self_care", "medicines": ("eye_oint",),
             "advice": "热敷，别挤，眼膏睡前涂"},
    "rhinitis": {"mode": "self_care", "medicines": ("loratadine", "nasal", "saline"),
                 "advice": "先洗鼻子再喷药，过敏药晚上吃"},
    "otitis": {"mode": "outpatient", "medicines": ("ear_drop", "amox"),
               "advice": "耳朵别进水，滴耳液滴完侧躺五分钟"},
    "mouth_ulcer": {"mode": "self_care", "medicines": ("mouth_patch",),
                    "advice": "贴住溃疡面，别吃辣的酸的，多睡会儿"},
    "wisdom_tooth": {"mode": "outpatient", "medicines": ("metronidazole", "buoluo"),
                     "advice": "先消炎止痛，炎症下去再考虑拔牙"},
    "dermatitis": {"mode": "self_care", "medicines": ("loratadine", "calamine"),
                   "advice": "别挠，炉甘石止痒，过敏药一天一片"},
    "eczema": {"mode": "self_care", "medicines": ("mupirocin", "calamine"),
               "advice": "保湿，别用热水烫，破了的地方薄涂药膏"},
    "sprain": {"mode": "self_care", "medicines": ("diclofenac", "bandaid"),
               "advice": "先冷敷两天，别揉，走路慢一点"},
    "fracture": {"mode": "inpatient", "medicines": ("yunnan", "buoluo"),
                 "advice": "先固定，住院观察几天，疼得厉害吃布洛芬"},
    "scald": {"mode": "self_care", "medicines": ("scald_oint", "iodine"),
              "advice": "冲凉水十分钟，别挑破水泡，涂烫伤膏"},
    "cervical": {"mode": "self_care", "medicines": ("jingfukang", "diclofenac"),
                 "advice": "少低头，每小时起来动动，晚上热敷"},
    "lumbar": {"mode": "self_care", "medicines": ("diclofenac", "yunnan"),
               "advice": "别久站久坐，搬东西先蹲下，热敷完再揉"},
    "exhaustion": {"mode": "self_care", "medicines": (),
                   "advice": "不用吃药，睡一觉就好"},
    "dehydration": {"mode": "self_care", "medicines": ("koufu",),
                    "advice": "小口多次喝水，补液盐兑水喝，别再晒太阳"},
    "heatstroke": {"mode": "self_care", "medicines": ("huoxiang", "koufu"),
                   "advice": "赶紧到阴凉处，解开衣服，藿香正气水别和头孢一起用"},
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
