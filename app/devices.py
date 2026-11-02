"""设备（产品线）枚举：用于筛选、问答范围与列表展示。"""
from __future__ import annotations

COMMON_DEVICE = "通用"
DEVICES = ("PLM", "Plus_500", "Plus_Pro", "LP", COMMON_DEVICE)


def normalize_device(value: str | None) -> str:
    """把输入规范成枚举值；空值表示未指定，非法值抛 ValueError。"""
    text = (value or "").strip()
    if not text:
        return ""
    for item in DEVICES:
        if text.casefold() == item.casefold():
            return item
    raise ValueError("设备只能是 " + "、".join(DEVICES) + " 或留空")
