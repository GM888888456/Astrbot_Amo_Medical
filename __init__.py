# -*- coding: utf-8 -*-
"""我想永远陪着你-医疗（Astrbot_Amo_Medical）。

导入必须容错：CI 和单测环境里没有 astrbot，包初始化不能因此炸掉。
"""

try:
    from .main import PLUGIN_NAME, PLUGIN_VERSION, AmoMedicalPlugin
    __all__ = ["AmoMedicalPlugin", "PLUGIN_NAME", "PLUGIN_VERSION"]
except Exception:  # pragma: no cover - 没有 astrbot 时只暴露常量
    PLUGIN_NAME = "Astrbot_Amo_Medical"
    PLUGIN_VERSION = "1.0.0"
    __all__ = ["PLUGIN_NAME", "PLUGIN_VERSION"]
