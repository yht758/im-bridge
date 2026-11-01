"""自绘控件库 v8 —— 「黑白极简 · 二次元水墨」。

与 v7 的区别：
- 黑白灰承担结构，绿色是唯一彩色（状态点 / 徽章 / 开关开启态）；
- 卡片为纯白（浅色内容区）/ 近黑灰（深色内容区），阴影更柔，悬停 ×1.6；
- 新增 StatusCard（四状态卡）、Badge（胶囊徽章）、Toast（轻提示）；
- 颜色全部从 qt_theme 的动态令牌读取，切主题后自绘控件在下一帧自动换色。
"""
from __future__ import annotations

import math
import random
from typing import Callable, Sequence

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import qt_theme as T


# ================================================================ 工具
def qcolor(value: object) -> QColor:
    """把 '#RRGGBB' 或 'rgba(r, g, b, a)' 字符串转成 QColor。"""
    text = str(value or "").strip()
    if text.startswith("rgba(") or text.startswith("rgb("):
        inner = text[text.index("(") + 1 : text.rindex(")")]
        parts = [part.strip() for part in inner.split(",")]
        if len(parts) >= 3:
            try:
                red, green, blue = (int(float(part)) for part in parts[:3])
                alpha = int(float(parts[3]) * 255) if len(parts) > 3 else 255
                return QColor(red, green, blue, alpha)
            except (TypeError, ValueError):
                pass
    return QColor(text)


def alpha(value: object, a: int) -> QColor:
    color = qcolor(value)
    color.setAlpha(max(0, min(255, int(a))))
    return color


def ui_font(size: int = 13, weight: int = 400, mono: bool = False) -> QFont:
    font = QFont()
    if mono:
        font.setFamilies(["Cascadia Code", "Consolas", "Microsoft YaHei UI"])
    else:
        font.setFamilies(["Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI"])
    font.setPixelSize(size)
    font.setWeight(QFont.Weight(weight))
    return font


def fmt_int(value: float | int | None) -> str:
    try:
        return f"{int(round(float(value or 0))):,}"
    except (TypeError, ValueError):
        return "0"


def fmt_tokens(value: float | int | None) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return f"{int(number)}"


def fmt_money(value: float | None) -> str:
    number = float(value or 0)
    if number >= 100:
        return f"¥{number:,.1f}"
    if number >= 1:
        return f"¥{number:.2f}"
    return f"¥{number:.3f}"


def fmt_uptime(seconds: float | int | None) -> str:
    try:
        total = int(float(seconds or 0))
    except (TypeError, ValueError):
        return "—"
    if total <= 0:
        return "—"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分"


