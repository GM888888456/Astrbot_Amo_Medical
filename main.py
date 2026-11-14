# -*- coding: utf-8 -*-
"""我想永远陪着你-医疗（Astrbot_Amo_Medical）。

她生病了，要不要治、治不治得起，在这里算。

**关键设计：治病前必须向终端申请扣款。**

```
医疗算出要花 2400
  → 上报终端（Amo）
  → 终端下发金融扣款 + 数据记账
  → 扣到了？执行治疗 : 不治疗，病情继续
```

医疗自己**不碰钱**，也不判断"该不该花"——它只负责「这病怎么治、多少钱、医保报多少」，
钱的事交给终端统一调度。这样每笔账她都知情。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    from astrbot.api.web import error_response, json_response, request as web_request
except Exception:  # pragma: no cover
    error_response = json_response = web_request = None

try:
    from .catalog import (
        HealthState,
        get_illness,
        insurance_split,
        roll_illness,
        will_tough_it_out,
    )
except Exception:  # pragma: no cover
    from catalog import (  # type: ignore
        HealthState,
        get_illness,
        insurance_split,
        roll_illness,
        will_tough_it_out,
    )

try:
    from .link import probe as _link_probe
except Exception:  # pragma: no cover
    from link import probe as _link_probe  # type: ignore

try:
    from .catalog import (MEDICINES, MODE_LABELS, environment_factors,
                          next_stage, roll_illness, season_of_month,
                          treatment_plan)
except Exception:  # pragma: no cover
    from catalog import (MEDICINES, MODE_LABELS, environment_factors,  # type: ignore
                         next_stage, roll_illness, season_of_month,
                         treatment_plan)

PLUGIN_NAME = "Astrbot_Amo_Medical"
PLUGIN_VERSION = "1.5.7"
DISPLAY_NAME = "我想永远陪着你-医疗"

try:
    import yaml as _meta_yaml

    with open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "metadata.yaml"),
        encoding="utf-8",
    ) as _fh:
        _meta = _meta_yaml.safe_load(_fh) or {}
    if _meta.get("version"):
        PLUGIN_VERSION = str(_meta["version"])
except Exception:
    pass

SICK_STATUSES = ("轻微不适", "感冒", "低烧", "发烧", "不舒服", "生病", "拉肚子",
                 "肠胃炎", "阑尾炎", "骨折", "过度疲劳")


def _now() -> float:
    return time.time()


class AmoMedicalPlugin(Star):
    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.context = context
        self.config = config or {}

        # 配置自愈：升级/重装/改目录名之后，把空掉的 key 从快照补回来。
        # 快照在 plugin_data/<插件名>/config_snapshot.json（0600），升级不覆盖、
        # schema 重写也删不到它。
        try:
            from .config_guard import heal as _cfg_heal, fallback_data_dir as _cfg_data_dir
        except ImportError:
            try:
                from config_guard import heal as _cfg_heal, fallback_data_dir as _cfg_data_dir
            except Exception:
                _cfg_heal = None
        if _cfg_heal is not None:
            try:
                _guard_dir = os.path.dirname(os.path.abspath(__file__))
                _healed = _cfg_heal(self.config, plugin_name=PLUGIN_NAME,
                                    plugin_dir=_guard_dir,
                                    data_dir=_cfg_data_dir(_guard_dir, PLUGIN_NAME),
                                    logger=logger)
                if _healed.get("restored"):
                    logger.info("[config] 本次启动补回 %d 项配置（%s）"
                                % (len(_healed["restored"]), ", ".join(_healed["restored"])))
            except Exception as exc:
                logger.debug("[config] 配置自愈失败: %s" % exc)
        self._started_at = _now()
        self._health = HealthState()
        self._history: list[dict[str, Any]] = []
        self._rng = random.Random()
        self._last_roll = ""
        self._load()
        # 后台：一天掷一次「今天会不会不舒服」，以及「拖着不治会不会变重」
        try:
            self._auto_task = asyncio.create_task(self._auto_loop())
        except Exception:
            self._auto_task = None
        self._register_web_apis()
        logger.info(
            "[amo_med] %s %s 已加载：当前 %s"
            % (DISPLAY_NAME, PLUGIN_VERSION, self._health.status)
        )

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _bool(self, key: str, default: bool) -> bool:
        value = self._cfg(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _int(self, key: str, default: int) -> int:
        try:
            return int(float(self._cfg(key, default)))
        except Exception:
            return default

    def _float(self, key: str, default: float) -> float:
        try:
            return float(self._cfg(key, default))
        except Exception:
            return default

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _data_dir(self) -> str:
        base = ""
        for getter in ("get_plugin_data_dir", "plugin_data_dir"):
            try:
                value = getattr(self.context, getter, None)
                value = value() if callable(value) else value
                value = str(value or "").strip()
                if value:
                    base = value
                    break
            except Exception:
                continue
        if not base:
            base = os.path.join(os.getcwd(), "data", "plugin_data")
        path = os.path.join(base, PLUGIN_NAME)
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass
        return path

    def _state_path(self) -> str:
        return os.path.join(self._data_dir(), "health.json")

    def _load(self) -> None:
        try:
            with open(self._state_path(), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        self._last_roll = str(data.get("last_roll") or "")
        health = data.get("health") or {}
        self._health = HealthState(
            status=str(health.get("status") or "健康"),
            since=float(health.get("since") or 0),
            until=float(health.get("until") or 0),
            note=str(health.get("note") or ""),
            illness=str(health.get("illness") or ""),
            treated=bool(health.get("treated")),
            used_insurance=float(health.get("used_insurance") or 0),
        )
        history = data.get("history")
        self._history = list(history) if isinstance(history, list) else []

    def _save(self) -> None:
        payload = {"health": self._health.as_dict(), "history": self._history[-200:],
                   "last_roll": self._last_roll}
        try:
            tmp = self._state_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._state_path())
        except Exception as exc:
            logger.warning("[amo_med] 状态写入失败: %s" % exc)

    # ------------------------------------------------------------------
    # 病情
    # ------------------------------------------------------------------

    async def _auto_loop(self) -> None:
        """每 30 分钟看一次：今天要不要掷骰子、有没有拖成大病。"""
        import asyncio
        first = True
        while True:
            try:
                if first:
                    first = False
                    self._emit_period_event()
                await asyncio.sleep(1800)
                self._auto_roll()
                self._progress_roll()
                self._emit_period_event()
                self._emit_health_event()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("[amo_med] 自动病情检查失败: %s" % exc)

    def _emit_health_event(self) -> None:
        """生病/治好：只发事件，心流加减交给终端。"""
        terminal = self._companion()
        emitter = getattr(terminal, "emit_event", None) if terminal is not None else None
        if not callable(emitter):
            return
        info = self.status() or {}
        today = datetime.now().strftime("%Y-%m-%d")
        marks = self._state.get("health_events")
        if not isinstance(marks, dict):
            marks = {}
        changed = False
        if info.get("sick") and marks.get("sick_day") != today:
            try:
                emitter("medical_sick", {"severity": 6})
            except Exception as exc:
                logger.debug("[amo_med] 发 medical_sick 失败: %s" % exc)
            marks["sick_day"] = today
            changed = True
        if getattr(self._health, "treated", False) and marks.get("treated_day") != today:
            try:
                emitter("medical_treated", {})
            except Exception as exc:
                logger.debug("[amo_med] 发 medical_treated 失败: %s" % exc)
            marks["treated_day"] = today
            changed = True
        if changed:
            self._state["health_events"] = marks
            self._save()

    def _emit_period_event(self) -> None:
        """生理期开始/结束：只发事件给终端总线，本插件不碰心流数值。

        事件里带 ``severity``（痛经等级 0–10），由 heartflow 决定加减多少。
        """
        terminal = self._companion()
        emitter = getattr(terminal, "emit_event", None) if terminal is not None else None
        if not callable(emitter):
            return
        info = self._period_info() or {}
        try:
            day = int(info.get("day") or 0)
            bleed = int(info.get("bleed_days") or 0)
        except (TypeError, ValueError):
            return
        if not day or not bleed:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        marks = self._state.get("period_events")
        if not isinstance(marks, dict):
            marks = {}
        try:
            pain = float(info.get("pain") or 3)
        except (TypeError, ValueError):
            pain = 3.0
        changed = False
        if day == 1 and marks.get("start_day") != today:
            try:
                emitter("period_start", {"severity": pain})
            except Exception as exc:
                logger.debug("[amo_med] 发 period_start 失败: %s" % exc)
            marks["start_day"] = today
            changed = True
        elif day == bleed + 1 and marks.get("end_day") != today:
            try:
                emitter("period_end", {})
            except Exception as exc:
                logger.debug("[amo_med] 发 period_end 失败: %s" % exc)
            marks["end_day"] = today
            changed = True
        if changed:
            self._state["period_events"] = marks
            self._save()
            logger.info("[amo_med] 已向终端发事件：生理期（第 %d 天，痛经 %.0f 级）", day, pain)

    def _auto_roll(self) -> dict[str, Any]:
        """一天最多掷一次「今天会不会不舒服」。"""
        if not self._bool("enable_sick_random", True):
            return {"ok": False, "skipped": "开关关着"}
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_roll == today:
            return {"ok": False, "skipped": "今天已经掷过"}
        self._last_roll = today
        self._save()
        if self.status().get("sick"):
            return {"ok": False, "skipped": "已经在病着"}

        base = max(0.0, min(0.5, self._float("sick_prob", 0.02)))
        ctx = self.context()
        env = environment_factors(
            season=ctx.get("season", ""), weather=ctx.get("weather", ""),
            temp=ctx.get("temp"), satiety=ctx.get("satiety"),
            hydration=ctx.get("hydration"), tired=ctx.get("tired"),
            stress=ctx.get("stress"), period_phase=ctx.get("period_phase", ""),
        )
        # 环境越差越容易病：湿冷、饿、渴、累、经期各加一点
        chance = base
        chance *= 1.0 + 0.6 * bool(env.get("cold")) + 0.4 * bool(env.get("rain"))
        chance *= 1.0 + 0.5 * bool(env.get("hungry")) + 0.5 * bool(env.get("thirsty"))
        chance *= 1.0 + 0.4 * bool(env.get("tired")) + 0.3 * bool(env.get("stress"))
        chance *= 1.0 + 0.3 * bool(env.get("period"))
        chance = max(0.0, min(0.6, chance))
        if self._rng.random() >= chance:
            return {"ok": False, "skipped": "今天没事", "chance": round(chance, 3)}
        return self.fall_sick()

    def _progress_roll(self) -> dict[str, Any]:
        """拖着不治：过了病程 60% 还没看，就有机会进到相邻的下一档。"""
        if not self._bool("worsen_enable", True):
            return {"ok": False, "skipped": "开关关着"}
        if self._health.treated or self._health.status in ("健康", "恢复中"):
            return {"ok": False, "skipped": "没在病着或已经治过"}
        current = get_illness(self._health.illness)
        if current is None:
            return {"ok": False, "skipped": "没有对应病种"}
        span = max(1.0, float(current.hours) * 3600.0)
        elapsed = _now() - float(self._health.since or _now())
        if elapsed < span * 0.6:
            return {"ok": False, "skipped": "还没到点"}
        if next_stage(current) is None:
            return {"ok": False, "skipped": "已经是最后一档"}
        if self._rng.random() >= 0.25:
            return {"ok": False, "skipped": "这次没变重"}
        return self.worsen()

    def status(self) -> dict[str, Any]:
        """当前状态；到点了自动好。"""
        if (self._health.status != "健康" and 0 < self._health.until <= _now()):
            self._health = HealthState(used_insurance=self._health.used_insurance)
            self._save()
        return {
            "status": self._health.status,
            "since": self._health.since,
            "until": self._health.until,
            "note": self._health.note,
            "illness": self._health.illness,
            "treated": self._health.treated,
            "sick": self._health.status in SICK_STATUSES,
        }

    def context(self) -> dict[str, Any]:
        """今天的环境：季节、天气、温度、吃饱没、累不累、是不是经期。

        天气/心情向终端要，饱食度向记忆库要；要不到的留空，不影响发病判定。
        """
        ctx: dict[str, Any] = {
            "season": season_of_month(datetime.now().month), "weather": "",
            "temp": None, "satiety": None, "hydration": None,
            "tired": None, "stress": None, "period_phase": "",
        }
        companion = self._companion()
        state = getattr(companion, "state", None) if companion is not None else None
        if state is not None:
            weather = getattr(state, "weather", None) or {}
            try:
                ctx["weather"] = str(weather.get("text") or "")
                if weather.get("temp") is not None:
                    ctx["temp"] = float(weather.get("temp"))
            except Exception:
                pass
            try:
                ctx["period_phase"] = str((state.period_info() or {}).get("phase") or "")
            except Exception:
                pass
            try:
                mood = getattr(state, "mood", None) or {}
                ctx["tired"] = float(mood.get("疲惫") or 0)
                ctx["stress"] = max(float(mood.get("焦虑") or 0),
                                    float(mood.get("暴躁") or 0),
                                    float(mood.get("生气") or 0))
            except Exception:
                pass
        try:  # 饱食度/水分在记忆库
            from . import link as _l
        except ImportError:  # pragma: no cover
            _l = None  # type: ignore
        if _l is not None:
            try:
                data = _l._find(getattr(self, "context", None),
                                _l.DATA_NAMES, _l.DATA_MARKERS, require_mem=True)
                getter = getattr(data, "vitals", None) if data is not None else None
                if callable(getter):
                    vitals = getter() or {}
                    ctx["satiety"] = vitals.get("satiety")
                    ctx["hydration"] = vitals.get("hydration")
            except Exception:
                pass
        return ctx

    def fall_sick(self, key: str = "") -> dict[str, Any]:
        """让她生病。

        ``key`` 留空时按**今天的真实环境**抽：冬天容易感冒、夏天吃坏肚子、
        饿着渴着容易脱水、经期容易痛经；住院级的病不会被直接抽到，
        只能从上一档拖出来。
        """
        illness = get_illness(key) if key else None
        if illness is None and not key:
            ctx = self.context()
            env = environment_factors(
                season=ctx.get("season", ""), weather=ctx.get("weather", ""),
                temp=ctx.get("temp"), satiety=ctx.get("satiety"),
                hydration=ctx.get("hydration"), tired=ctx.get("tired"),
                stress=ctx.get("stress"), period_phase=ctx.get("period_phase", ""),
            )
            illness = roll_illness(self._rng, season=ctx.get("season", ""), env=env)
            logger.info("[amo_med] 今天的环境：%s %s %s度 饱%s 水%s → 得病 %s"
                        % (ctx.get("season"), ctx.get("weather") or "-",
                           ctx.get("temp"), ctx.get("satiety"), ctx.get("hydration"),
                           illness.name))
        if illness is None:
            return {"ok": False, "reason": "没有这种病"}
        self._health = HealthState(
            status=illness.name,
            since=_now(),
            until=_now() + illness.hours * 3600,
            note=illness.note,
            illness=illness.key,
            treated=False,
            used_insurance=self._health.used_insurance,
        )
        self._save()
        logger.info("[amo_med] 生病：%s（自愈 %d 小时）" % (illness.name, illness.hours))
        return {
            "ok": True,
            "illness": illness.key,
            "name": illness.name,
            "hours": illness.hours,
            "severity": illness.severity,
        }

    def worsen(self) -> dict[str, Any]:
        """拖着不治 → 病情升级。没有下一级就延长。"""
        current = get_illness(self._health.illness)
        if current is None:
            return {"ok": False}
        nxt = get_illness(current.worsen_to)
        if nxt is None:
            self._health.until = max(self._health.until, _now()) + 12 * 3600
            self._save()
            return {"ok": True, "name": self._health.status, "worsened": False}
        self._health.status = nxt.name
        self._health.illness = nxt.key
        self._health.note = nxt.note
        self._health.until = _now() + nxt.hours * 3600
        self._health.treated = False
        self._save()
        logger.info("[amo_med] 病情加重：%s → %s" % (current.name, nxt.name))
        return {"ok": True, "name": nxt.name, "worsened": True}

    def heal(self) -> None:
        self._health = HealthState(used_insurance=self._health.used_insurance)
        self._save()

    # ------------------------------------------------------------------
    # 治疗：先要钱，再治病
    # ------------------------------------------------------------------

    def quote(self) -> dict[str, Any]:
        """报个价：总费用、医保报多少、自付多少，**以及钱花在哪、吃什么药**。"""
        current = get_illness(self._health.illness)
        if current is None:
            return {"ok": False, "reason": "现在没病"}
        plan = treatment_plan(current, self._rng)
        total = float(plan.get("total") or 0.0)
        # 药价改查市场：同一名字的药，用市场当天价，并把差额算进总额
        try:
            market = self._market_drug_prices()
            if market:
                meds = plan.get("medicines") or []
                old_med = sum(float(m.get("price") or 0) for m in meds)
                new_med = 0.0
                for med in meds:
                    price = market.get(str(med.get("name") or ""))
                    if price:
                        med["price"] = round(float(price), 2)
                    new_med += float(med.get("price") or 0)
                if meds and abs(new_med - old_med) > 0.01:
                    delta = round(new_med - old_med, 2)
                    total = round(max(0.0, total + delta), 2)
                    plan["total"] = total
                    for row in plan.get("items") or []:
                        if str(row.get("kind")) == "medicine":
                            row["price"] = round(new_med, 2)
        except Exception as exc:
            logger.debug("[amo_med] 药价接市场失败: %s" % exc)
        split = insurance_split(total, current.admission)
        # 医保个人账户能抵一部分自付
        return {
            "ok": True,
            "illness": current.key,
            "name": current.name,
            "admission": current.admission,
            "total": split["total"],
            "reimbursed": split["reimbursed"],
            "self_pay": split["self_pay"],
            "mode": plan.get("mode"),
            "mode_label": plan.get("mode_label"),
            "items": plan.get("items") or [],
            "medicines": plan.get("medicines") or [],
            "advice": plan.get("advice") or "",
        }

    def _market_drug_prices(self) -> dict[str, float]:
        """市场插件当天的药价（名字 → 价格），拿不到就返回空。"""
        try:
            for meta in self.context.get_all_stars():
                text = (str(getattr(meta, "name", "") or "") + " "
                        + str(getattr(meta, "module_path", "") or ""))
                if "Marketplace" not in text and "市场" not in text:
                    continue
                inst = None
                for attr in ("star_cls", "star", "instance"):
                    obj = getattr(meta, attr, None)
                    if obj is None:
                        continue
                    inst = obj() if isinstance(obj, type) else obj
                    break
                items = getattr(inst, "items", None) if inst is not None else None
                if callable(items):
                    return {str(row.get("name") or ""): float(row.get("price") or 0)
                            for row in (items("drugs") or [])
                            if row.get("name") and float(row.get("price") or 0) > 0}
        except Exception as exc:
            logger.debug("[amo_med] 读市场药价失败: %s" % exc)
        return {}

    def _companion_balance(self) -> float:
        """终端那边的余额；读不到按 0 算（= 付不起，先扛）。"""
        companion = self._companion()
        getter = getattr(companion, "balance", None) if companion is not None else None
        if not callable(getter):
            return 0.0
        try:
            return float(getter() or 0)
        except Exception:
            return 0.0

    COMPANION_NAMES = ("Astrbot_Amo_forever", "astrbot_plugin_amo_forever", "amo_forever")

    def _companion(self):
        for name in self.COMPANION_NAMES:
            try:
                meta = self.context.get_registered_star(name)
            except Exception:
                meta = None
            star = self._instance(meta)
            if star is not None:
                return star
        try:
            for meta in self.context.get_all_stars():
                marker = ((getattr(meta, "name", "") or "") + " "
                          + (getattr(meta, "module_path", "") or "")).lower()
                if "amo_forever" not in marker:
                    continue
                star = self._instance(meta)
                if star is not None:
                    return star
        except Exception:
            pass
        return None

    @staticmethod
    def _instance(meta):
        if meta is None:
            return None
        for attr in ("star_cls", "instance", "star", "plugin"):
            value = getattr(meta, attr, None)
            if value is not None:
                return value
        return None

    async def treat(self, auto: bool = False) -> dict[str, Any]:
        """走完整流程：报价 → 请终端扣款 → 扣到就治，扣不到不治。

        ``auto=True`` 表示是后台自动判断触发的（终端循环）；面板上的按钮走
        ``auto=False``。两种入口的差别只有「要不要拦」：自动模式下尊重
        ``auto_treat`` / ``tough_it_out`` 两个配置，手动点了就一定去治。
        """
        # 已经治好了就别再扣一次：病情结束后 illness 会被清空，
        # 这里再兜一层，防止后台循环和手动指令撞在一起重复扣款。
        if self._health.treated and self._health.status not in ("健康", "恢复中"):
            return {"ok": True, "repeated": True, "name": self._health.status,
                    "reason": "已经治过了"}
        info = self.quote()
        if not info.get("ok"):
            return {"ok": False, "reason": info.get("reason") or "现在没病"}
        if auto and not self._bool("auto_treat", True):
            return {"ok": False, "skipped": True, "manual": True,
                    "reason": "自动就医关着，等手动处理"}
        self_pay = float(info["self_pay"])
        if auto and self._bool("tough_it_out", True) and self_pay > 0:
            illness = get_illness(info.get("illness") or "")
            balance = self._companion_balance()
            if illness is not None and will_tough_it_out(illness, balance, self_pay, self._rng):
                return {"ok": False, "skipped": True, "tough": True,
                        "reason": "小病先扛一扛", "self_pay": self_pay}
        paid = True
        reason = ""

        if self_pay > 0:
            companion = self._companion()
            if companion is None or not callable(getattr(companion, "charge", None)):
                logger.warning("[amo_med] 找不到终端扣款入口，按未支付处理")
                paid = False
                reason = "没连上终端"
            else:
                # 幂等号：同一次病只扣一次，重试/并发都认这个号
                request_id = "med-%s-%d" % (
                    str(info.get("illness") or info.get("name") or "sick"),
                    int(self._health.since or 0),
                )
                event_id = request_id
                approved, deny_reason = True, ""
                submit = getattr(companion, "submit_event", None)
                if callable(submit):
                    try:
                        decision = submit({
                            "event_id": event_id,
                            "event_type": "medical_treat",
                            "source": "medical",
                            "scene": "private",
                            "payload": {
                                "cost": self_pay,
                                "reason": "%s治疗" % info["name"],
                                "detail": {"illness": info.get("illness"),
                                           "name": info.get("name")},
                            },
                            "need_approve": True,
                        })
                        approved = bool(decision.get("approved")) or \
                            str(decision.get("reason") or "") == "duplicate"
                        deny_reason = str(decision.get("reason") or "")
                    except Exception as exc:
                        # 老终端/接口异常：别把看病卡死，按原流程走
                        logger.warning("[amo_med] 提案失败，按原流程走: %s" % exc)
                        approved = True
                result = {}
                if not approved:
                    paid = False
                    reason = deny_reason or "终端没批"
                    logger.info("[amo_med] 提案未通过（%s），这病先不治" % reason)
                else:
                    try:
                        result = await companion.charge(
                            self_pay, reason="%s治疗" % info["name"], payee="医院",
                            request_id=request_id, source="medical",
                            detail=str(info.get("name") or ""),
                        )
                        paid = bool(result.get("ok"))
                        reason = str(result.get("reason") or "")
                    except Exception as exc:
                        logger.warning("[amo_med] 扣款调用失败: %s" % exc)
                        paid = False
                        result = {"code": "call_failed"}
                        reason = "扣款出错"
                    receipt = getattr(companion, "event_receipt", None)
                    if callable(receipt):
                        try:
                            receipt({
                                "event_id": event_id,
                                "status": "success" if paid else "fail",
                                "code": str(result.get("code") or ""),
                                "reason": reason,
                                "amount": self_pay,
                                "balance": result.get("balance"),
                            })
                        except Exception as exc:
                            logger.warning("[amo_med] 回执失败: %s" % exc)

        self._history.append({
            "ts": _now(), "illness": info["illness"], "name": info["name"],
            "total": info["total"], "self_pay": self_pay, "paid": paid,
            "reason": reason,
        })
        del self._history[:-200]

        if not paid:
            # 没钱治：病继续，还有可能加重
            self.worsen()
            self._save()
            logger.info("[amo_med] 未支付，%s 继续加重" % info["name"])
            return {"ok": False, "reason": reason or "余额不足", "need": self_pay,
                    **{k: info[k] for k in ("name", "total", "reimbursed", "self_pay")},
                    "mode_label": info.get("mode_label"),
                    "medicines": info.get("medicines") or [],
                    "advice": info.get("advice") or ""}

        illness = get_illness(info["illness"])
        recovery_hours = max(2, int((illness.hours if illness else 12) / 2))
        # 病结束：状态进入「恢复中」，不再算 sick，后台循环也就不会重复来治
        self._health.status = "恢复中"
        self._health.illness = ""
        self._health.note = "刚看完病，还在恢复"
        self._health.treated = True
        self._health.until = _now() + recovery_hours * 3600
        self._health.used_insurance = round(
            self._health.used_insurance + float(info["reimbursed"]), 2
        )
        self._save()
        logger.info(
            "[amo_med] 已治疗 %s（%s）：总 %.0f，医保报 %.0f，自付 %.0f，用药 %s"
            % (info["name"], info.get("mode_label") or "", info["total"],
               info["reimbursed"], self_pay,
               "、".join(m.get("name", "") for m in (info.get("medicines") or [])) or "无")
        )
        return {"ok": True, "recovered": True, **info}

    # ------------------------------------------------------------------
    # 指令
    # ------------------------------------------------------------------

    @filter.command("health", alias={"身体"})
    async def cmd_health(self, event: AstrMessageEvent):
        info = self.status()
        if not info["sick"]:
            yield event.plain_result("现在好好的。")
            return
        quote = self.quote()
        if quote.get("ok"):
            yield event.plain_result(
                "%s（%s）\n治疗总费 %.0f，医保报 %.0f，自付 %.0f"
                % (info["status"], info["note"], quote["total"],
                   quote["reimbursed"], quote["self_pay"])
            )
        else:
            yield event.plain_result("%s（%s）" % (info["status"], info["note"]))

    @filter.command("treat", alias={"看病"})
    async def cmd_treat(self, event: AstrMessageEvent):
        result = await self.treat()
        if result.get("ok"):
            yield event.plain_result(
                "治好了：总费 %.0f，医保报 %.0f，自付 %.0f"
                % (result["total"], result["reimbursed"], result["self_pay"])
            )
        else:
            yield event.plain_result("没治成：%s" % result.get("reason"))

    # ------------------------------------------------------------------
    # 面板
    # ------------------------------------------------------------------

    @staticmethod
    def _json(payload):
        if json_response is not None:
            try:
                return json_response(payload)
            except Exception:
                pass
        return payload

    @staticmethod
    def _fail(message):
        if error_response is not None:
            try:
                return error_response(message)
            except Exception:
                pass
        return {"code": 1, "message": message}

    def _register_web_apis(self) -> None:
        routes = [
            ("/status", self.api_status, ["GET"]),
            ("/health", self.api_health, ["GET"]),
            ("/history", self.api_history, ["GET"]),
            ("/treat", self.api_treat, ["POST"]),
            ("/fall", self.api_fall, ["POST"]),
            ("/medicines", self.api_medicines, ["GET"]),
            ("/period", self.api_period, ["GET", "POST"]),
        ]
        for path, handler, methods in routes:
            try:
                self.context.register_web_api("/" + PLUGIN_NAME + path, handler, methods, path)
            except TypeError:
                try:
                    self.context.register_web_api("/" + PLUGIN_NAME + path, methods, path, handler)
                except Exception as exc:
                    logger.warning("[amo_med] 接口 %s 注册失败: %s" % (path, exc))
            except Exception as exc:
                logger.warning("[amo_med] 接口 %s 注册失败: %s" % (path, exc))

    def _period_info(self) -> dict:
        """从终端读生理期状态；终端没连就返回空。"""
        terminal = self._companion()
        state = getattr(terminal, "state", None) if terminal is not None else None
        if state is None:
            return {}
        try:
            return dict(state.period_info() or {})
        except Exception:
            return {}

    async def api_period(self):
        """生理期设置：GET 读，POST（cycle_days / start）写。数值由终端算，这里只转发。"""
        terminal = self._companion()
        state = getattr(terminal, "state", None) if terminal is not None else None
        if state is None:
            return self._json({"code": 1, "message": "终端未连接，生理期设置需要它"})
        payload = {}
        try:
            payload = await self._payload()
        except Exception:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        cycle_days = payload.get("cycle_days")
        start = str(payload.get("start") or "").strip()
        if cycle_days or start:
            try:
                info = state.set_period_settings(cycle_days, start or None)
            except Exception as exc:
                return self._json({"code": 1, "message": "设置失败：%s" % exc})
            logger.info("[amo_med] 生理期设置已更新：周期 %s 天，上次 %s",
                        info.get("cycle_days"), start or "不变")
        else:
            info = self._period_info()
        return self._json({"code": 0, "data": info})

    async def api_medicines(self):
        """药价表：面板「医疗」页直接用。"""
        rows = []
        for key, item in MEDICINES.items():
            rows.append({"key": item.key, "name": item.name, "price": item.price,
                         "kind": item.kind, "note": item.note})
        rows.sort(key=lambda x: x["price"])
        return self._json({"code": 0, "data": {
            "items": rows,
            "modes": MODE_LABELS,
            "market_total": self._market_drug_total(),
        }})

    def _market_drug_total(self) -> int:
        """市场插件里一共多少种药；拿不到就退回 161（当前目录规模）。"""
        try:
            for meta in self.context.get_all_stars():
                text = (str(getattr(meta, "name", "") or "") + " "
                        + str(getattr(meta, "module_path", "") or ""))
                if "Marketplace" not in text and "市场" not in text:
                    continue
                for attr in ("star_cls", "star", "instance"):
                    obj = getattr(meta, attr, None)
                    if obj is None:
                        continue
                    inst = obj() if isinstance(obj, type) else obj
                    counts = getattr(inst, "counts", None)
                    if callable(counts):
                        return int((counts() or {}).get("drugs") or 0)
        except Exception as exc:
            logger.debug("[amo_med] 读市场药数失败: %s" % exc)
        return 161

    async def api_health(self):
        """我自己连没连上终端/记忆库。面板顶部那一行就看这个。"""
        return self._json({"code": 0, "data": _link_probe(self)})

    async def api_status(self):
        info = self.status()
        quote = self.quote() if info["sick"] else {}
        return self._json({"code": 0, "data": {
            "name": PLUGIN_NAME, "version": PLUGIN_VERSION, "display_name": DISPLAY_NAME,
            "link": _link_probe(self),
            "health": info, "quote": quote,
            "used_insurance": self._health.used_insurance,
            "auto_treat": self._bool("auto_treat", True),
            "tough_it_out": self._bool("tough_it_out", True),
            "period": self._period_info(),
            "uptime": int(_now() - self._started_at),
        }})

    async def api_history(self):
        return self._json({"code": 0, "data": {"items": list(reversed(self._history))}})

    async def api_treat(self):
        result = await self.treat(auto=False)
        return self._json({"code": 0, "data": result})

    async def api_fall(self):
        payload: dict[str, Any] = {}
        if web_request is not None:
            try:
                payload = await web_request.json(default={}) or {}
            except Exception:
                payload = {}
        key = str((payload or {}).get("illness") or "")
        result = self.fall_sick(key)
        return self._json({"code": 0, "data": result})

    async def terminate(self) -> None:
        try:
            if getattr(self, "_auto_task", None) is not None:
                self._auto_task.cancel()
        except Exception:
            pass
        self._save()
