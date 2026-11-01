"""可复用组件 —— 只依赖 tokens / icons / motion，不碰业务。"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable, Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
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

from . import icons, motion
from . import tokens as K

ASSETS_HINT = "assets"


# ---------------------------------------------------------------- 基础
def label(text: str = "", name: str = "Body", parent: QWidget | None = None) -> QLabel:
    widget = QLabel(text, parent)
    widget.setObjectName(name)
    return widget


def font(size: int, weight: int = 400, mono: bool = False) -> QFont:
    result = QFont("Cascadia Mono" if mono else "Microsoft YaHei UI")
    result.setPixelSize(size)
    result.setWeight(QFont.Weight(weight))
    if mono:
        result.setStyleHint(QFont.StyleHint.Monospace)
    return result


def elide(text: str, widget: QWidget, width: int) -> str:
    metrics = QFontMetrics(widget.font())
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(20, width))


def hline(color: str | None = None, height: int = 1) -> QFrame:
    line = QFrame()
    line.setFixedHeight(height)
    line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    line.setStyleSheet(f"background: {color or K.t('LINE')}; border: none;")
    return line


def soft_shadow(widget: QWidget, blur: int = 18, dy: int = 5) -> QGraphicsDropShadowEffect:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    alpha = int(K.t("SHADOW_ALPHA") or 20)
    effect.setColor(K.qcolor(K.t("SHADOW"), alpha))
    widget.setGraphicsEffect(effect)
    return effect


class Card(QFrame):
    """白卡：1px 描边 + 18px 圆角，可选柔和阴影。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        padding: tuple[int, int, int, int] = (20, 18, 20, 18),
        spacing: int = 12,
        shadow: bool = False,
        name: str = "Card",
    ) -> None:
        super().__init__(parent)
        self.setObjectName(name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(*padding)
        self.box.setSpacing(spacing)
        if shadow:
            soft_shadow(self)


class NavButton(QPushButton):
    """侧栏导航项：自绘选中态背景与图标着色。"""

    def __init__(self, key: str, text: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Nav")
        self.key = key
        self.icon_name = icon_name
        self.active = False
        self.setFixedHeight(K.NAV_ITEM_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(17, 17))
        self._sync_icon()

    def set_active(self, value: bool) -> None:
        if self.active == value:
            return
        self.active = value
        self.setProperty("active", "true" if value else "false")
        self._sync_icon()
        self.style().unpolish(self)
        self.style().polish(self)

    def _sync_icon(self) -> None:
        color = str(K.t("SIDEBAR_INK")) if self.active else str(K.t("SIDEBAR_INK_2"))
        self.setIcon(icons.icon(self.icon_name, color, 17))


class Badge(QLabel):
    _TONES = {"ok": "BadgeOk", "off": "BadgeOff", "warn": "BadgeWarn", "err": "BadgeErr", "accent": "BadgeAccent"}

    def __init__(self, text: str = "", tone: str = "off", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName(self._TONES.get(tone, "Badge"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_tone(self, tone: str) -> None:
        self.setObjectName(self._TONES.get(tone, "Badge"))
        self.style().unpolish(self)
        self.style().polish(self)

    def set_text(self, text: str) -> None:
        self.setText(text)


class StatusDot(QWidget):
    def __init__(self, tone: str = "ok", size: int = 7, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tone = tone
        self._size = size
        self.setFixedSize(size + 2, size + 2)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_tone(self, tone: str) -> None:
        if tone != self._tone:
            self._tone = tone
            self.update()

    def _color(self) -> QColor:
        if self._tone == "ok":
            return K.qcolor(K.t("OK"))
        if self._tone == "err":
            return K.qcolor(K.t("ERR"))
        if self._tone == "warn":
            return K.qcolor(K.t("WARN"))
        return K.qcolor(K.t("INK_3"))

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color())
        offset = (self.width() - self._size) / 2
        painter.drawEllipse(QRectF(offset, offset, self._size, self._size))
        painter.end()


class IconTile(QWidget):
    """圆角方形图标承托（参考图状态卡左上角）。"""

    def __init__(self, icon_name: str, size: int = 38, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon = icon_name
        self._size = size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(K.qcolor(K.t("SURFACE_2")))
        painter.drawRoundedRect(QRectF(0, 0, self._size, self._size), self._size * 0.32, self._size * 0.32)
        pixmap = icons.pixmap(self._icon, str(K.t("INK")), 19)
        painter.drawPixmap(
            QPointF((self._size - 19) / 2, (self._size - 19) / 2),
            pixmap,
        )
        painter.end()


class Avatar(QWidget):
    """圆形头像：优先用应用图标，退化到首字母。"""

    def __init__(self, path: str | None = None, size: int = 32, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self._pixmap = QPixmap(path) if path else QPixmap()
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addEllipse(QRectF(0, 0, self._size, self._size))
        painter.setClipPath(path)
        if not self._pixmap.isNull():
            dpr = max(1.0, float(self.devicePixelRatioF()))
            scaled = self._pixmap.scaled(
                int(self._size * dpr),
                int(self._size * dpr),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            painter.drawPixmap(0, 0, scaled)
        else:
            painter.fillRect(0, 0, self._size, self._size, K.qcolor(K.t("SURFACE_3")))
            painter.setPen(K.qcolor(K.t("INK_2")))
            painter.setFont(font(13, 600))
            painter.drawText(QRectF(0, 0, self._size, self._size), Qt.AlignmentFlag.AlignCenter, "im")
        painter.end()


# ---------------------------------------------------------------- 按钮
def primary_button(text: str, icon_name: str | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("Primary")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        button.setIcon(icons.icon(icon_name, str(K.t("ACCENT_INK")), 15))
        button.setIconSize(QSize(15, 15))
        button.setProperty("iconName", icon_name)
    return button


def secondary_button(text: str, icon_name: str | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("Secondary")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        button.setIcon(icons.icon(icon_name, str(K.t("INK")), 15))
        button.setIconSize(QSize(15, 15))
        button.setProperty("iconName", icon_name)
    return button


def quiet_button(text: str, icon_name: str | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("Quiet")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        button.setIcon(icons.icon(icon_name, str(K.t("INK_2")), 14))
        button.setIconSize(QSize(14, 14))
        button.setProperty("iconName", icon_name)
    return button


def icon_button(icon_name: str, tooltip: str = "", size: int = 16) -> QPushButton:
    button = QPushButton()
    button.setObjectName("IconBtn")
    button.setIcon(icons.icon(icon_name, str(K.t("INK_2")), size))
    button.setIconSize(QSize(size, size))
    button.setFixedSize(size + 16, size + 16)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
    button.setProperty("iconName", icon_name)
    return button


# ---------------------------------------------------------------- 结构件
class SectionTitle(QWidget):
    def __init__(self, text: str, note: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.title = label(text, "CardTitle")
        row.addWidget(self.title)
        self.note = label(note, "Hint")
        row.addWidget(self.note)
        row.addStretch(1)
        self.action_slot = row

    def add_action(self, widget: QWidget) -> None:
        self.action_slot.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)


class StatusCard(QWidget):
    """首页四状态卡：图标 + 标题 + 状态点 + 关键摘要 + chevron，整卡可点。"""

    clicked = Signal()

    def __init__(self, title: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hover = False
        self.card = QFrame()
        self.card.setObjectName("StatusCard")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._shadow = soft_shadow(self.card, 16, 4)
        self._wrap = motion.LiftWrap(self.card, space=5)
        self._wrap.set_hoverable(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._wrap)

        box = QVBoxLayout(self.card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(11)
        top.addWidget(IconTile(icon_name, 38), 0, Qt.AlignmentFlag.AlignTop)
        text_box = QVBoxLayout()
        text_box.setSpacing(4)
        self.title = label(title, "H3")
        text_box.addWidget(self.title)
        state_row = QHBoxLayout()
        state_row.setSpacing(6)
        self.dot = StatusDot("idle", 6)
        state_row.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.state = label("—", "Muted")
        state_row.addWidget(self.state)
        state_row.addStretch(1)
        text_box.addLayout(state_row)
        top.addLayout(text_box, 1)
        box.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.info = ElidedLabel("—", "Body")
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bottom.addWidget(self.info, 1)
        self.chevron = QLabel()
        self.chevron.setPixmap(icons.pixmap("chevron-right", str(K.t("INK_3")), 15))
        bottom.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(bottom)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(K.STATUS_CARD_H + 5)
        # 卡片与宿主都让出鼠标事件，点击落到整卡容器上（无内部可交互控件）
        for widget in (self.card, self._wrap):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_state(self, tone: str, text: str, info: str) -> None:
        self.dot.set_tone(tone)
        color = {
            "ok": K.t("OK"),
            "err": K.t("ERR"),
            "warn": K.t("WARN"),
        }.get(tone, K.t("INK_2"))
        self.state.setText(text)
        self.state.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.info.setText(info)
        self.info.setToolTip(info)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._shadow.setBlurRadius(26)
        self._shadow.setOffset(0, 7)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._shadow.setBlurRadius(16)
        self._shadow.setOffset(0, 4)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusOutEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self.hasFocus():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(K.qcolor(K.t("INK_3")), 1.5))
        painter.drawRoundedRect(QRectF(1.5, 3.0, self.width() - 3, self.height() - 6), K.RADIUS_LG, K.RADIUS_LG)
        painter.end()


class Stat(QWidget):
    """标签 + 大数字 + 单位 + 副行（等宽数字）。"""

    def __init__(self, title: str, value: str = "—", unit: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        box.addWidget(label(title, "StatLabel"))
        row = QHBoxLayout()
        row.setSpacing(5)
        self.value = label(value, "StatValue")
        row.addWidget(self.value, 0, Qt.AlignmentFlag.AlignBottom)
        self.unit = label(unit, "StatSub")
        row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        box.addLayout(row)
        self.sub = label("", "StatSub")
        box.addWidget(self.sub)

    def set(self, value: str, unit: str | None = None, sub: str | None = None) -> None:
        self.value.setText(value)
        if unit is not None:
            self.unit.setText(unit)
        if sub is not None:
            self.sub.setText(sub)


class MetricCell(QWidget):
    """CPU / 内存 / 磁盘：小标签 + 数值 + 细进度条。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(5)
        box.addWidget(label(title, "Hint"))
        self.value = label("—", "H3")
        box.addWidget(self.value)
        self.bar = _Bar()
        box.addWidget(self.bar)

    def set_value(self, percent: float | None) -> None:
        if percent is None:
            self.value.setText("—")
            self.bar.set_ratio(None)
            return
        self.value.setText(f"{percent:.0f}%")
        self.bar.set_ratio(max(0.0, min(1.0, percent / 100.0)))


class _Bar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ratio: float | None = None
        self.setFixedHeight(6)

    def set_ratio(self, ratio: float | None) -> None:
        self._ratio = ratio
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(K.qcolor(K.t("SURFACE_3")))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), 6), 3, 3)
        if self._ratio:
            painter.setBrush(K.qcolor(K.t("INK")))
            painter.drawRoundedRect(QRectF(0, 0, max(6.0, self.width() * self._ratio), 6), 3, 3)
        painter.end()


# ---------------------------------------------------------------- 图表
class Sparkline(QWidget):
    """平滑折线 + 极淡面积 + 末端点；无数据时显示基线与「等待数据」。"""

    def __init__(self, height: int = 56, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._labels: list[str] = []
        self.setMinimumHeight(height)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, values: Sequence[float], labels: Sequence[str] | None = None) -> None:
        self._values = [float(v) for v in values]
        self._labels = list(labels or [])
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width, height = self.width(), self.height()
        pad = 5
        base = height - pad
        painter.setPen(QPen(K.qcolor(K.t("LINE")), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(0, base), QPointF(width, base))

        if len(self._values) < 2 or max(self._values) <= 0:
            painter.setPen(K.qcolor(K.t("INK_3")))
            painter.setFont(font(11))
            painter.drawText(QRectF(0, 0, width, height), Qt.AlignmentFlag.AlignCenter, "等待首条数据")
            painter.end()
            return

        top = max(self._values)
        step = width / (len(self._values) - 1)
        points = [
            QPointF(index * step, base - (value / top) * (height - pad * 2 - 6))
            for index, value in enumerate(self._values)
        ]

        path = QPainterPath(points[0])
        for index in range(1, len(points)):
            prev, current = points[index - 1], points[index]
            mid = (prev.x() + current.x()) / 2
            path.cubicTo(QPointF(mid, prev.y()), QPointF(mid, current.y()), current)

        area = QPainterPath(path)
        area.lineTo(QPointF(points[-1].x(), base))
        area.lineTo(QPointF(points[0].x(), base))
        area.closeSubpath()
        gradient = QLinearGradient(0, 0, 0, height)
        gradient.setColorAt(0.0, K.qcolor(K.t("INK"), 26))
        gradient.setColorAt(1.0, K.qcolor(K.t("INK"), 0))
        painter.fillPath(area, gradient)

        painter.setPen(QPen(K.qcolor(K.t("INK")), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(K.qcolor(K.t("OK")))
        painter.drawEllipse(points[-1], 3.0, 3.0)
        painter.end()


# ---------------------------------------------------------------- 日志
_TIME = None


def _split_log(item: dict[str, Any]) -> tuple[str, str, str, str]:
    text = str(item.get("text") or "").strip()
    level = str(item.get("level") or "INFO").upper()
    raw = text

    # journalctl -o short-iso：2026-10-27T09:32:01+0800 host unit[pid]: message
    import re as _re

    match = _re.match(
        r"^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})\S*\s+(?:\S+\s+)?([^\s:]+?)(?:\[\d+\])?:\s*(.*)$",
        raw,
    )
    if match:
        source = match.group(2).replace(".service", "")
        message = match.group(3)
        # 应用自身日志里还套了一层 "2026-10-27 03:32:01 [im_bridge.xxx] "，去掉避免重复
        message = _re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s*", "", message)
        message = _re.sub(r"^\[[^\]]{1,24}\]\s*", "", message)
        return match.group(1), source, message or raw, level

    time_part, source_part, message = "", "", raw
    head, _, rest = raw.partition(" ")
    if len(head) == 8 and head.count(":") == 2:
        time_part = head
        parts = rest.split(" ", 1)
        source_part = parts[0] if parts else ""
        message = parts[1] if len(parts) > 1 else ""
    elif len(raw) > 9 and raw[2] == ":" and raw[5] == ":":
        time_part, message = raw[:8], raw[9:]
    source_part = source_part.split("[")[0]
    return time_part, source_part, message or raw, level


class ElidedLabel(QLabel):
    """按可用宽度省略，避免 QLabel 硬裁切出半个字。"""

    def __init__(self, text: str = "", name: str = "Body", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName(name)
        self._full = text

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._apply()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._apply()
        super().resizeEvent(event)

    def _apply(self) -> None:
        metrics = QFontMetrics(self.font())
        width = max(20, self.width() - 2)
        super().setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, width))


class LogList(QWidget):
    """时间（等宽）+ 来源 + 事件，行高 34、细分隔线。"""

    def __init__(self, limit: int = 5, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.limit = limit
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(0)
        self.empty = QLabel("暂无运行记录")
        self.empty.setObjectName("Hint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._box.addWidget(self.empty)
        self._box.addStretch(1)
        self.rows: list[QWidget] = []
        self._lines: list[dict[str, Any]] = []

    def set_lines(self, lines: Iterable[dict[str, Any]]) -> None:
        self._lines = [line for line in lines if str(line.get("text") or "").strip()]
        self._render()

    def set_limit(self, limit: int) -> None:
        if limit != self.limit:
            self.limit = limit
            self._render()

    def _render(self) -> None:
        for widget in self.rows:
            widget.setParent(None)
            widget.deleteLater()
        self.rows.clear()

        items = self._lines[-self.limit:]
        self.empty.setVisible(not items)
        for index, line in enumerate(items):
            time_part, source, message, level = _split_log(line)
            row = QWidget()
            row.setFixedHeight(34)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 2, 0)
            row_layout.setSpacing(10)

            time_label = label(time_part or "—", "Mono")
            time_label.setFixedWidth(62)
            row_layout.addWidget(time_label, 0, Qt.AlignmentFlag.AlignVCenter)

            source_label = label(source or "系统", "H3")
            source_label.setFixedWidth(60)
            row_layout.addWidget(source_label, 0, Qt.AlignmentFlag.AlignVCenter)

            text_label = ElidedLabel(message, "Muted")
            if level.startswith("E"):
                text_label.setStyleSheet(f"color: {K.t('ERR')};")
            elif level.startswith("W"):
                text_label.setStyleSheet(f"color: {K.t('INK')};")
            text_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            text_label.setToolTip(str(line.get("text") or ""))
            row_layout.addWidget(text_label, 1, Qt.AlignmentFlag.AlignVCenter)

            container = QWidget()
            holder = QVBoxLayout(container)
            holder.setContentsMargins(0, 0, 0, 0)
            holder.setSpacing(0)
            holder.addWidget(row)
            if index < len(items) - 1:
                holder.addWidget(hline())
            self._box.insertWidget(self._box.count() - 1, container)
            self.rows.append(container)


# ---------------------------------------------------------------- 插画
class HeroArt(QWidget):
    """水墨插画：右侧出血、左缘渐隐，圆角与卡片一致。"""

    def __init__(self, path: str, radius: int = 22, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap(path)
        self._radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setMinimumWidth(240)

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), self._radius, self._radius)
        painter.setClipPath(clip)

        # 按设备像素比取源图，避免 125% / 150% 缩放时被二次放大而发虚
        dpr = max(1.0, float(self.devicePixelRatioF()))
        target_h = int(round(self.height() * dpr))
        scaled = self._pixmap.scaledToHeight(target_h, Qt.TransformationMode.SmoothTransformation)
        if scaled.width() < self.width() * dpr:
            scaled = self._pixmap.scaled(
                int(self.width() * dpr), target_h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        scaled.setDevicePixelRatio(dpr)
        x = self.width() - scaled.width() / dpr
        y = (self.height() - scaled.height() / dpr) / 2
        painter.drawPixmap(QPointF(x, y), scaled)

        top_fade = QLinearGradient(0, 0, 0, self.height() * 0.09)
        white = QColor(255, 255, 255)
        stop = QColor(white)
        stop.setAlpha(255)
        gone = QColor(white)
        gone.setAlpha(0)
        top_fade.setColorAt(0.0, stop)
        top_fade.setColorAt(1.0, gone)
        painter.fillRect(QRectF(0, 0, self.width(), self.height() * 0.09), top_fade)

        fade = QLinearGradient(0, 0, self.width() * 0.42, 0)
        fade.setColorAt(0.0, QColor(255, 255, 255, 255))
        fade.setColorAt(0.55, QColor(255, 255, 255, 170))
        fade.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(0, 0, int(self.width() * 0.42) + 2, self.height(), fade)
        painter.end()


class BrandArt(QWidget):
    """品牌卡插画：圆角裁切 + 顶部渐隐。

    这张卡在深色主题下也保持白底（参考图的「明信片」处理），
    所以插画永远按原稿绘制，不做反相——反相会让灰色块面变成一团白雾。
    """

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap(path)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), K.RADIUS_LG, K.RADIUS_LG)
        painter.setClipPath(clip)

        dpr = max(1.0, float(self.devicePixelRatioF()))
        target_h = int(round(self.height() * 0.9 * dpr))
        scaled = self._pixmap.scaledToHeight(target_h, Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        draw_w = scaled.width() / dpr
        draw_h = scaled.height() / dpr
        x = self.width() - draw_w + 10
        y = self.height() - draw_h + 8
        painter.setOpacity(0.96)
        painter.drawPixmap(QPointF(x, y), scaled)

        fade = QLinearGradient(0, 0, 0, self.height() * 0.34)
        white = QColor(255, 255, 255)
        top = QColor(white); top.setAlpha(255)
        mid = QColor(white); mid.setAlpha(170)
        clear = QColor(white); clear.setAlpha(0)
        fade.setColorAt(0.0, top)
        fade.setColorAt(0.45, mid)
        fade.setColorAt(1.0, clear)
        painter.fillRect(QRectF(0, 0, self.width(), self.height() * 0.34), fade)

        bottom = QLinearGradient(0, self.height() * 0.72, 0, self.height())
        clear_top = QColor(white); clear_top.setAlpha(0)
        solid = QColor(white); solid.setAlpha(235)
        bottom.setColorAt(0.0, clear_top)
        bottom.setColorAt(1.0, solid)
        painter.fillRect(QRectF(0, self.height() * 0.72, self.width(), self.height() * 0.28), bottom)
        painter.end()


class Toast(QWidget):
    """右下角轻提示：1px 描边 + 状态点，2.2s / 3.5s 自动消失。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        box = QHBoxLayout(self)
        box.setContentsMargins(14, 10, 16, 10)
        box.setSpacing(9)
        self.dot = StatusDot("ok", 7)
        box.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = QLabel("")
        self.text.setStyleSheet("font-size: 12.5px;")
        box.addWidget(self.text, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._hide)
        self.hide()
        self._shadow = soft_shadow(self, 22, 8)

    def show_message(self, text: str, kind: str = "ok") -> None:
        self.text.setText(text)
        self.dot.set_tone("err" if kind == "err" else "ok")
        self.text.setStyleSheet(
            f"font-size: 12.5px; color: {K.t('INK')};"
        )
        metrics = QFontMetrics(self.text.font())
        width = min(360, metrics.horizontalAdvance(text) + 66)
        self.setFixedWidth(width)
        self.adjustSize()
        window = self.parentWidget()
        if window:
            self.move(window.width() - self.width() - 24, window.height() - self.height() - 24)
        self.show()
        self.raise_()
        motion.pop_in(self)
        self._timer.start(3500 if kind == "err" else 2200)

    def _hide(self) -> None:
        motion.fade_out(self, 120)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(K.qcolor(K.t("LINE")), 1))
        painter.setBrush(K.qcolor(K.t("SURFACE")))
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 12, 12)
        painter.end()


def now_text() -> str:
    return datetime.now().strftime("%H:%M")


def restyle_subnav(nav) -> None:
    """供页面 retheme 调用：二级导航的底色是内联样式，主题切换后需重刷。"""
    if hasattr(nav, "select") and getattr(nav, "_current", ""):
        nav.select(nav._current)