# ================================================================ 容器
class HoverCard(QFrame):
    """浅色卡（v8 黑白极简）。悬停时阴影 ×1.6 上浮。

    Qt 的 QGraphicsDropShadowEffect 动画几个属性即可获得「浮起来」的手感，
    比改 geometry（会被布局重置）可靠。
    """

    def __init__(self, alt: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BlockAlt" if alt else "Block")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._alt = alt
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(24)
        self._shadow.setOffset(0, 6)
        self._shadow.setColor(alpha(T.SHADOW, int(T.CARD_SHADOW_ALPHA)))  # type: ignore[arg-type]
        self.setGraphicsEffect(self._shadow)
        self._anim: QVariantAnimation | None = None
        self._shadow_t = 0.0

    # 用进度 0..1 驱动阴影（进入 1，离开 0）
    def _animate_shadow(self, target: float) -> None:
        if self._anim is not None:
            self._anim.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(T.DURATION_HOVER)
        animation.setStartValue(self._shadow_t)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def apply(value: object) -> None:
            try:
                progress = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return
            self._shadow_t = progress
            self._shadow.setBlurRadius(24 + 8 * progress)
            self._shadow.setOffset(0, 6 + 3 * progress)
            base = int(T.CARD_SHADOW_ALPHA)  # type: ignore[arg-type]
            color = alpha(T.SHADOW, min(255, int(base * (1.0 + 0.6 * progress))))  # type: ignore[arg-type]
            self._shadow.setColor(color)
            self.update()

        animation.valueChanged.connect(apply)
        animation.start()
        self._anim = animation

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self._animate_shadow(1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._animate_shadow(0.0)


def block(alt: bool = False, parent: QWidget | None = None) -> HoverCard:
    return HoverCard(alt=alt, parent=parent)


def card(accent: bool = False, flat: bool = False, parent: QWidget | None = None) -> HoverCard:
    return HoverCard(alt=accent, parent=parent)


def inset(parent: QWidget | None = None) -> QFrame:
    frame = QFrame(parent)
    frame.setObjectName("Inset")
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return frame


class Divider(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HLine")
        self.setFixedHeight(1)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


# ================================================================ 背景氛围
class AmbientBackground(QWidget):
    """窗口背景：v8 黑白极简 —— 纯平 $BG 底，无星点无金光。

    保留类名与 set_theme 接口（desktop_qt 在切主题时会调用），
    实现从 v7 的星点/暖光动画简化为纯色填充，消除常驻 40ms 重绘。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Ambient")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._theme = str(T.current_theme())

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(QRectF(self.rect()), qcolor(T.BG))
        painter.end()


class RevealOverlay(QWidget):
    """主题切换的圆形揭示：旧主题截图 → 圆形扩散露出新主题截图。"""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._old = None
        self._new = None
        self._center = QPointF(0.5, 0.5)
        self._progress = 0.0
        self._anim: QVariantAnimation | None = None
        self.hide()

    def start(self, window: QWidget, old_pix, new_pix, center: QPointF) -> None:
        self.setParent(window)
        self.setGeometry(window.rect())
        self._old = old_pix
        self._new = new_pix
        self._center = center
        self._progress = 0.0
        self.show()
        self.raise_()
        animation = QVariantAnimation(self)
        animation.setDuration(T.DURATION_REVEAL)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(T.bezier_curve())

        def apply(value: object) -> None:
            try:
                self._progress = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return
            self.update()

        animation.valueChanged.connect(apply)
        animation.finished.connect(self._finish)
        animation.start()
        self._anim = animation

    def _finish(self) -> None:
        self.hide()
        self.deleteLater()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._old is None or self._new is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._old)

        max_radius = math.hypot(self.width(), self.height())
        radius = max_radius * self._progress
        if radius > 0.5:
            path = QPainterPath()
            path.addEllipse(self._center, radius, radius)
            painter.setClipPath(path)
            painter.drawPixmap(self.rect(), self._new)
            painter.setClipping(False)
            painter.setPen(QPen(alpha(T.ACCENT, 120), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self._center, radius, radius)
        painter.end()


# ================================================================ 导航 / 主题按钮
class NavButton(QPushButton):
    def __init__(self, key: str, text: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.key = key
        self.icon_name = icon_name
        self.setObjectName("Nav")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(T.icon_size(17))
        self.setFixedHeight(38)
        self._active = False
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setProperty("active", "true" if active else "false")
        # 侧栏恒深色：图标永远用侧栏专属灰阶，选中变白，不再用金色
        self.setIcon(T.icon(self.icon_name, str(T.SIDEBAR_INK) if active else str(T.SIDEBAR_INK_2), 17))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        if not self._active:
            self.setIcon(T.icon(self.icon_name, str(T.SIDEBAR_INK), 17))

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        if not self._active:
            self.setIcon(T.icon(self.icon_name, str(T.SIDEBAR_INK_2), 17))


class ThemeToggle(QPushButton):
    """顶部主题切换按钮：太阳 / 月亮。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("IconBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36, 36)
        self.setIconSize(T.icon_size(18))
        self.set_theme(str(T.current_theme()))

    def set_theme(self, theme: str) -> None:
        name = "sun" if theme == "dark" else "moon"
        self.setIcon(T.icon(name, str(T.INK_2), 18))
        self.setAccessibleName("切换到纸白主题" if theme == "dark" else "切换到墨黑主题")


# ================================================================ 状态点
class StatusDot(QWidget):
    """带柔光的状态点；pulse=True 时呼吸（只给关键状态用）。"""

    def __init__(self, color: str = "OK", size: int = 8, pulse: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self._pulse = pulse
        self._glow = 0.0
        self.setFixedSize(size + 8, size + 8)
        self._dot = size
        if pulse:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._beat)
            self._timer.start(1200)

    def _beat(self) -> None:
        animation = QVariantAnimation(self)
        animation.setDuration(1200)
        animation.setStartValue(0.0)
        animation.setKeyValueAt(0.5, 1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.InOutSine)

        def apply(value: object) -> None:
            try:
                self._glow = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return
            self.update()

        animation.valueChanged.connect(apply)
        animation.start()

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def set_pulse(self, pulse: bool) -> None:
        self._pulse = pulse

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        size = self._dot
        offset = (self.width() - size) / 2
        color = qcolor(str(getattr(T, self._color, self._color)) if not str(self._color).startswith(("#", "rgb")) else self._color)
        if self._pulse:
            halo = alpha(color, int(28 + 42 * self._glow))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QRectF(offset - 4, offset - 4, size + 8, size + 8))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(offset, offset, size, size))
        painter.end()


# ================================================================ 数字
class Stat(QWidget):
    """标签 + 数字（支持滚动）+ 单位 + 副行。"""

    def __init__(
        self,
        label: str,
        value: str = "—",
        unit: str = "",
        size: int = 26,
        accent: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._accent = accent
        self._size = size
        self._formatter: Callable[[float], str] | None = None
        self._anim: QVariantAnimation | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self.label = QLabel(label)
        self.label.setObjectName("Eyebrow" if accent else "Label")
        layout.addWidget(self.label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.value = QLabel(value)
        self.value.setObjectName(
            "Gold" if accent else ("Display" if size >= 40 else "StatValue" if size >= 22 else "Value")
        )
        row.addWidget(self.value)
        self.unit = QLabel(unit)
        self.unit.setObjectName("Label")
        self.unit.setStyleSheet("padding-bottom:6px;")
        row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        layout.addLayout(row)

        self.sub = QLabel("")
        self.sub.setObjectName("Muted")
        layout.addWidget(self.sub)
        self.sub.hide()

    def _style(self, color: str | None = None) -> str:
        tone = color or (str(T.ACCENT) if self._accent else str(T.INK))
        return f"color:{tone};"

    def _apply_color(self, color: str | None = None) -> None:
        # 不传颜色就清空 inline 样式，让 QSS 的颜色随主题自动切换
        self.value.setStyleSheet(self._style(color) if color else "")

    def set(self, value: str, sub: str = "", color: str | None = None) -> None:
        self._stop()
        self._formatter = None
        self.value.setText(value)
        self._apply_color(color)
        self._set_sub(sub)

    def set_value(
        self,
        number: float,
        formatter: Callable[[float], str] = fmt_tokens,
        sub: str = "",
        color: str | None = None,
        animate: bool = True,
    ) -> None:
        """数字滚动：从 0 滚到目标值。重复调用会从当前显示值继续，避免跳变。"""
        try:
            target = float(number or 0)
        except (TypeError, ValueError):
            target = 0.0
        self._formatter = formatter
        self._apply_color(color)
        self._set_sub(sub)
        if not animate:
            self.value.setText(formatter(target))
            return
        start = 0.0
        try:
            start = float(str(self.value.text()).replace(",", "").replace("¥", "")) if self.value.text() not in ("", "—") else 0.0
        except ValueError:
            start = 0.0
        if abs(target - start) < 1e-9:
            self.value.setText(formatter(target))
            return
        self._stop()
        animation = QVariantAnimation(self)
        animation.setDuration(T.DURATION_NUM)
        animation.setStartValue(start)
        animation.setEndValue(target)
        animation.setEasingCurve(T.bezier_curve())

        def apply(value: object) -> None:
            try:
                current = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return
            self.value.setText(formatter(current))

        animation.valueChanged.connect(apply)
        animation.start()
        self._anim = animation

    def _stop(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

    def _set_sub(self, sub: str) -> None:
        if sub:
            self.sub.setText(sub)
            self.sub.show()
        else:
            self.sub.hide()


class KVRow(QWidget):
    """一行「标签 — 值」，标签固定宽，值右对齐。"""

    def __init__(self, key: str, value: str = "—", label_width: int = 88, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.key_label = QLabel(key)
        self.key_label.setObjectName("Label")
        self.key_label.setFixedWidth(label_width)
        layout.addWidget(self.key_label)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("Body")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.value_label, 1)

    def set(self, value: str, color: str | None = None) -> None:
        self.value_label.setText(value)
        self.value_label.setStyleSheet(f"color:{color};" if color else "")


class SectionHeader(QWidget):
    def __init__(self, title: str, note: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.title = QLabel(title)
        self.title.setObjectName("SectionTitle")
        layout.addWidget(self.title)
        self.note = QLabel(note)
        self.note.setObjectName("Muted")
        layout.addWidget(self.note)
        # 先挂布局再决定显隐，避免无 parent 时被当顶层窗口创建（v6 启动闪窗根因）
        self.note.setVisible(bool(note))
        layout.addStretch(1)
        self.slot = QHBoxLayout()
        self.slot.setSpacing(8)
        layout.addLayout(self.slot)

    def add_action(self, widget: QWidget) -> None:
        self.slot.addWidget(widget)

    def set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))


class EmptyState(QWidget):
    """空状态：可选黑白插画（mascot）+ 主文案 + 副文案 + 动作按钮。"""

    def __init__(self, text: str = "暂无数据", hint: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 26, 0, 26)
        layout.setSpacing(6)
        layout.addStretch(1)
        self.illustration = QLabel()
        self.illustration.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.illustration.hide()
        layout.addWidget(self.illustration)
        self.label = QLabel(text)
        self.label.setObjectName("Body")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)
        self.hint = QLabel(hint)
        self.hint.setObjectName("Muted")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.hint.setVisible(bool(hint))
        self.action_row = QHBoxLayout()
        self.action_row.setSpacing(8)
        self.action_row.addStretch(1)
        layout.addLayout(self.action_row)
        self.action_row.addStretch(1)
        layout.addStretch(1)

    def set_illustration(self, path, height: int = 140) -> None:
        """设置黑白插画（PNG 路径）；传 None 隐藏。插画永远放在浅色卡内。"""
        if not path:
            self.illustration.hide()
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.illustration.hide()
            return
        scaled = pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
        self.illustration.setPixmap(scaled)
        self.illustration.show()

    def add_action(self, widget: QWidget) -> None:
        self.action_row.addWidget(widget)

    def set_text(self, text: str, hint: str = "") -> None:
        self.label.setText(text)
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))


# ================================================================ 图表
class Sparkline(QWidget):
    """v8：1.5px 墨色折线 + 末端 3px 绿点（当前值），6% 透明面积填充。"""

    def __init__(self, color: str = "", height: int = 96, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._labels: list[str] = []
        self._color = color or str(T.INK)
        self._progress = 1.0
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, values: Sequence[float], labels: Sequence[str] | None = None) -> None:
        self._values = [float(v) for v in values]
        self._labels = list(labels or [])
        self._progress = 0.0
        animation = QVariantAnimation(self)
        animation.setDuration(T.DURATION_NUM)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(T.bezier_curve())

        def apply(value: object) -> None:
            try:
                self._progress = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return
            self.update()

        animation.valueChanged.connect(apply)
        animation.start()
        self._anim = animation

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        area = QRectF(2, 10, self.width() - 4, self.height() - 30)

        painter.setPen(QPen(alpha(T.INK, 18), 1, Qt.PenStyle.DotLine))
        painter.drawLine(QPointF(area.left(), area.bottom()), QPointF(area.right(), area.bottom()))

        if len(self._values) < 2:
            painter.setPen(QPen(alpha(T.INK_3, 200), 1))
            painter.setFont(ui_font(12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无数据")
            painter.end()
            return

        top = max(self._values) or 1.0
        step = area.width() / (len(self._values) - 1)
        visible = max(2, int(len(self._values) * self._progress))
        points = [
            QPointF(area.left() + index * step, area.bottom() - (value / top) * area.height())
            for index, value in enumerate(self._values[:visible])
        ]

        fill = QPainterPath()
        fill.moveTo(points[0])
        for point in points[1:]:
            fill.lineTo(point)
        fill.lineTo(QPointF(points[-1].x(), area.bottom()))
        fill.lineTo(QPointF(points[0].x(), area.bottom()))
        fill.closeSubpath()
        # v8：面积填充只用 6% 透明墨色，去掉金色渐变
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(alpha(self._color, 15))
        painter.drawPath(fill)

        line = QPainterPath()
        line.moveTo(points[0])
        for point in points[1:]:
            line.lineTo(point)
        painter.setPen(QPen(qcolor(self._color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(line)

        # 末端 3px 绿点：唯一彩色，表示当前值
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor(T.OK))
        painter.drawEllipse(points[-1], 3.0, 3.0)

        if self._labels:
            painter.setFont(ui_font(11))
            painter.setPen(QPen(alpha(T.INK_3, 200), 1))
            for index, text in enumerate(self._labels[-4:]):
                x = area.left() + (len(self._values) - 4 + index) * step
                painter.drawText(
                    QRectF(x - 26, area.bottom() + 9, 52, 14),
                    Qt.AlignmentFlag.AlignCenter,
                    text,
                )
        painter.end()


class BarChart(QWidget):
    """v8：常规柱灰阶（SERIES[3]），最新/最大一根墨色（SERIES[0]）；
    悬停显示黑底白字（深色主题白底黑字）数值胶囊。"""

    bar_clicked = Signal(int)

    def __init__(self, color: str = "", height: int = 150, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._labels: list[str] = []
        self._color = color or str(T.SERIES[0])
        self._hover = -1
        self._progress = 1.0
        self.setMinimumHeight(height)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, values: Sequence[float], labels: Sequence[str] | None = None) -> None:
        self._values = [float(v) for v in values]
        self._labels = list(labels or [])
        self._progress = 0.0
        animation = QVariantAnimation(self)
        animation.setDuration(T.DURATION_NUM)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(T.bezier_curve())

        def apply(value: object) -> None:
            try:
                self._progress = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return
            self.update()

        animation.valueChanged.connect(apply)
        animation.start()
        self._anim = animation

    def _plot(self) -> QRectF:
        area = QRectF(2, 10, self.width() - 4, self.height() - 32)
        usable = min(area.width(), float(getattr(T, "CHART_MAX_W", 820)))
        return QRectF(area.left() + (area.width() - usable) / 2, area.top(), usable, area.height())

    def _rects(self) -> list[QRectF]:
        if not self._values:
            return []
        count = len(self._values)
        plot = self._plot()
        slot = plot.width() / count
        width = max(6.0, min(30.0, slot * 0.44))
        top = max(self._values) or 1.0
        rects = []
        for index, value in enumerate(self._values):
            height = max(3.0, (value / top) * (plot.height() - 2) * self._progress)
            x = plot.left() + slot * index + (slot - width) / 2
            rects.append(QRectF(x, plot.bottom() - height, width, height))
        return rects

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = -1
        for i, rect in enumerate(self._rects()):
            if rect.adjusted(-8, 0, 8, 14).contains(event.position()):
                index = i
                break
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        if self._hover != -1:
            self._hover = -1
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if not self._values:
            painter.setPen(QPen(alpha(T.INK_3, 200), 1))
            painter.setFont(ui_font(12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无数据")
            painter.end()
            return

        plot = self._plot()
        # 三条水平参考线
        painter.setPen(QPen(alpha(T.INK, 16), 1))
        for ratio in (0.25, 0.5, 0.75):
            y = plot.bottom() - plot.height() * ratio
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        rects = self._rects()
        last = len(rects) - 1
        gray = str(T.SERIES[3])
        gray_hover = str(T.SERIES[2])
        for index, rect in enumerate(rects):
            if rect.height() < 1:
                continue
            radius = min(6.0, rect.width() / 2)
            # v8：最后一根用 SERIES[0]（墨色）强调，其余灰阶
            if index == last:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(alpha(self._color, 245))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(qcolor(gray_hover if index == self._hover else gray))
            painter.drawRoundedRect(rect, radius, radius)
            if index == self._hover and index != last:
                painter.setPen(QPen(qcolor(self._color), 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect, radius, radius)

        painter.setFont(ui_font(11))
        painter.setPen(QPen(alpha(T.INK_3, 220), 1))
        slot = self._plot().width() / max(1, len(self._labels))
        step = max(1, math.ceil(46 / slot) if slot > 0 else 1)
        for index, label_text in enumerate(self._labels):
            if index % step:
                continue
            rect = rects[index] if rects else QRectF()
            painter.drawText(
                QRectF(rect.center().x() - 26, self.height() - 20, 52, 14),
                Qt.AlignmentFlag.AlignCenter,
                label_text,
            )

        if self._hover >= 0 and self._hover < len(self._values):
            rect = rects[self._hover]
            text = fmt_tokens(self._values[self._hover])
            metrics = QFontMetrics(ui_font(11, 600))
            width = metrics.horizontalAdvance(text) + 18
            box = QRectF(rect.center().x() - width / 2, max(0, rect.top() - 26), width, 22)
            # v8：黑底白字（深色主题白底黑字）数值胶囊
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(alpha(T.ACCENT, 240))
            painter.drawRoundedRect(box, 7, 7)
            painter.setPen(QPen(qcolor(T.ACCENT_INK), 1))
            painter.setFont(ui_font(11, 600))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()


class Donut(QWidget):
    """环形占比：金色系顺序色，入场时从 12 点方向生长。"""

    def __init__(self, size: int = 148, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[tuple[str, float, str]] = []
        self._center = ""
        self._sub = ""
        self._progress = 1.0
        self.setMinimumSize(size, size)

    def set_data(self, segments: Sequence[tuple[str, float, str]], center: str = "", sub: str = "") -> None:
        self._segments = list(segments)
        self._center = center
        self._sub = sub
        self._progress = 0.0
        animation = QVariantAnimation(self)
        animation.setDuration(T.DURATION_NUM)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(T.bezier_curve())

        def apply(value: object) -> None:
            try:
                self._progress = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return
            self.update()

        animation.valueChanged.connect(apply)
        animation.start()
        self._anim = animation

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height())
        ox = (self.width() - side) / 2
        oy = (self.height() - side) / 2
        thickness = max(10.0, side * 0.095)
        rect = QRectF(ox + thickness / 2 + 2, oy + thickness / 2 + 2, side - thickness - 4, side - thickness - 4)

        painter.setPen(QPen(qcolor(T.PANEL_3), thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 0, 360 * 16)

        total = sum(value for _name, value, _color in self._segments) or 0
        if total > 0:
            start = 90 * 16
            for _name, value, color in self._segments:
                span = int(-(value / total) * 360 * 16 * self._progress)
                painter.setPen(QPen(qcolor(color), thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawArc(rect, start, span)
                start += span

        if self._center:
            painter.setPen(QPen(qcolor(T.INK), 1))
            painter.setFont(ui_font(max(15, int(side * 0.13)), 700))
            painter.drawText(
                QRectF(ox, oy + side * 0.31, side, side * 0.22),
                Qt.AlignmentFlag.AlignCenter,
                self._center,
            )
        if self._sub:
            painter.setPen(QPen(qcolor(T.INK_3), 1))
            painter.setFont(ui_font(max(11, int(side * 0.085))))
            painter.drawText(
                QRectF(ox, oy + side * 0.53, side, side * 0.16),
                Qt.AlignmentFlag.AlignCenter,
                self._sub,
            )
        painter.end()


class LegendRow(QWidget):
    def __init__(self, color: str, name: str, value: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        swatch = QLabel()
        swatch.setFixedSize(9, 9)
        swatch.setStyleSheet(f"background:{color}; border-radius:4px;")
        layout.addWidget(swatch)
        self.name = QLabel(name)
        self.name.setObjectName("Body")
        layout.addWidget(self.name, 1)
        self.value = QLabel(value)
        self.value.setObjectName("Body")
        layout.addWidget(self.value)

    def set(self, name: str, value: str) -> None:
        self.name.setText(name)
        self.value.setText(value)


# ================================================================ 开关
class ToggleSwitch(QWidget):
    """v8 开关：开 = 绿底白球（唯一彩色），关 = PANEL_3 底灰球。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = checked
        self._pos = 1.0 if checked else 0.0
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(lambda: self.set_knob(1.0 if self._checked else 0.0))

    def get_knob(self) -> float:
        return self._pos

    def set_knob(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = Property(float, get_knob, set_knob)

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        if checked == self._checked:
            return
        self._checked = checked
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()
        self.toggled.emit(checked)

    def mousePressEvent(self, _event) -> None:  # noqa: N802
        self._checked = not self._checked
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if self._checked else 0.0)
        self._anim.start()
        self.toggled.emit(self._checked)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(0, 1, self.width(), self.height() - 2)
        off = qcolor(T.PANEL_3)
        on = qcolor(T.OK)
        color = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)

        knob = self.height() - 6
        x = 3 + (self.width() - knob - 6) * self._pos
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(alpha("#FFFFFF", 235) if self._pos > 0.5 else alpha(T.INK_3, 220))
        painter.drawEllipse(QRectF(x, 3, knob, knob))
        painter.end()


# ================================================================ v8 新组件
class Badge(QLabel):
    """胶囊徽章：OK_SOFT_BG 底 + OK 字（绿） / PANEL_2 底灰字 / 黑底白字强调。"""

    def __init__(self, text: str = "", tone: str = "ok", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        name = {"ok": "BadgeOk", "off": "BadgeOff", "warn": "BadgeWarn", "err": "BadgeErr"}.get(
            tone, "BadgeAccent"
        )
        if tone == "accent":
            name = "BadgeAccent"
        self.setObjectName(name)
        self.style().unpolish(self)
        self.style().polish(self)


class StatusCard(HoverCard):
    """首页四状态卡（DESIGN_SPEC §5.1）。

    结构：40×40 图标底 + 卡名 + StatusDot/状态文字 + 底部信息行 + chevron。
    三态（UX_SPEC §1）：loading（灰点「获取中…」）/ idle（灰点「未连接」）/
    offline（砖红点「离线」）；绿点仅表示真实运行/在线。整卡可点击。
    """

    clicked = Signal()

    def __init__(self, title: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 14, 14)
        outer.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(12)
        icon_holder = QFrame()
        icon_holder.setObjectName("Inset")
        icon_holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        icon_holder.setFixedSize(40, 40)
        icon_layout = QVBoxLayout(icon_holder)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_holder_icon = QLabel()
        icon_holder_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_name = icon_name
        self._icon_label = icon_holder_icon
        icon_layout.addWidget(icon_holder_icon)
        top.addWidget(icon_holder)

        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("Value")
        text_box.addWidget(self.title_label)
        state_row = QHBoxLayout()
        state_row.setSpacing(6)
        self.dot = StatusDot("INK_3", 7)
        state_row.addWidget(self.dot)
        self.state_label = QLabel("获取中…")
        self.state_label.setObjectName("Muted")
        state_row.addWidget(self.state_label)
        state_row.addStretch(1)
        text_box.addLayout(state_row)
        top.addLayout(text_box, 1)
        outer.addLayout(top)
        outer.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.info_label = QLabel("获取中…")
        self.info_label.setObjectName("Body")
        self.info_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bottom.addWidget(self.info_label, 1)
        self.chevron = QLabel()
        self.chevron.setPixmap(T.glyph("chevron-right", str(T.INK_3), 16))
        bottom.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(bottom)

        self._refresh_icon()

    def _refresh_icon(self) -> None:
        self._icon_label.setPixmap(T.glyph(self._icon_name, str(T.INK), 18))

    def set_state(self, state: str, status_text: str = "", info_text: str = "") -> None:
        """state: 'ok' / 'loading' / 'idle' / 'offline'。"""
        mapping = {
            "ok": ("OK", status_text or "在线", str(T.OK)),
            "loading": ("INK_3", status_text or "获取中…", str(T.INK_3)),
            "idle": ("INK_3", status_text or "未知", str(T.INK_2)),
            "offline": ("ERR", status_text or "离线", str(T.ERR)),
        }
        dot_color, text, text_color = mapping.get(state, mapping["loading"])
        self.dot.set_color(dot_color)
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"color:{text_color};" if state == "ok" else "")
        self.info_label.setText(info_text or "—")

    def set_info(self, info_text: str) -> None:
        self.info_label.setText(info_text)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Toast(QLabel):
    """右下角轻提示（UX_SPEC §6）：PANEL 底 + 状态点 + 12.5px 文本，自动淡出。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ToastLabel")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setMaximumWidth(360)
        self.setWordWrap(True)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._anim: QVariantAnimation | None = None

    def _apply_style(self) -> None:
        self.setStyleSheet(
            "QLabel#ToastLabel {"
            f" background: {T.PANEL};"
            f" border: 1px solid {T.BORDER};"
            " border-radius: 12px;"
            " padding: 9px 16px 9px 12px;"
            " font-size: 12.5px;"
            "}"
        )

    def show_message(self, text: str, kind: str = "ok") -> None:
        self._apply_style()  # 每次显示时按当前主题重建样式
        dot_color = {"ok": T.OK, "err": T.ERR, "busy": T.INK_3}.get(kind, T.OK)
        # 状态点用富文本着色（QLabel 支持富文本）
        self.setText(f'<span style="color:{dot_color};">●</span>&nbsp; {text}')
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            margin = 24
            x = parent.width() - self.width() - margin
            y = parent.height() - self.height() - margin
            self.move(max(margin, x), max(margin, y))
        self.show()
        self.raise_()
        self.setGraphicsEffect(None)
        self._timer.start(2200 if kind != "err" else 3500)

    def _fade_out(self) -> None:
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        animation = QVariantAnimation(self)
        animation.setDuration(200)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def apply(value: object) -> None:
            try:
                effect.setOpacity(float(value))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return

        animation.valueChanged.connect(apply)
        animation.finished.connect(self.hide)
        animation.start()
        self._anim = animation


__all__ = [
    "AmbientBackground",
    "Badge",
    "BarChart",
    "Divider",
    "Donut",
    "EmptyState",
    "HoverCard",
    "KVRow",
    "LegendRow",
    "NavButton",
    "RevealOverlay",
    "SectionHeader",
    "Sparkline",
    "Stat",
    "StatusCard",
    "StatusDot",
    "ThemeToggle",
    "Toast",
    "ToggleSwitch",
    "alpha",
    "block",
    "card",
    "fmt_int",
    "fmt_money",
    "fmt_tokens",
    "fmt_uptime",
    "inset",
    "qcolor",
    "ui_font",
]
