"""展示层格式化：数字一律千分位，token 用 K/M 缩写，时长用中文。"""
from __future__ import annotations


def fmt_int(value: float | int | None) -> str:
    if value in (None, ""):
        return "—"
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "—"


def fmt_tokens(value: float | int | None) -> str:
    if value in (None, ""):
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    for unit, scale in (("M", 1_000_000), ("K", 1_000)):
        if abs(number) >= scale:
            text = f"{number / scale:.1f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
    return f"{int(number):,}"


def fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number and number < 0.01:
        return f"¥{number:.4f}"
    return f"¥{number:,.2f}"


def fmt_uptime(seconds: float | int | None) -> str:
    if not seconds:
        return "—"
    total = int(float(seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分钟"


def mask_host(host: str | None) -> str:
    """IP / 域名脱敏：203.0.113.10 → 120.***.***.101。"""
    text = str(host or "").strip()
    if not text:
        return "—"
    parts = text.split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        return f"{parts[0]}.***.***.{parts[3]}"
    return text
