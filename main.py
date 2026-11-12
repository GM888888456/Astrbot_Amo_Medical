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
        ILLNESSES,
        HealthState,
        get_illness,
        insurance_split,
        roll_illness,
        will_tough_it_out,
    )
except Exception:  # pragma: no cover
    from catalog import (  # type: ignore
        ILLNESSES,
        HealthState,
        get_illness,
        insurance_split,
        roll_illness,
        will_tough_it_out,
    )

PLUGIN_NAME = "Astrbot_Amo_Medical"
PLUGIN_VERSION = "1.0.1"
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
        self._started_at = _now()
        self._health = HealthState()
        self._history: list[dict[str, Any]] = []
        self._rng = random.Random()
        self._load()
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
        payload = {"health": self._health.as_dict(), "history": self._history[-200:]}
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

    def fall_sick(self, key: str = "") -> dict[str, Any]:
        """让她生病。key 留空就按常见病抽。"""
        illness = get_illness(key) if key else roll_illness(self._rng)
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
        """报个价：总费用、医保报多少、自付多少。"""
        current = get_illness(self._health.illness)
        if current is None:
            return {"ok": False, "reason": "现在没病"}
        total = current.price(self._rng)
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
        }

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

    async def treat(self) -> dict[str, Any]:
        """走完整流程：报价 → 请终端扣款 → 扣到就治，扣不到不治。"""
        # 已经治好了就别再扣一次：病情结束后 illness 会被清空，
        # 这里再兜一层，防止后台循环和手动指令撞在一起重复扣款。
        if self._health.treated and self._health.status not in ("健康", "恢复中"):
            return {"ok": True, "repeated": True, "name": self._health.status,
                    "reason": "已经治过了"}
        info = self.quote()
        if not info.get("ok"):
            return {"ok": False, "reason": info.get("reason") or "现在没病"}

        self_pay = float(info["self_pay"])
        paid = True
        reason = ""

        if self_pay > 0:
            companion = self._companion()
            if companion is None or not callable(getattr(companion, "charge", None)):
                logger.warning("[amo_med] 找不到终端扣款入口，按未支付处理")
                paid = False
                reason = "没连上终端"
            else:
                try:
                    result = await companion.charge(
                        self_pay, reason="%s治疗" % info["name"], payee="医院"
                    )
                    paid = bool(result.get("ok"))
                    reason = str(result.get("reason") or "")
                except Exception as exc:
                    logger.warning("[amo_med] 扣款调用失败: %s" % exc)
                    paid = False
                    reason = "扣款出错"

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
                    **{k: info[k] for k in ("name", "total", "reimbursed", "self_pay")}}

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
            "[amo_med] 已治疗 %s：总 %.0f，医保报 %.0f，自付 %.0f"
            % (info["name"], info["total"], info["reimbursed"], self_pay)
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
            ("/history", self.api_history, ["GET"]),
            ("/treat", self.api_treat, ["POST"]),
            ("/fall", self.api_fall, ["POST"]),
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

    async def api_status(self):
        info = self.status()
        quote = self.quote() if info["sick"] else {}
        return self._json({"code": 0, "data": {
            "name": PLUGIN_NAME, "version": PLUGIN_VERSION, "display_name": DISPLAY_NAME,
            "health": info, "quote": quote,
            "used_insurance": self._health.used_insurance,
            "uptime": int(_now() - self._started_at),
        }})

    async def api_history(self):
        return self._json({"code": 0, "data": {"items": list(reversed(self._history))}})

    async def api_treat(self):
        result = await self.treat()
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
        self._save()
