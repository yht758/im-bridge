"""线性图标 —— Lucide 几何（ISC），按需着色并缓存。

统一 24 网格、1.7 描边、圆头圆角；尺寸一律按逻辑像素给，
内部按 devicePixelRatio 渲染，避免高 DPI 下发虚。
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import tokens as K
from .icons_data import ICONS

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

_ICON_CACHE: dict[tuple[str, str, int, float], QIcon] = {}
_PIX_CACHE: dict[tuple[str, str, int, float], QPixmap] = {}

# 旧页面使用的图标名 → 新图标名
ALIASES = {
    "key": "key-round",
    "message": "message-circle",
    "refresh": "refresh-cw",
    "restart": "rotate-cw",
    "stop": "square",
    "close": "x",
    "delete": "trash",
    "chat": "message-circle",
    "sparkle": "sparkles",
}


def _resolve(name: str) -> str:
    return ALIASES.get(name, name)


def _svg(name: str, color: str, width: float = 1.7) -> bytes:
    body = ICONS.get(_resolve(name))
    if body is None:
        body = ICONS["minus"]
    stroke, opacity = _stroke(color)
    extra = f' stroke-opacity="{opacity}"' if opacity < 1.0 else ""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        f'fill="none" stroke="{stroke}" stroke-width="{width}" stroke-linecap="round" '
        f'stroke-linejoin="round"{extra}>{body}</svg>'
    ).encode("utf-8")


def _stroke(color: str) -> tuple[str, float]:
    """SVG 不认 rgba()，统一转成 #RRGGBB + stroke-opacity。"""
    parsed = K.qcolor(color)
    if not parsed.isValid():
        return "#000000", 1.0
    return parsed.name(), round(parsed.alpha() / 255.0, 3)


def _render(name: str, color: str, size: int, dpr: float, width: float = 1.7) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, color, width)))
    pixmap = QPixmap(int(round(size * dpr)), int(round(size * dpr)))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def pixmap(name: str, color: str | None = None, size: int = 17, dpr: float = 2.0) -> QPixmap:
    color = color or str(K.t("INK_2"))
    key = (name, color, size, dpr)
    cached = _PIX_CACHE.get(key)
    if cached is None:
        cached = _render(name, color, size, dpr)
        _PIX_CACHE[key] = cached
    return cached


def icon(name: str, color: str | None = None, size: int = 17) -> QIcon:
    color = color or str(K.t("INK_2"))
    key = (name, color, size, 0.0)
    cached = _ICON_CACHE.get(key)
    if cached is None:
        result = QIcon()
        for dpr in (1.0, 2.0):
            result.addPixmap(_render(name, color, size, dpr))
        _ICON_CACHE[key] = result
        cached = result
    return cached


def icon_size(size: int = 17) -> QSize:
    return QSize(size, size)


def clear_cache() -> None:
    _ICON_CACHE.clear()
    _PIX_CACHE.clear()
