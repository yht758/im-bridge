"""动效预设 —— 统一时长/曲线，全部可中断。

Qt 没有 Motion/Framer，这里只用两种廉价手段：
1. `QGraphicsOpacityEffect` + QPropertyAnimation 做淡入淡出；
2. 容器内自由定位的子件做几何位移（悬停上浮），不触发布局。

时长分级沿用规范：Micro 140 / UI 200 / Layout 260 / Data 180。
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QRect, QRectF, Qt, QTimer, Property
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from . import tokens as K

_EASE = QEasingCurve(QEasingCurve.Type.OutQuint)
_reduced: bool | None = None


def ease() -> QEasingCurve:
    return QEasingCurve(_EASE)


def reduced_motion() -> bool:
    """读 Windows「减少动态效果」；读取失败时按不减少处理。"""
    global _reduced
    if _reduced is not None:
        return _reduced
    value = 0
    try:
        if sys.platform == "win32":
            enabled = ctypes.c_int(1)
            ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
            value = 0 if ok and not enabled.value else 1
        else:
            value = 1
    except Exception:  # noqa: BLE001
        value = 1
    _reduced = value == 0
    return _reduced


def duration(base: int) -> int:
    return 0 if reduced_motion() else base


class _Fade(QObject):
    """一次淡入淡出；同一控件再次触发会先停掉上一段，避免叠动画。"""

    def __init__(self, widget: QWidget, start: float, end: float, ms: int, delay: int) -> None:
        super().__init__(widget)
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(start)
            widget.setGraphicsEffect(effect)
        self._effect = effect
        self._anim = QPropertyAnimation(effect, b"opacity", self)
        self._anim.setDuration(ms)
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.setEasingCurve(ease())
        if delay:
            QTimer.singleShot(delay, self._start)
        else:
            self._start()
        # 动画结束后必须摘掉 effect：常驻的 QGraphicsOpacityEffect 会和子控件
        # 自身的 effect 嵌套，Qt 在这种组合下会漏画子控件（表现为整块卡片消失）。
        self._anim.finished.connect(self._cleanup)

    def _start(self) -> None:
        self._anim.start()

    def _cleanup(self) -> None:
        widget = self.parent()
        if isinstance(widget, QWidget) and widget.graphicsEffect() is self._effect:
            widget.setGraphicsEffect(None)
        self.deleteLater()


def fade_in(widget: QWidget, ms: int = K.DUR_UI, delay: int = 0, start: float = 0.0) -> None:
    if duration(ms) == 0:
        widget.setGraphicsEffect(None)
        return
    _Fade(widget, start, 1.0, duration(ms), delay)


def fade_out(widget: QWidget, ms: int = K.DUR_MICRO) -> None:
    if duration(ms) == 0:
        widget.hide()
        return
    fade = _Fade(widget, 1.0, 0.0, duration(ms), 0)
    fade._anim.finished.connect(widget.hide)


def stagger(widgets: list[QWidget], ms: int = K.DUR_UI, step: int = 34) -> None:
    """卡片入场：短促错峰，整体在 ~320ms 内结束。"""
    if duration(ms) == 0:
        return
    for index, widget in enumerate(widgets):
        fade_in(widget, ms=ms, delay=index * step, start=0.0)


class LiftWrap(QWidget):
    """给卡片一个可上浮的宿主：卡片是自由定位子件，位移不触发布局。

    用法：`wrap = LiftWrap(card, space=4)`，把 wrap 放进布局，
    `wrap.set_lifted(True)` 或 hover 自动触发。
    """

    def __init__(self, card: QWidget, space: int = 4, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._card = card
        self._space = space
        self._lift = 0.0
        self._hoverable = False
        card.setParent(self)
        self._anim = QPropertyAnimation(self, b"lift", self)
        self._anim.setDuration(K.DUR_MICRO)
        self._anim.setEasingCurve(ease())
        self.setMouseTracking(True)

    def set_hoverable(self, value: bool) -> None:
        self._hoverable = value

    def get_lift(self) -> float:
        return self._lift

    def set_lift(self, value: float) -> None:
        self._lift = value
        self._place()

    lift = Property(float, get_lift, set_lift)

    def _place(self) -> None:
        y = int(round(self._space - self._lift))
        self._card.setGeometry(0, y, self.width(), max(0, self.height() - self._space))

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._place()
        super().resizeEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self._hoverable:
            self._anim.stop()
            self._anim.setStartValue(self._lift)
            self._anim.setEndValue(2.0)
            self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hoverable:
            self._anim.stop()
            self._anim.setStartValue(self._lift)
            self._anim.setEndValue(0.0)
            self._anim.start()
        super().leaveEvent(event)


def pop_in(widget: QWidget, ms: int = K.DUR_UI) -> None:
    """浮层出现：只动透明度，避免 QWidget 无 transform 带来的抖动。"""
    fade_in(widget, ms=ms)


def animate_value(owner: QObject, prop: bytes, start: float, end: float, ms: int = K.DUR_DATA):
    anim = QPropertyAnimation(owner, prop, owner)
    anim.setDuration(duration(ms))
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(ease())
    anim.start()
    return anim


def shadow_params(theme_key: str = "SHADOW_ALPHA") -> tuple[int, int]:
    """返回 (模糊半径, Y 偏移)，按主题给不同强度。"""
    alpha = int(K.t(theme_key) or 20)
    return (18 if alpha < 40 else 26), (5 if alpha < 40 else 7)


def reveal_rect(widget: QWidget) -> QRect:
    return QRect(widget.mapToGlobal(widget.rect().topLeft()), widget.size())


class PageFade(QWidget):
    """切页转场：把旧页面截图盖在新页面上，淡出并轻微放大。

    比给整页挂 QGraphicsOpacityEffect 更稳：不参与布局，也不会和子控件特效嵌套。
    """

    def __init__(self, parent: QWidget, pixmap) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._opacity = 1.0
        self._scale = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        if parent is not None:
            self.setGeometry(parent.rect())
        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(K.DUR_LAYOUT)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self._anim.finished.connect(self.deleteLater)

    def get_progress(self) -> float:
        return self._opacity

    def set_progress(self, value: float) -> None:
        self._opacity = 1.0 - value
        self._scale = 1.0 + 0.004 * value
        self.update()

    progress = Property(float, get_progress, set_progress)

    def start(self) -> None:
        self.show()
        self.raise_()
        self._anim.start()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(max(0.0, self._opacity))
        width = self.width() * self._scale
        height = self.height() * self._scale
        x = (self.width() - width) / 2
        y = (self.height() - height) / 2
        painter.drawPixmap(QRectF(x, y, width, height), self._pixmap, QRectF(self._pixmap.rect()))
        painter.end()
