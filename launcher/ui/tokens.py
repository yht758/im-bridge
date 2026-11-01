"""设计令牌 —— 全项目唯一真源。

参考图（im-bridge Concept Design）落成的取值：近黑侧栏 + 冷白工作区，
绿色只承担「在线/成功」，主操作纯黑，卡片 18px 圆角，阴影宽而淡。

任何新颜色/圆角/间距都必须先加进这里，再被 style.py 与组件消费；
禁止在页面里写死色值。
"""
from __future__ import annotations

import re

from PySide6.QtGui import QColor

# ---------------------------------------------------------------- 调色板
LIGHT: dict[str, object] = {
    "APP_BG": "#F3F4F6",
    "SURFACE": "#FFFFFF",
    "SURFACE_2": "#F7F8FA",
    "SURFACE_3": "#EEF0F3",
    "INK": "#16181C",
    "INK_2": "#6C7280",
    "INK_3": "#747C89",
    "LINE": "#ECEEF2",
    "LINE_2": "#DDE1E7",
    "OK": "#17B26A",
    "OK_SOFT": "rgba(23, 178, 106, 0.10)",
    "WARN": "#B9832B",
    "ERR": "#D0483C",
    "ERR_SOFT": "rgba(208, 72, 60, 0.10)",
    "ACCENT": "#16181C",
    "ACCENT_HOVER": "#2B2D34",
    "ACCENT_INK": "#FFFFFF",
    "SIDEBAR": "#17181B",
    "SIDEBAR_INK": "#F2F3F5",
    "SIDEBAR_INK_2": "rgba(242, 243, 245, 0.52)",
    "SIDEBAR_INK_3": "rgba(242, 243, 245, 0.34)",
    "SIDEBAR_ACTIVE": "rgba(255, 255, 255, 0.10)",
    "SIDEBAR_HOVER": "rgba(255, 255, 255, 0.055)",
    "SIDEBAR_LINE": "rgba(255, 255, 255, 0.07)",
    "SHADOW": "#1B1D22",
    "SHADOW_ALPHA": 20,
    "HERO_INK": "#16181C",
    "HERO_INK_2": "#5F6672",
    "SERIES": ["#16181C", "#4A4E58", "#767C88", "#A5AAB4", "#D2D6DC", "#17B26A"],
}

DARK: dict[str, object] = {
    "APP_BG": "#101114",
    "SURFACE": "#17191D",
    "SURFACE_2": "#1D2025",
    "SURFACE_3": "#24272E",
    "INK": "#F2F3F5",
    "INK_2": "#A2A8B3",
    "INK_3": "#9299A6",
    "LINE": "rgba(255, 255, 255, 0.07)",
    "LINE_2": "rgba(255, 255, 255, 0.13)",
    "OK": "#3DD68C",
    "OK_SOFT": "rgba(61, 214, 140, 0.14)",
    "WARN": "#D6A356",
    "ERR": "#E5766A",
    "ERR_SOFT": "rgba(229, 118, 106, 0.14)",
    "ACCENT": "#F2F3F5",
    "ACCENT_HOVER": "#FFFFFF",
    "ACCENT_INK": "#101114",
    "SIDEBAR": "#0B0C0E",
    "SIDEBAR_INK": "#F2F3F5",
    "SIDEBAR_INK_2": "rgba(242, 243, 245, 0.50)",
    "SIDEBAR_INK_3": "rgba(242, 243, 245, 0.30)",
    "SIDEBAR_ACTIVE": "rgba(255, 255, 255, 0.11)",
    "SIDEBAR_HOVER": "rgba(255, 255, 255, 0.06)",
    "SIDEBAR_LINE": "rgba(255, 255, 255, 0.06)",
    "SHADOW": "#000000",
    "SHADOW_ALPHA": 70,
    "HERO_INK": "#16181C",
    "HERO_INK_2": "#5F6672",
    "SERIES": ["#F2F3F5", "#BFC4CC", "#9198A3", "#666C77", "#3C4049", "#3DD68C"],
}

LIGHT.update(POSTER_BG="#FFFFFF", POSTER_LINE="#ECEEF2")
DARK.update(POSTER_BG="#FFFFFF", POSTER_LINE="#ECEEF2")

THEMES: dict[str, dict[str, object]] = {"light": LIGHT, "dark": DARK}
THEME_NAMES = {"light": "纸白", "dark": "墨黑"}

_current = "light"


def set_theme(name: str) -> str:
    global _current
    _current = name if name in THEMES else "light"
    return _current


def current_theme() -> str:
    return _current


def t(key: str) -> object:
    return THEMES[_current].get(key, LIGHT.get(key, "#000000"))


# ---------------------------------------------------------------- 度量
RADIUS_SM = 10
RADIUS_MD = 14
RADIUS_LG = 18
RADIUS_XL = 22

SIDEBAR_W = 216
NAV_ITEM_H = 42
PAGE_PAD = 24
PAGE_GAP = 16
CARD_GAP = 14
CONTENT_MAX = 1360
HERO_H = 236
STATUS_CARD_H = 108

FONT_UI = '"Microsoft YaHei UI", "Segoe UI", "Microsoft YaHei"'
FONT_MONO = '"Cascadia Mono", "Cascadia Code", "Consolas", "Microsoft YaHei UI"'

# 字号 / 字重（px, weight）
TYPE: dict[str, tuple[int, int]] = {
    "display": (30, 600),
    "h1": (20, 600),
    "h2": (15, 600),
    "h3": (13, 600),
    "body": (13, 400),
    "caption": (12, 400),
    "hint": (11, 400),
    "mono": (12, 400),
    "stat": (24, 600),
}

# ---------------------------------------------------------------- 动效
DUR_MICRO = 140
DUR_UI = 200
DUR_LAYOUT = 260
DUR_DATA = 180


# ---------------------------------------------------------------- 颜色工具
_RGBA = re.compile(r"rgba?\(([^)]+)\)")


def qcolor(value: object, alpha: int | None = None) -> QColor:
    """把 #RGB / #RRGGBB / rgba(...) / QColor 统一成 QColor。"""
    if isinstance(value, QColor):
        color = QColor(value)
    else:
        text = str(value).strip()
        match = _RGBA.match(text)
        if match:
            parts = [p.strip() for p in match.group(1).split(",")]
            red, green, blue = (int(float(p)) for p in parts[:3])
            opacity = float(parts[3]) if len(parts) > 3 else 1.0
            color = QColor(red, green, blue, int(round(opacity * 255)))
        else:
            color = QColor(text)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


def mix(base: object, other: object, ratio: float) -> QColor:
    """线性混色，ratio=0 取 base。"""
    first, second = qcolor(base), qcolor(other)
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        int(first.red() + (second.red() - first.red()) * ratio),
        int(first.green() + (second.green() - first.green()) * ratio),
        int(first.blue() + (second.blue() - first.blue()) * ratio),
        int(first.alpha() + (second.alpha() - first.alpha()) * ratio),
    )

# Homepage pilot: Qt logical pixels; shared by layout and QSS.
HOME_HEADER_H = 194
HOME_GAP = 16
HOME_STATUS_BREAKPOINT = 900
HOME_TITLE_SIZE = 28
