"""IM Bridge 控制台（PySide6）—— 「仪器」v5。

v4 用渐变、玻璃卡片、发光状态点做「花哨」，正好是 taste-skill 与 impeccable
两份设计规则里被逐条点名的反模式。v5 换一种做法：

- **不用自绘标题栏与半透明窗口**：直接使用系统原生窗口框。
  这既符合「不做装饰性 chrome」，也顺手消除了启动时分层窗口造成的闪烁。
- **层次靠底色台阶**（#101211 → #171A18 → #1E2220 → #262B28），不用边框、不用阴影。
- **只有一个强调色**（#FF6A3D），只出现在三处：主按钮、当前导航项、今日花费。
- **字号只有五档**：44 / 26 / 16 / 13 / 12；大数字不再是等宽字体。
- **布局不做「一排相同卡片」**：概览是一句大状态 + 一条趋势 + 一列活动，信息密度不对称。

业务逻辑与 v4 完全一致，只重排了展示层。
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import local_rvc
import config_store
import qt_theme as T
import remote
import widgets as W
from ui import components as C
from ui import icons as UI_ICONS
from ui import motion as M
from ui import tokens as K
from ui.format import fmt_money, fmt_tokens, fmt_uptime
from ui.home import HomePage
from ui.pages.ai_config import AIConfigPage
from ui.pages.bots import BotsPage
from ui.pages.logs import LogsPage as LogsPageV9
from ui.pages.napcat import NapcatPage as NapcatPageV9
from ui.pages.servers import ServersPage
from ui.pages.settings import SettingsPage as SettingsPageV9

ASSETS = Path(__file__).resolve().parent / "assets"
ICON_PATH = ASSETS / "app.ico"
APP_VERSION = config_store.APP_VERSION
from ui.edition import IS_PERSONAL
WINDOW_TITLE = f"IM Bridge 控制台 {APP_VERSION}" + (" · 特供版" if IS_PERSONAL else "")

PAGE_META: dict[str, tuple[str, str]] = {
    "overview": ("", ""),
    "connection": ("云服务器", "主机档案、连接状态与资源占用"),
    "config": ("AI 配置", "模型、接口与输出节奏"),
    "napcat": ("NapCat", "OneBot 网关容器的状态与更新"),
    "instances": ("QQ Bot", "每个实例对应一个 QQ 号与独立端口"),
    "logs": ("日志记录", "journalctl 实时尾巴，按级别着色"),
    "usage": ("设置", "用量与计费、外观、环境自检"),
}

PAGE_EYEBROW: dict[str, str] = {
    "overview": "OVERVIEW",
    "instances": "INSTANCES",
    "logs": "LOGS",
    "usage": "USAGE",
    "config": "CONFIG",
    "napcat": "NAPCAT",
    "connection": "CONNECTION",
}


def _rounded_pixmap(source: QPixmap, size: int, radius: int) -> QPixmap:
    """把应用图标裁成圆角方块：浅色主题下不再是一块突兀的黑方块。"""
    scaled = source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    if scaled.width() > size or scaled.height() > size:
        scaled = scaled.copy(
            max(0, (scaled.width() - size) // 2),
            max(0, (scaled.height() - size) // 2),
            size,
            size,
        )
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.setClipping(False)
    painter.setPen(QPen(W.qcolor(T.BORDER), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(0.6, 0.6, size - 1.2, size - 1.2), radius, radius)
    painter.end()
    return result


# ---------------------------------------------------------------- 异步执行
class _TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


_PENDING: set[_TaskSignals] = set()


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:  # noqa: D401
        try:
            self.fn()
        except Exception:  # noqa: BLE001
            import traceback

            traceback.print_exc()


def run_async(
    work: Callable[[], Any],
    done: Callable[[Any], None] | None = None,
    fail: Callable[[str], None] | None = None,
) -> None:
    """工作丢线程池，结果回主线程；信号对象必须持有引用，否则会被 GC。"""
    signals = _TaskSignals()
    _PENDING.add(signals)

    def _on_done(result: Any) -> None:
        try:
            if done:
                done(result)
        finally:
            _PENDING.discard(signals)

    def _on_failed(message: str) -> None:
        try:
            if fail:
                fail(message)
        finally:
            _PENDING.discard(signals)

    signals.done.connect(_on_done)
    signals.failed.connect(_on_failed)

    def runner() -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001
            signals.failed.emit(str(exc) or exc.__class__.__name__)
            return
        signals.done.emit(result)

    QThreadPool.globalInstance().start(_Task(runner))


# ---------------------------------------------------------------- 小工具
def label(text: str = "", obj: str = "", parent: QWidget | None = None) -> QLabel:
    widget = QLabel(text, parent)
    if obj:
        widget.setObjectName(obj)
    return widget


def icon_button(name: str, tooltip: str = "", size: int = 16) -> QPushButton:
    button = QPushButton()
    button.setObjectName("IconBtn")
    button.setIcon(T.icon(name, T.INK_2, size))
    button.setIconSize(T.icon_size(size))
    if tooltip:
        button.setAccessibleName(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedSize(size + 16, size + 16)
    return button


def accent_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("Primary")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def ghost_button(text: str, glyph: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("Ghost")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if glyph:
        button.setIcon(T.icon(glyph, T.INK_2, 14))
        button.setIconSize(T.icon_size(14))
    return button


def quiet_button(text: str, glyph: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("Quiet")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if glyph:
        button.setIcon(T.icon(glyph, T.INK_2, 14))
        button.setIconSize(T.icon_size(14))
    return button


def day_cost(day_item: dict[str, Any], pricing: dict[str, Any], fallback: str = "deepseek-chat") -> float:
    """当天花费：多模型时按各模型分别计价再求和。"""
    models = (day_item or {}).get("models") or {}
    if models:
        return sum(
            remote.cost_of(
                pricing,
                str(name),
                float(item.get("prompt", 0)),
                float(item.get("completion", 0)),
                float(item.get("cached", 0)),
            )
            for name, item in models.items()
        )
    return remote.cost_of(
        pricing,
        fallback,
        float((day_item or {}).get("prompt", 0)),
        float((day_item or {}).get("completion", 0)),
        float((day_item or {}).get("cached", 0)),
    )


# ---------------------------------------------------------------- 页面基类
class BasePage(QWidget):
    def __init__(self, window: "ConsoleWindow") -> None:
        super().__init__()
        self.win = window
        self.setObjectName("Page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def refresh(self) -> None:
        pass

    def enter(self) -> None:
        self.refresh()


# ---------------------------------------------------------------- 概览
class ActivityList(QWidget):
    """v8 首页实时日志行：级别点 + 文本，行高 30，1px 分隔线。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(0)
        self.empty = W.EmptyState("还没有日志", "启动 bot 后这里会出现最近的运行记录")
        self.layout_.addWidget(self.empty)
        self.layout_.addStretch(1)  # 末尾弹簧：日志行保持自然高度，不被拉伸
        self.rows: list[QWidget] = []

    def set_lines(self, lines: list[dict[str, Any]], limit: int = 6) -> None:
        for widget in self.rows:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        self.rows.clear()

        items = [line for line in lines if str(line.get("text", "")).strip()][-limit:]
        self.empty.setVisible(not items)
        for line in items:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 6, 0)
            row_layout.setSpacing(9)
            row.setFixedHeight(30)
            level = str(line.get("level") or "INFO").upper()
            # v8 日志级别映射：INFO→INK_2，WARN→INK（加粗），ERROR→ERR
            if "ERROR" in level:
                color, weight = str(T.ERR), 600
            elif "WARN" in level:
                color, weight = str(T.INK), 600
            else:
                color, weight = str(T.INK_2), 400
            dot = W.StatusDot(color, 5)
            row_layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
            text = label(str(line.get("text", "")))
            text.setObjectName("Body")
            text.setWordWrap(False)
            text.setStyleSheet(f"color:{color}; font-weight:{weight}; font-size:12.5px;")
            # 长日志行不能撑大页面的最小宽度，否则 QScrollArea 会把页面
            # 设得比视口更宽，右侧内容被裁。
            text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            row_layout.addWidget(text, 1)
            line_frame = QFrame()
            line_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            line_frame.setStyleSheet("background: transparent; border-bottom: 1px solid %s;" % T.BORDER)
            inner = QVBoxLayout(line_frame)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.addWidget(row)
            self.layout_.insertWidget(self.layout_.count() - 1, line_frame)
            self.rows.append(line_frame)


def _greeting() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 11:
        return "早上好"
    if 11 <= hour < 13:
        return "中午好"
    if 13 <= hour < 18:
        return "下午好"
    return "晚上好"


class InstancesPage(BasePage):
    # 列收窄策略（UX_SPEC §4）：宽度不足时按序隐藏 人设 → OneBot → PID → 配置文件
    _NARROW_COLUMNS = (3, 4, 7, 5)

    def __init__(self, window: "ConsoleWindow") -> None:
        super().__init__(window)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.btn_new = accent_button("新建实例")
        self.btn_enter = ghost_button("进入配置")
        self.btn_start = ghost_button("启动")
        self.btn_stop = ghost_button("停止")
        self.btn_restart = ghost_button("重启")
        self.btn_delete = quiet_button("删除")
        self.btn_reload = quiet_button("刷新", "refresh")
        for button in (self.btn_new, self.btn_enter, self.btn_start, self.btn_stop, self.btn_restart):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        self.count_label = label("", "Muted")
        toolbar.addWidget(self.count_label)
        toolbar.addWidget(self.btn_delete)
        toolbar.addWidget(self.btn_reload)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["实例 ID", "名称", "QQ", "人设", "OneBot 地址", "配置文件", "状态", "PID"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)  # v8：行高 40
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 110), (2, 110), (6, 84), (7, 70)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        self.table.setMinimumHeight(120)
        table_box = W.block()
        table_layout = QVBoxLayout(table_box)
        table_layout.setContentsMargins(18, 14, 18, 12)
        table_layout.setSpacing(8)
        table_layout.addWidget(self.table)

        # v8 空状态（未连接 / 无实例），带黑白立绘
        self.empty_state = W.EmptyState("未连接到服务器", "先在「连接」页完成连接")
        self.empty_state.set_illustration(ASSETS / "illustrations" / "empty.png", 120)
        btn_connect = ghost_button("去连接")
        btn_connect.clicked.connect(lambda: self.win.switch_page("connection"))
        self.empty_state.add_action(btn_connect)
        self.empty_state.hide()
        table_layout.addWidget(self.empty_state)
        layout.addWidget(table_box)
        layout.addStretch(1)
        layout.addWidget(label("双击一行进入该实例配置；删除会把配置与数据备份到 backups/instances/。", "Muted"))

        self.btn_new.clicked.connect(self.win.open_create_dialog)
        self.btn_enter.clicked.connect(self._enter)
        self.btn_start.clicked.connect(lambda: self._action("start"))
        self.btn_stop.clicked.connect(lambda: self._action("stop"))
        self.btn_restart.clicked.connect(lambda: self._action("restart"))
        self.btn_delete.clicked.connect(self._delete)
        self.btn_reload.clicked.connect(lambda: self.win.reload_instances())
        self.table.doubleClicked.connect(lambda _i: self._enter())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 列收窄：可用宽度不够时按优先级隐藏次要列
        hidden = 0
        for offset, column in enumerate(self._NARROW_COLUMNS):
            hide = self.width() < 1180 - offset * 130
            self.table.setColumnHidden(column, hide)
            hidden += int(hide)
        self._narrow_hidden = hidden

    def apply(self, instances: list[dict[str, Any]]) -> None:
        self.table.setRowCount(len(instances))
        self.table.setFixedHeight(58 + max(1, len(instances)) * 40)
        self.count_label.setText(f"{len(instances)} 个")
        self.table.setVisible(bool(instances))
        self.empty_state.setVisible(not instances)
        for row, item in enumerate(instances):
            status = item.get("status") or {}
            running = bool(status.get("running"))
            config_dir = str(item.get("config_dir") or "")
            if config_dir == ".":
                config_text = "config.json（根）"
            elif config_dir:
                config_text = f"{config_dir}/config.json"
            else:
                config_text = "—"
            values = [
                item.get("id", ""),
                item.get("name", ""),
                item.get("bot_qq", ""),
                item.get("persona", ""),
                item.get("onebot_ws_url", ""),
                config_text,
                "运行中" if running else "已停止",
                str(status.get("pid") or "—"),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 6:
                    cell.setForeground(QColor(T.OK if running else T.INK_3))
                elif column in (4, 5):
                    cell.setForeground(QColor(T.INK_2))
                self.table.setItem(row, column, cell)

    def _selected_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.text() if item else None

    def _enter(self) -> None:
        instance_id = self._selected_id()
        if instance_id:
            self.win.open_instance_config(instance_id)

    def _action(self, action: str) -> None:
        instance_id = self._selected_id()
        if not instance_id:
            QMessageBox.information(self, "提示", "请先在列表中选中一个实例。")
            return
        self.win.instance_action(instance_id, action)

    def _delete(self) -> None:
        instance_id = self._selected_id()
        if not instance_id:
            QMessageBox.information(self, "提示", "请先在列表中选中一个实例。")
            return
        self.win.delete_instance(instance_id)


# ---------------------------------------------------------------- 日志
class LogsPage(BasePage):
    def __init__(self, window: "ConsoleWindow") -> None:
        super().__init__(window)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.auto = W.ToggleSwitch(True)
        toolbar.addWidget(self.auto)
        toolbar.addWidget(label("自动刷新", "Body"))
        toolbar.addSpacing(18)

        self.filters: dict[str, QPushButton] = {}
        for key, text in (("all", "全部"), ("error", "错误"), ("warn", "警告"), ("info", "信息")):
            button = quiet_button(text)
            button.setCheckable(True)
            button.setChecked(key == "all")
            button.setProperty("active", "true" if key == "all" else "false")
            button.clicked.connect(lambda _c=False, k=key: self._set_filter(k))
            self.filters[key] = button
            toolbar.addWidget(button)

        toolbar.addStretch(1)
        self.counter = label("", "Muted")
        toolbar.addWidget(self.counter)
        self.btn_copy = quiet_button("复制", "copy")
        self.btn_clear = quiet_button("清屏")
        self.btn_reload = quiet_button("刷新", "refresh")
        for button in (self.btn_copy, self.btn_clear, self.btn_reload):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        self.view = QTextEdit()
        self.view.setObjectName("LogView")
        self.view.setReadOnly(True)
        self.view.document().setMaximumBlockCount(5000)
        mono = QFont("Cascadia Code")
        mono.setFamilies(["Cascadia Code", "Consolas", "Microsoft YaHei UI"])
        mono.setPixelSize(12)
        self.view.document().setDefaultFont(mono)
        layout.addWidget(self.view, 1)

        self._filter = "all"
        self._lines: list[dict[str, Any]] = []
        self.btn_reload.clicked.connect(lambda: self.win.load_logs())
        self.btn_clear.clicked.connect(self._clear)
        self.btn_copy.clicked.connect(self._copy)

    def _clear(self) -> None:
        self.view.clear()
        self.win.toast("已清屏", "ok")

    def _set_filter(self, key: str) -> None:
        self._filter = key
        for name, button in self.filters.items():
            button.setChecked(name == key)
            button.setProperty("active", "true" if name == key else "false")
            button.style().unpolish(button)
            button.style().polish(button)
        self.apply(self._lines)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.view.toPlainText())
        self.win.toast("已复制日志", "ok")

    def apply(self, lines: list[dict[str, Any]]) -> None:
        self._lines = list(lines)
        scrollbar = self.view.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        if self._filter == "all":
            shown = self._lines
        else:
            keyword = {"error": "ERROR", "warn": "WARN", "info": "INFO"}[self._filter]
            shown = [
                line
                for line in self._lines
                if keyword in str(line.get("level") or "").upper()
                or keyword in str(line.get("text") or "").upper()
            ]
        self.counter.setText(f"{len(shown)} / {len(self._lines)} 行")
        self.view.setHtml(self._render(shown))
        if at_bottom or self.auto.isChecked():
            scrollbar.setValue(scrollbar.maximum())

    @staticmethod
    def _render(lines: list[dict[str, Any]]) -> str:
        import html as _html

        chunks: list[str] = []
        for line in lines:
            text = _html.escape(str(line.get("text", "")))
            level = str(line.get("level") or "").upper()
            # v8 日志级别映射：INFO→INK_2、WARN→INK（加粗）、ERROR→ERR，避免彩色污染
            if "ERROR" in level:
                style = f"color:{T.ERR};"
            elif "WARN" in level:
                style = f"color:{T.INK}; font-weight:600;"
            elif "DEBUG" in level:
                style = f"color:{T.INK_3};"
            else:
                style = f"color:{T.INK_2};"
            chunks.append(f'<div style="{style}">{text or "&nbsp;"}</div>')
        return "".join(chunks)


# ---------------------------------------------------------------- 用量
class UsagePage(BasePage):
    def __init__(self, window: "ConsoleWindow") -> None:
        super().__init__(window)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)

        self.status = label("正在读取…", "Muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        stats = QHBoxLayout()
        stats.setSpacing(44)
        self.stat_today = W.Stat("今日", "—", "tok", size=24)
        self.stat_cost = W.Stat("今日花费", "—", "元", size=24)
        self.stat_week = W.Stat("近 7 天", "—", "tok", size=24)
        self.stat_total = W.Stat("累计", "—", "tok", size=24)
        for stat in (self.stat_today, self.stat_cost, self.stat_week, self.stat_total):
            stats.addWidget(stat)
        stats.addStretch(1)
        layout.addLayout(stats)

        middle = QHBoxLayout()
        middle.setSpacing(16)
        trend = W.block()
        trend_layout = QVBoxLayout(trend)
        trend_layout.setContentsMargins(20, 18, 20, 16)
        trend_layout.setSpacing(12)
        head = W.SectionHeader("每日用量", "近 14 天")
        self.range_note = label("", "Muted")
        head.add_action(self.range_note)
        trend_layout.addWidget(head)
        self.bars = W.BarChart(T.ACCENT, 150)
        trend_layout.addWidget(self.bars, 1)
        middle.addWidget(trend, 3)

        model_box = W.block()
        model_layout = QVBoxLayout(model_box)
        model_layout.setContentsMargins(20, 18, 20, 16)
        model_layout.setSpacing(10)
        model_layout.addWidget(W.SectionHeader("模型分布"))
        donut_row = QHBoxLayout()
        donut_row.setSpacing(18)
        self.donut = W.Donut(124)
        donut_row.addWidget(self.donut)
        legend_box = QVBoxLayout()
        legend_box.setSpacing(8)
        self.legends = [W.LegendRow(T.SERIES[i], "—") for i in range(4)]
        for legend in self.legends:
            legend_box.addWidget(legend)
            legend.setVisible(False)
        legend_box.addStretch(1)
        donut_row.addLayout(legend_box, 1)
        model_layout.addLayout(donut_row, 1)
        middle.addWidget(model_box, 2)
        layout.addLayout(middle, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        table_box = W.block()
        table_layout = QVBoxLayout(table_box)
        table_layout.setContentsMargins(20, 18, 20, 14)
        table_layout.setSpacing(10)
        table_layout.addWidget(W.SectionHeader("最近调用", "最多 20 条"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["时间", "模型", "输入", "输出", "估算"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 100), (1, 132), (2, 74), (3, 74), (4, 82)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        table_layout.addWidget(self.table, 1)
        bottom.addWidget(table_box, 4)

        price_box = W.block()
        price_layout = QVBoxLayout(price_box)
        price_layout.setContentsMargins(20, 18, 20, 18)
        price_layout.setSpacing(10)
        price_layout.addWidget(W.SectionHeader("单价", "元 / 百万 token"))
        self.price_model = QComboBox()
        self.price_model.setEditable(True)
        for model in ("deepseek-chat", "deepseek-reasoner"):
            self.price_model.addItem(model)
        price_layout.addWidget(self.price_model)
        price_row = QHBoxLayout()
        price_row.setSpacing(8)
        self.price_input = QLineEdit()
        self.price_output = QLineEdit()
        self.price_cached = QLineEdit()
        for text, widget in (("输入", self.price_input), ("输出", self.price_output), ("缓存", self.price_cached)):
            column = QVBoxLayout()
            column.setSpacing(3)
            column.addWidget(label(text, "Label"))
            column.addWidget(widget)
            price_row.addLayout(column, 1)
        price_layout.addLayout(price_row)
        self.btn_save_price = accent_button("保存单价")
        price_layout.addWidget(self.btn_save_price, 0, Qt.AlignmentFlag.AlignLeft)
        price_layout.addWidget(label("只存本地，用来把 token 换算成钱。", "Muted"))
        price_layout.addStretch(1)
        bottom.addWidget(price_box, 2)
        layout.addLayout(bottom, 1)

        self.btn_save_price.clicked.connect(self._save_price)
        self.price_model.currentTextChanged.connect(self._load_price_row)

    def _load_price_row(self, model: str) -> None:
        pricing = self.win.pricing or remote.load_pricing()
        price = remote.price_of(pricing, model)
        self.price_input.setText(str(price.get("input", 0)))
        self.price_output.setText(str(price.get("output", 0)))
        self.price_cached.setText(str(price.get("cached", 0)))

    def _save_price(self) -> None:
        model = self.price_model.currentText().strip()
        if not model:
            return
        try:
            payload = {
                "input": float(self.price_input.text() or 0),
                "output": float(self.price_output.text() or 0),
                "cached": float(self.price_cached.text() or 0),
            }
        except ValueError:
            QMessageBox.warning(self, "格式错误", "单价必须是数字。")
            return
        self.win.save_pricing(model, payload)

    def enter(self) -> None:
        self._load_price_row(self.price_model.currentText())
        if self.win.connected:
            self.win.load_usage()

    def apply(self, data: dict[str, Any]) -> None:
        usage = (data or {}).get("usage") or {}
        pricing = (data or {}).get("pricing") or {}
        self.win.pricing = pricing
        self._load_price_row(self.price_model.currentText())

        if usage.get("available"):
            updated = str(usage.get("updated_at") or "")[:19].replace("T", " ")
            self.status.setText(f"{usage.get('path', '')} · 更新于 {updated or '未知'}")
            self.status.setStyleSheet(f"color:{T.INK_3};")
        else:
            self.status.setText(str(usage.get("reason") or "暂无用量数据"))
            self.status.setStyleSheet(f"color:{T.INK_3};")

        days = usage.get("days") or {}
        total = usage.get("total") or {}
        models = usage.get("models") or {}
        fallback = next(iter(models.keys()), "deepseek-chat")

        today_item = days.get(date.today().isoformat()) or {}
        self.stat_today.set(
            W.fmt_tokens(float(today_item.get("prompt", 0)) + float(today_item.get("completion", 0))),
            f"{W.fmt_int(today_item.get('calls', 0))} 次",
        )
        self.stat_cost.set(W.fmt_money(day_cost(today_item, pricing, fallback)), "")

        week = 0.0
        for offset in range(7):
            item = days.get((date.today() - timedelta(days=offset)).isoformat()) or {}
            week += float(item.get("prompt", 0)) + float(item.get("completion", 0))
        total_tokens = float(total.get("prompt", 0)) + float(total.get("completion", 0))
        self.stat_week.set(W.fmt_tokens(week), "")
        self.stat_total.set(W.fmt_tokens(total_tokens), f"缓存 {W.fmt_tokens(total.get('cached', 0))}")

        series: list[float] = []
        labels: list[str] = []
        for offset in range(13, -1, -1):
            day = (date.today() - timedelta(days=offset)).isoformat()
            item = days.get(day) or {}
            series.append(float(item.get("prompt", 0)) + float(item.get("completion", 0)))
            labels.append(day[5:])
        self.bars.set_data(series, labels)
        self.range_note.setText(f"合计 {W.fmt_tokens(sum(series))} tok")

        ranked = sorted(
            models.items(),
            key=lambda pair: float(pair[1].get("prompt", 0)) + float(pair[1].get("completion", 0)),
            reverse=True,
        )[:4]
        segments = []
        for index, (name, item) in enumerate(ranked):
            segments.append(
                (name, float(item.get("prompt", 0)) + float(item.get("completion", 0)), T.SERIES[index % len(T.SERIES)])
            )
        self.donut.set_data(segments, W.fmt_tokens(total_tokens), "总 token")
        for index, legend in enumerate(self.legends):
            if index < len(segments):
                ratio = segments[index][1] / total_tokens * 100 if total_tokens else 0
                legend.set(segments[index][0], f"{ratio:.0f}%")
                legend.setVisible(True)
            else:
                legend.setVisible(False)

        recent = list(usage.get("recent") or [])[:20]
        self.table.setRowCount(len(recent))
        for row, item in enumerate(recent):
            ts = item.get("ts") or 0
            when = datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M") if ts else "—"
            model = str(item.get("model") or "—")
            prompt = float(item.get("prompt", 0))
            completion = float(item.get("completion", 0))
            cost = remote.cost_of(pricing, model, prompt, completion, float(item.get("cached", 0)))
            values = [when, model, W.fmt_int(prompt), W.fmt_int(completion), W.fmt_money(cost)]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 4:
                    cell.setForeground(QColor(T.ACCENT))
                elif column in (0, 1):
                    cell.setForeground(QColor(T.INK_2))
                self.table.setItem(row, column, cell)


# ---------------------------------------------------------------- 配置
class ConfigPage(BasePage):
    def __init__(self, window: "ConsoleWindow") -> None:
        super().__init__(window)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.back_btn = quiet_button("← 实例列表")
        self.back_btn.clicked.connect(lambda: self.win.switch_page("instances"))
        toolbar.addWidget(self.back_btn)
        toolbar.addSpacing(4)
        self.target = label("default", "Value")
        toolbar.addWidget(self.target)
        self.config_note = label("", "Muted")
        toolbar.addWidget(self.config_note)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索字段")
        self.search.setFixedWidth(220)
        toolbar.addWidget(self.search)
        toolbar.addStretch(1)
        self.btn_reload = quiet_button("重新载入", "refresh")
        self.btn_save = accent_button("保存到服务器")
        toolbar.addWidget(self.btn_reload)
        toolbar.addWidget(self.btn_save)
        layout.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.form_layout = QVBoxLayout(holder)
        self.form_layout.setContentsMargins(0, 4, 8, 20)
        self.form_layout.setSpacing(30)

        self.inputs: dict[str, tuple[Any, str]] = {}
        self.field_labels: dict[str, tuple[QLabel, QWidget]] = {}
        schema = {spec["key"]: spec for spec in config_store.SCHEMA}

        for group in T.GROUP_ORDER:
            members = [(k, v) for k, v in T.FIELD_UI.items() if v[1] == group and k in schema]
            if not members:
                continue
            self.form_layout.addWidget(label(group, "SectionTitle"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(10)
            # 列序：0 标签 | 1 输入 | 2 标签 | 3 输入；只有输入列吃 stretch
            grid.setColumnStretch(0, 0)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(2, 0)
            grid.setColumnStretch(3, 1)
            for index, (key, (text, _group, hint)) in enumerate(members):
                row, column = divmod(index, 2)
                field_type = schema[key].get("type")
                caption = label(text, "Label")
                caption.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                caption.setFixedWidth(96)
                grid.addWidget(caption, row, column * 2)
                if field_type == "bool":
                    bool_holder = QWidget()
                    row_box = QHBoxLayout(bool_holder)
                    row_box.setContentsMargins(0, 0, 0, 0)
                    row_box.setSpacing(8)
                    switch = W.ToggleSwitch(False)
                    row_box.addWidget(switch)
                    state_text = label("关闭", "Body")
                    row_box.addWidget(state_text)
                    row_box.addStretch(1)
                    switch.toggled.connect(
                        lambda checked, tw=state_text: tw.setText("开启" if checked else "关闭")
                    )
                    self.inputs[key] = (switch, "bool")
                    widget = bool_holder
                else:
                    widget = QLineEdit()
                    widget.setPlaceholderText(hint)
                    self.inputs[key] = (widget, field_type or "str")
                grid.addWidget(widget, row, column * 2 + 1)
                self.field_labels[key] = (caption, widget)
            self.form_layout.addLayout(grid)

        self.form_layout.addStretch(1)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)

        self.search.textChanged.connect(self._filter)
        self.btn_reload.clicked.connect(lambda: self.win.load_config())
        self.btn_save.clicked.connect(lambda: self.win.save_config())

        # ---- v8 保存吸底条（UX_SPEC §3）：常态显示上次保存时间，dirty 时出现动作 ----
        self.save_bar = QFrame()
        self.save_bar.setObjectName("SaveBar")
        save_bar_layout = QHBoxLayout(self.save_bar)
        save_bar_layout.setContentsMargins(18, 10, 18, 10)
        save_bar_layout.setSpacing(8)
        self.saved_label = label("尚未保存", "Muted")
        save_bar_layout.addWidget(self.saved_label, 1)
        self.btn_discard = ghost_button("放弃修改")
        self.btn_discard.hide()
        self.btn_save_bar = accent_button("保存更改")
        self.btn_save_bar.hide()
        save_bar_layout.addWidget(self.btn_discard)
        save_bar_layout.addWidget(self.btn_save_bar)
        layout.addWidget(self.save_bar)
        self.btn_save_bar.clicked.connect(lambda: self.win.save_config())
        self.btn_discard.clicked.connect(lambda: self.win.load_config())

        # dirty 跟踪：任意输入变化即置位（load 期间挂起）
        self._dirty = False
        self._loading = False
        for _key, (widget, field_type) in self.inputs.items():
            if field_type == "bool":
                widget.toggled.connect(self._mark_dirty)
            else:
                widget.textChanged.connect(self._mark_dirty)

    # -------------------------------------------------- dirty 状态
    def _mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        self._set_dirty(True)

    def _set_dirty(self, value: bool) -> None:
        self._dirty = value
        self.btn_discard.setVisible(value)
        self.btn_save_bar.setVisible(value)
        self.saved_label.setText("有未保存的修改" if value else self.saved_label.text())

    def mark_saved(self, note: str = "") -> None:
        """保存成功后由 ConsoleWindow / ConfigDialog 调用，清零 dirty。"""
        self._loading = True
        self._set_dirty(False)
        stamp = datetime.now().strftime("%H:%M")
        self.saved_label.setText(note or f"上次保存 {stamp}")
        self._loading = False

    def discard(self) -> None:
        self._loading = True
        self._set_dirty(False)
        self._loading = False

    def _filter(self, keyword: str) -> None:
        keyword = keyword.strip().lower()
        for key, (caption, widget) in self.field_labels.items():
            text = f"{caption.text()} {T.FIELD_UI.get(key, ('', '', ''))[2]}".lower()
            visible = not keyword or keyword in text
            caption.setVisible(visible)
            widget.setVisible(visible)

    def enter(self) -> None:
        """从实例列表点进来时，重新读取「当前实例」的配置。

        之前没有这个方法，从实例页跳转后配置页显示的仍是上一次加载的
        default 配置，看起来像「实例配置被删了」。
        """
        self._sync_header()
        if self.win.connected:
            self.win.load_config()

    def _sync_header(self) -> None:
        instance_id = getattr(self, "override_instance", None) or self.win.current_instance
        info = next((item for item in self.win.instances if item.get("id") == instance_id), {})
        name = str(info.get("name") or instance_id)
        self.target.setText(f"{name}（{instance_id}）")
        config_dir = str(info.get("config_dir") or "")
        if config_dir == ".":
            note = "config.json（项目根，default 实例）"
        elif config_dir:
            note = f"{config_dir}/config.json"
        else:
            note = ""
        self.config_note.setText(note)

    def apply(self, config: dict[str, Any]) -> None:
        self._sync_header()
        self._loading = True  # load 期间挂起 dirty 置位
        for key, (widget, field_type) in self.inputs.items():
            value = config.get(key)
            if field_type == "bool":
                widget.setChecked(bool(value))
            else:
                widget.setText("" if value is None else str(value))
        self._loading = False
        self._set_dirty(False)

    def collect(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, (widget, field_type) in self.inputs.items():
            if field_type == "bool":
                payload[key] = widget.isChecked()
            elif field_type == "int":
                try:
                    payload[key] = int(float(widget.text().strip() or 0))
                except ValueError:
                    payload[key] = 0
            elif field_type == "float":
                try:
                    payload[key] = float(widget.text().strip() or 0)
                except ValueError:
                    payload[key] = 0.0
            else:
                payload[key] = widget.text().strip()
        return payload


# ---------------------------------------------------------------- NapCat
class NapcatPage(BasePage):
    def __init__(self, window: "ConsoleWindow") -> None:
        super().__init__(window)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)

        head = QHBoxLayout()
        head.setSpacing(12)
        self.badge = label("读取中", "BadgeOff")
        head.addWidget(self.badge)
        head.addStretch(1)
        for text, action, glyph in (("启动", "start", "play"), ("停止", "stop", "square"), ("重启", "restart", "rotate")):
            button = ghost_button(text, glyph)
            button.clicked.connect(lambda _c=False, a=action: self.win.napcat_action(a))
            head.addWidget(button)
        refresh = quiet_button("刷新", "refresh")
        refresh.clicked.connect(lambda: self.win.load_napcat())
        head.addWidget(refresh)
        layout.addLayout(head)

        self.detail = label("读取中…", "Body")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        info = W.block()
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(20, 18, 20, 18)
        info_layout.setSpacing(10)
        info_layout.addWidget(W.SectionHeader("容器"))
        self.rows: dict[str, W.KVRow] = {}
        for key, text in (("container", "容器名"), ("ws", "OneBot"), ("webui", "WebUI"), ("state", "状态"), ("started", "启动时间")):
            row = W.KVRow(text, label_width=92)
            self.rows[key] = row
            info_layout.addWidget(row)
        layout.addWidget(info)
        layout.addWidget(label("登录二维码与账号切换请在服务器端或 NapCat WebUI（6099）操作。", "Muted"))
        layout.addStretch(1)

    def enter(self) -> None:
        if self.win.connected:
            self.win.load_napcat()

    def apply(self, info: dict[str, Any]) -> None:
        if not info:
            self.detail.setText("无数据")
            return
        running = bool(info.get("running"))
        self.badge.setObjectName("BadgeOk" if running else "BadgeErr")
        self.badge.setText("running" if running else "stopped")
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)
        if info.get("error"):
            self.detail.setText(str(info["error"]))
            return
        state = str(info.get("state") or ("running" if running else "stopped"))
        self.detail.setText("容器运行中，OneBot 网关可用。" if running else "容器未运行，QQ 侧收不到任何消息。")
        self.rows["container"].set("napcat")
        self.rows["ws"].set("3001（反向 WS）")
        self.rows["webui"].set("6099")
        self.rows["state"].set(state, T.OK if running else T.ERR)
        self.rows["started"].set(str(info.get("started_at") or "—")[:19].replace("T", " "))


# ---------------------------------------------------------------- 连接
class HostRow(QFrame):
    clicked = Signal(str)

    def __init__(self, host: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.host_id = str(host.get("id"))
        self.setObjectName("Block")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(9)
        active = bool(host.get("active"))
        layout.addWidget(W.StatusDot(T.ACCENT if active else T.INK_3, 6))
        box = QVBoxLayout()
        box.setSpacing(1)
        box.addWidget(label(str(host.get("name") or host.get("host") or "未命名"), "Value"))
        box.addWidget(label(f"{host.get('user')}@{host.get('host')}:{host.get('port')}", "Muted"))
        layout.addLayout(box, 1)
        if active:
            layout.addWidget(label("当前", "BadgeAccent"))

    def mousePressEvent(self, _event) -> None:  # noqa: N802
        self.clicked.emit(self.host_id)


class ConnectionPage(BasePage):
    def __init__(self, window: "ConsoleWindow") -> None:
        super().__init__(window)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        top = QHBoxLayout()
        top.setSpacing(16)

        list_box = W.block()
        list_box.setFixedWidth(300)
        list_layout = QVBoxLayout(list_box)
        list_layout.setContentsMargins(18, 16, 18, 14)
        list_layout.setSpacing(10)
        head = W.SectionHeader("主机档案")
        add = quiet_button("新建", "plus")
        add.clicked.connect(self._new_host)
        head.add_action(add)
        list_layout.addWidget(head)
        self.host_container = QVBoxLayout()
        self.host_container.setSpacing(6)
        list_layout.addLayout(self.host_container)
        self.host_empty = W.EmptyState("还没有主机", "点右上角新建")
        list_layout.addWidget(self.host_empty)
        list_layout.addStretch(1)
        self.btn_delete_host = quiet_button("删除该主机")
        list_layout.addWidget(self.btn_delete_host)
        top.addWidget(list_box)

        form = QWidget()
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)
        form_layout.addWidget(W.SectionHeader("连接参数", "SSH 密钥登录，密码不落盘"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(9)
        grid.setColumnStretch(1, 1)
        self.fields: dict[str, QLineEdit] = {}
        rows = (
            ("name", "名称", "生产服务器"),
            ("host", "地址", "203.0.113.10"),
            ("port", "端口", "22"),
            ("user", "用户", "root"),
            ("key", "私钥", r"D:\爱弥斯_bot\.tools\deploy\id_im_bridge"),
            ("dir", "项目路径", "/opt/im-bridge"),
        )
        for row, (key, text, placeholder) in enumerate(rows):
            grid.addWidget(label(text, "Label"), row, 0)
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            self.fields[key] = edit
            grid.addWidget(edit, row, 1)
            if key == "key":
                browse = quiet_button("选择")
                browse.setFixedWidth(58)
                browse.clicked.connect(self._pick_key)
                grid.addWidget(browse, row, 2)
        form_layout.addLayout(grid)

        self.result = label("", "Muted")
        self.result.setWordWrap(True)
        form_layout.addWidget(self.result)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_save = accent_button("保存并连接")
        self.btn_test = ghost_button("仅测试")
        self.btn_active = quiet_button("设为当前")
        buttons.addWidget(self.btn_save)
        buttons.addWidget(self.btn_test)
        buttons.addWidget(self.btn_active)
        buttons.addStretch(1)
        form_layout.addLayout(buttons)
        top.addWidget(form, 1)
        layout.addLayout(top)

        checks = W.block()
        checks_layout = QVBoxLayout(checks)
        checks_layout.setContentsMargins(20, 16, 20, 18)
        checks_layout.setSpacing(8)
        head2 = W.SectionHeader("连接体检", "任一项失败都会指出是哪一步")
        self.btn_diagnose = quiet_button("重新体检", "activity")
        head2.add_action(self.btn_diagnose)
        checks_layout.addWidget(head2)
        self.check_container = QGridLayout()
        self.check_container.setHorizontalSpacing(28)
        self.check_container.setVerticalSpacing(7)
        self.check_container.setColumnStretch(0, 1)
        self.check_container.setColumnStretch(1, 1)
        checks_layout.addLayout(self.check_container)
        layout.addWidget(checks)
        layout.addStretch(1)

        self.current_host_id = ""
        self.btn_save.clicked.connect(self._save)
        self.btn_test.clicked.connect(self._test)
        self.btn_active.clicked.connect(self._set_active)
        self.btn_delete_host.clicked.connect(self._delete)
        self.btn_diagnose.clicked.connect(lambda: self.win.run_diagnose(self.collect()))

    def collect(self) -> dict[str, Any]:
        payload: dict[str, Any] = {key: edit.text().strip() for key, edit in self.fields.items()}
        payload["id"] = self.current_host_id
        try:
            payload["port"] = int(payload.get("port") or 22)
        except ValueError:
            payload["port"] = 22
        return payload

    def _pick_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 SSH 私钥", str(Path.home() / ".ssh"))
        if path:
            self.fields["key"].setText(path)

    def _new_host(self) -> None:
        self.current_host_id = ""
        for edit in self.fields.values():
            edit.clear()
        self.fields["port"].setText("22")
        self.fields["user"].setText("root")
        self.fields["dir"].setText("/opt/im-bridge")
        self.result.setText("新主机：填好后点「保存并连接」")
        self.result.setStyleSheet(f"color:{T.INK_3};")

    def _load_host(self, host: dict[str, Any]) -> None:
        self.current_host_id = str(host.get("id") or "")
        for key in ("name", "host", "port", "user", "key", "dir"):
            self.fields[key].setText(str(host.get(key) or ""))

    def _save(self) -> None:
        payload = self.collect()
        if not payload.get("host"):
            QMessageBox.warning(self, "缺少信息", "服务器地址不能为空。")
            return
        self.win.save_host(payload)

    def _test(self) -> None:
        self.win.test_connection(self.collect())

    def _set_active(self) -> None:
        if not self.current_host_id:
            QMessageBox.information(self, "提示", "先保存这台主机。")
            return
        self.win.set_active_host(self.current_host_id)

    def _delete(self) -> None:
        if self.current_host_id:
            self.win.delete_host(self.current_host_id)

    def apply_hosts(self, hosts: list[dict[str, Any]]) -> None:
        while self.host_container.count():
            item = self.host_container.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.host_empty.setVisible(not hosts)
        for host in hosts:
            row = HostRow(host)
            row.clicked.connect(self._on_host_clicked)
            self.host_container.addWidget(row)
        active = next((host for host in hosts if host.get("active")), None)
        if active and not self.current_host_id:
            self._load_host(active)

    def _on_host_clicked(self, host_id: str) -> None:
        for host in self.win.hosts:
            if str(host.get("id")) == host_id:
                self._load_host(host)
                break

    def enter(self) -> None:
        self.win.hosts = remote.list_hosts()
        self.apply_hosts(self.win.hosts)

    def apply_checks(self, checks: list[dict[str, Any]]) -> None:
        while self.check_container.count():
            item = self.check_container.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        if not checks:
            return
        for index, check in enumerate(checks):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(9)
            ok = bool(check.get("ok"))
            tone = T.OK if ok else (T.WARN if check.get("level") == "warn" else T.ERR)
            mark = QLabel()
            mark.setPixmap(T.glyph("check" if ok else "x-circle", tone, 14))
            row_layout.addWidget(mark)
            name = label(str(check.get("label") or ""), "Body")
            name.setFixedWidth(118)
            row_layout.addWidget(name)
            detail = label(str(check.get("detail") or ""), "Muted")
            detail.setWordWrap(False)
            row_layout.addWidget(detail, 1)
            # 两列排布：10 项 → 5 行，避免把整页顶出窗口
            self.check_container.addWidget(row, index // 2, index % 2)


# ---------------------------------------------------------------- 主窗口
class ConsoleWindow(QMainWindow):
    NAV = (
        ("overview", "首页", "home"),
        ("connection", "云服务器", "server"),
        ("config", "AI 配置", "sparkles"),
        ("napcat", "NapCat", "plug"),
        ("instances", "QQ Bot", "message-circle"),
        ("logs", "日志记录", "file-text"),
        ("usage", "设置", "settings"),
    )

    def __init__(self) -> None:
        super().__init__()
        # 原生窗口框：不做无边框/半透明，避免分层窗口在启动时的重绘闪烁
        self.setWindowTitle(WINDOW_TITLE)
        self.setStyleSheet(T.STYLE)
        self.resize(1280, 850)
        self.setMinimumSize(1080, 720)
        if ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        self.rvc_session = local_rvc.LocalRvcSession()
        self.manager: remote.RemoteInstanceManager | None = None
        self.instances: list[dict[str, Any]] = []
        self.hosts: list[dict[str, Any]] = remote.list_hosts()
        self.current_instance = "default"
        self.connected = False
        self.pricing: dict[str, dict[str, float]] = remote.load_pricing()
        self._memo: dict[str, tuple[float, Any]] = {}
        self._page_anim: QPropertyAnimation | None = None
        self._theme_pref = T.load_theme_pref()
        self._theme = T.current_theme()
        self._system_theme_seen = T.system_theme()

        central = QWidget()
        central.setObjectName("Root")
        body = QHBoxLayout(central)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_content(), 1)
        self.setCentralWidget(central)

        self.pages: dict[str, BasePage] = {
            "overview": HomePage(self),
            "connection": ServersPage(self),
            "config": AIConfigPage(self),
            "napcat": NapcatPageV9(self),
            "instances": BotsPage(self),
            "logs": LogsPageV9(self),
            "usage": SettingsPageV9(self),
        }
        self._page_scrolls: dict[str, QScrollArea] = {}
        for key, page in self.pages.items():
            wrapper = QWidget()
            wrap_layout = QVBoxLayout(wrapper)
            wrap_layout.setContentsMargins(K.PAGE_PAD, 18, K.PAGE_PAD, 22)
            wrap_layout.setSpacing(0)
            wrap_layout.addWidget(page)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(wrapper)
            self.stack.addWidget(scroll)
            self._page_scrolls[key] = scroll
        self._home_intro = False
        self._current_key = "overview"

        self.nav_buttons: dict[str, QPushButton] = {}
        self._build_nav_buttons()
        self.switch_page("overview", animate=False)
        self.pages["connection"].apply_hosts(self.hosts)

        # 先让首帧画完，再去碰网络，避免启动瞬间同时做窗口合成与 ssh 进程创建
        QTimer.singleShot(480, self.connect_background)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(6000)

        # 跟随系统主题：每 5 秒检测一次 Windows 的浅色/深色偏好
        self._theme_timer = QTimer(self)
        self._theme_timer.timeout.connect(self._check_system_theme)
        self._theme_timer.start(5000)

    # -------------------------------------------------- 结构
    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(K.SIDEBAR_W)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(14, 22, 14, 16)
        layout.setSpacing(2)

        brand = QHBoxLayout()
        brand.setContentsMargins(8, 0, 8, 0)
        brand.setSpacing(10)
        logo = QLabel()
        pixmap = QPixmap(str(ASSETS / "app_256.png"))
        if not pixmap.isNull():
            logo.setPixmap(_rounded_pixmap(pixmap, 30, 9))
        brand.addWidget(logo)
        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand_name = QLabel("im-bridge")
        brand_name.setObjectName("BrandName")
        brand_tag = QLabel("Connect Everything")
        brand_tag.setObjectName("BrandTag")
        brand_box.addWidget(brand_name)
        brand_box.addWidget(brand_tag)
        brand.addLayout(brand_box, 1)
        layout.addLayout(brand)
        layout.addSpacing(22)

        self.nav_layout = QVBoxLayout()
        self.nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_layout.setSpacing(3)
        layout.addLayout(self.nav_layout)
        layout.addStretch(1)

        line = QFrame()
        line.setFixedHeight(1)
        line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        line.setStyleSheet(f"background: {K.t('SIDEBAR_LINE')}; border: none;")
        layout.addWidget(line)
        layout.addSpacing(12)

        footer = QHBoxLayout()
        footer.setContentsMargins(6, 0, 4, 0)
        footer.setSpacing(8)
        self.footer_dot = C.StatusDot("idle", 7)
        footer.addWidget(self.footer_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        footer_box = QVBoxLayout()
        footer_box.setSpacing(1)
        self.footer_host = QLabel("未连接")
        self.footer_host.setStyleSheet(f"color: {K.t('SIDEBAR_INK')}; font-size: 12px;")
        self.footer_sub = QLabel("")
        self.footer_sub.setStyleSheet(f"color: {K.t('SIDEBAR_INK_2')}; font-size: 10.5px;")
        footer_box.addWidget(self.footer_host)
        footer_box.addWidget(self.footer_sub)
        footer.addLayout(footer_box, 1)

        self.theme_toggle = C.icon_button("moon", "切换纸白 / 墨黑", 15)
        self.theme_toggle.setIcon(UI_ICONS.icon("moon", str(K.t("SIDEBAR_INK")), 15))
        self.theme_toggle.setStyleSheet(
            "QPushButton#IconBtn { background: rgba(255,255,255,0.06); }"
            "QPushButton#IconBtn:hover { background: rgba(255,255,255,0.12); }"
        )
        self.theme_toggle.clicked.connect(self.toggle_theme)
        footer.addWidget(self.theme_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(footer)
        return side

    def _set_footer(self, host: str, sub: str, ok: bool) -> None:
        self.footer_host.setText(host or "未连接")
        self.footer_sub.setText(sub)
        self.footer_dot.set_tone("ok" if ok else "idle")

    def _build_nav_buttons(self) -> None:
        self.nav_buttons = {}
        for key, text, icon_name in self.NAV:
            button = C.NavButton(key, text, icon_name)
            button.clicked.connect(lambda _checked=False, k=key: self.switch_page(k))
            self.nav_layout.addWidget(button)
            self.nav_buttons[key] = button

    def _build_content(self) -> QWidget:
        holder = QWidget()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QWidget()
        head = QHBoxLayout(self.header)
        head.setContentsMargins(K.PAGE_PAD, 24, K.PAGE_PAD, 0)
        head.setSpacing(12)
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        self.page_title = C.label("首页", "PageTitle")
        self.page_desc = C.label("", "PageDesc")
        title_box.addWidget(self.page_title)
        title_box.addWidget(self.page_desc)
        head.addLayout(title_box)
        head.addStretch(1)

        self.instance_box = QComboBox()
        self.instance_box.setFixedWidth(150)
        self.instance_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.instance_box.currentIndexChanged.connect(self._instance_index_changed)
        head.addWidget(self.instance_box, 0, Qt.AlignmentFlag.AlignVCenter)

        self.status_dot = C.StatusDot("idle", 7)
        head.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status_text = C.label("连接中…", "Muted")
        head.addWidget(self.status_text)
        outer.addWidget(self.header)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.content_holder = holder
        return holder

    # -------------------------------------------------- 页面切换
    def switch_page(self, key: str, animate: bool = True) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        # 配置页有未保存修改时切走，先确认（默认留在此页）
        if (
            self._current_key == "config"
            and key != "config"
            and getattr(self.pages["config"], "_dirty", False)
        ):
            box = QMessageBox(self)
            box.setWindowTitle("有未保存的修改")
            box.setText("配置页有未保存的修改，确定要离开吗？")
            leave = box.addButton("放弃修改并离开", QMessageBox.ButtonRole.DestructiveRole)
            stay = box.addButton("留在此页", QMessageBox.ButtonRole.AcceptRole)
            box.setDefaultButton(stay)
            box.exec()
            if box.clickedButton() is stay:
                return
            self.pages["config"].discard()

        target = self._page_scrolls.get(key, page)
        old_pixmap = self.stack.grab() if animate and self._current_key != key else None
        self._current_key = key
        self.stack.setCurrentWidget(target)
        # 回到顶部：否则上一页的滚动位置会把新页头顶切掉，看起来像"内容在跳"
        bar = target.verticalScrollBar() if hasattr(target, "verticalScrollBar") else None
        if bar is not None:
            bar.setValue(0)
        title, desc = PAGE_META.get(key, (key, ""))
        self.page_title.setText(title)
        self.page_desc.setText(desc)
        self.header.setVisible(bool(title))
        for name, button in self.nav_buttons.items():
            button.set_active(name == key)
        self.instance_box.setVisible(key in ("logs", "napcat", "config", "instances"))

        page.enter()

        if not animate or old_pixmap is None:
            target.setGraphicsEffect(None)
            return
        fade = M.PageFade(self.stack, old_pixmap)
        fade.start()
        if key == "overview" and not self._home_intro:
            self._home_intro = True
            page.play_intro()

    # -------------------------------------------------- 主题
    def _refresh_theme_widgets(self) -> None:
        for button in getattr(self, "nav_buttons", {}).values():
            button._sync_icon()
            button.style().unpolish(button)
            button.style().polish(button)
        toggle = getattr(self, "theme_toggle", None)
        if toggle is not None:
            name = "sun" if K.current_theme() == "dark" else "moon"
            toggle.setIcon(UI_ICONS.icon(name, str(K.t("SIDEBAR_INK")), 15))
        if hasattr(self, "footer_host"):
            self.footer_host.setStyleSheet(f"color: {K.t('SIDEBAR_INK')}; font-size: 12px;")
            self.footer_sub.setStyleSheet(f"color: {K.t('SIDEBAR_INK_2')}; font-size: 10.5px;")
        for page in self.pages.values():
            hook = getattr(page, "retheme", None)
            if callable(hook):
                hook()

        # 按钮图标是把颜色烤进 QPixmap 的，主题切换后必须重设
        for button in self.findChildren(QPushButton):
            name = button.property("iconName")
            if not name:
                continue
            role = button.objectName()
            if role == "Primary":
                color = str(K.t("ACCENT_INK"))
            elif role in ("Quiet", "IconBtn"):
                color = str(K.t("INK_2"))
            else:
                color = str(K.t("INK"))
            size = button.iconSize().width() or 15
            button.setIcon(UI_ICONS.icon(str(name), color, size))
        for widget in self.findChildren(QWidget):
            widget.update()

    def _apply_theme(self, theme: str, *, animate: bool = False) -> None:
        if animate:
            self._apply_theme_animated(theme)
            return
        T.apply_theme(theme)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(T.STYLE)
        self.setStyleSheet(T.STYLE)
        self._refresh_theme_widgets()

    def toggle_theme(self) -> None:
        target = "light" if T.current_theme() == "dark" else "dark"
        self._theme_pref = target
        self._theme = target
        T.save_theme_pref(target)
        self._apply_theme_animated(target)

    def _apply_theme_animated(self, target: str) -> None:
        old_pix = self.grab()
        center = QPointF(self.width() / 2.0, 42.0)
        button = getattr(self, "theme_toggle", None)
        if button is not None:
            point = button.mapTo(self, button.rect().center())
            center = QPointF(point.x(), point.y())
        self._apply_theme(target)
        QTimer.singleShot(90, lambda: self._play_reveal(old_pix, center))

    def _play_reveal(self, old_pix, center: QPointF) -> None:
        new_pix = self.grab()
        overlay = W.RevealOverlay(self)
        overlay.start(self, old_pix, new_pix, center)
        self._reveal_overlay = overlay

    def _check_system_theme(self) -> None:
        if self._theme_pref != "system":
            return
        current = T.system_theme()
        if current != self._system_theme_seen:
            self._system_theme_seen = current
            self._apply_theme_animated(current)

    def _icon_of(self, key: str) -> str:
        for _group, items in self.NAV:
            for name, _text, icon_name in items:
                if name == key:
                    return icon_name
        return "activity"

    def set_instance(self, instance_id: str) -> None:
        self.current_instance = instance_id
        self._sync_instance_box()
        self.refresh_overview(force=True)

    def _instance_index_changed(self, index: int) -> None:
        if 0 <= index < len(self.instances):
            self.current_instance = str(self.instances[index].get("id") or "default")
            self.refresh_overview(force=True)
            if self._current_key == "config":
                self.load_config()

    def _sync_instance_box(self) -> None:
        self.instance_box.blockSignals(True)
        self.instance_box.clear()
        for item in self.instances:
            self.instance_box.addItem(str(item.get("name") or item.get("id")))
        self.instance_box.blockSignals(False)
        for index, item in enumerate(self.instances):
            if item.get("id") == self.current_instance:
                self.instance_box.setCurrentIndex(index)
                break

    # -------------------------------------------------- 反馈
    def toast(self, text: str, kind: str = "ok") -> None:
        """右下角轻提示：不做模态，成功 2.2s / 失败 3.5s 自动淡出。"""
        if not hasattr(self, "_toast_label"):
            self._toast_label = C.Toast(getattr(self, "content_holder", self))
        self._toast_label.show_message(text, kind)

    # -------------------------------------------------- 状态
    def _set_status(self, text: str, color: str) -> None:
        self.status_text.setText(text)
        tone = "ok" if color == "OK" else ("err" if color == "ERR" else "idle")
        self.status_dot.set_tone(tone)

    def _memo_get(self, key: str, ttl: float) -> Any:
        item = self._memo.get(key)
        if item and time.time() - item[0] < ttl:
            return item[1]
        return None

    def _memo_set(self, key: str, value: Any) -> Any:
        self._memo[key] = (time.time(), value)
        return value

    def connect_background(self) -> None:
        self._set_status("连接中…", "INK_3")

        def work() -> dict[str, Any]:
            info = remote.test_connection()
            if not info.get("ok"):
                return {"info": info, "instances": [], "manager": None}
            manager = remote.RemoteInstanceManager()
            return {"info": info, "instances": manager.list_instances(), "manager": manager}

        def done(result: dict[str, Any]) -> None:
            info = result["info"]
            self.connected = bool(info.get("ok"))
            if not self.connected:
                self._set_status(f"连接失败 · {str(info.get('error'))[:24]}", T.ERR)
                self.pages["overview"].apply(
                    {"status": {}, "usage": {}, "config": {}, "hostinfo": info, "logs": []}
                )
                self._set_footer("未连接", "", False)
                return
            self.manager = result["manager"]
            self.instances = result["instances"]
            self._set_status("已连接", T.OK)
            self._set_footer(str(info.get("host") or "—"), f"{len(self.instances)} 个实例", True)
            self._memo.clear()
            self._sync_instance_box()
            self.pages["instances"].apply(self.instances)
            self.refresh_overview(force=True)
            if self._current_key == "usage":
                self.load_usage(force=True)

        def fail(message: str) -> None:
            self.connected = False
            self._set_status(f"连接失败 · {message[:24]}", T.ERR)

        run_async(work, done, fail)

    def test_connection(self, payload: dict[str, Any]) -> None:
        page = self.pages["connection"]
        page.result.setText("测试中…")
        page.result.setStyleSheet(f"color:{T.INK_3};")

        def done(info: dict[str, Any]) -> None:
            if info.get("ok"):
                page.result.setText(f"连接成功 · {info.get('detail') or info.get('host')}")
                page.result.setStyleSheet(f"color:{T.OK};")
            else:
                page.result.setText(f"失败 · {info.get('error')}")
                page.result.setStyleSheet(f"color:{T.ERR};")

        def fail(message: str) -> None:
            page.result.setText(f"失败 · {message}")
            page.result.setStyleSheet(f"color:{T.ERR};")

        run_async(lambda: remote.test_connection(payload), done, fail)

    def start_rvc(self, panel) -> None:
        if not self._require_connection("连接本地语音"):
            return
        page = self.pages["config"]
        values = page.collect()
        values = {**config_store.DEFAULTS, **values}
        instance_id = self.current_instance
        manager = self.manager
        cfg = remote.read_deploy_env()
        panel.busy(True)
        panel.status.setText("正在检查本地文件并连接云端…")

        def work():
            # Fail before starting anything if server code has not been upgraded yet.
            import shlex
            script = "from im_bridge.config import FIELD_BY_KEY; assert 'rvc_endpoint' in FIELD_BY_KEY"
            code, _ = remote.run("cd " + shlex.quote(cfg["dir"]) + " && PYTHONPATH=src " + remote.REMOTE_PYTHON
                                 + " -c " + shlex.quote(script), cfg=cfg)
            if code:
                raise RuntimeError("云端代码尚未支持 RVC 桥接，请先更新云端程序")
            token = self.rvc_session.start(values, cfg)
            try:
                payload = {key: value for key, value in values.items()
                           if (key.startswith("rvc_") or key.startswith("local_tts_") or key == "voice_mode") and key != "rvc_api_key"}
                payload.update(rvc_endpoint=local_rvc.ENDPOINT, rvc_enabled=True,
                               rvc_fallback="off", voice_reply_enabled=True)
                # Secret first: restart cannot see a new endpoint with an old credential.
                remote.write_secret("rvc_api_key", token)
                manager.store.write_config(instance_id, payload)
                manager.restart(instance_id)
            except Exception:
                self.rvc_session.stop()
                raise

        def done(_):
            panel.busy(False)
            panel.refresh()
            if self.current_instance == instance_id:
                self.load_config()
            self.toast("本地语音已连接，离线时只发文字", "ok")

        def failed(message):
            panel.busy(False)
            panel.show_error(message)
            self.toast(message, "err")

        run_async(work, done, failed)

    def stop_rvc(self, panel) -> None:
        panel.busy(True)
        run_async(self.rvc_session.stop,
                  lambda _: (panel.busy(False), panel.refresh()),
                  lambda message: (panel.busy(False), panel.status.setText(message)))

    def preview_rvc(self, panel) -> None:
        if not self._require_connection("试听"):
            return
        text = panel.text.text().strip()
        if not text:
            self.toast("请先输入试听文字", "err")
            return
        instance_id = self.current_instance
        panel.busy(True)
        panel.preview_btn.setText("合成中…")

        def work():
            import base64
            import json
            import shlex
            code, output = remote.run(
                remote.REMOTE_PYTHON + " " + remote.CLI_PATH + " rvc-preview " + shlex.quote(instance_id),
                timeout=400, stdin_text=json.dumps({"text": text}),
            )
            data = remote.parse_json_output(output)
            if code or not data.get("ok"):
                raise RuntimeError(data.get("error") or "云端试听失败")
            audio = base64.b64decode(data["audio"], validate=True)
            target = config_store.ROOT / "data" / "rvc-preview.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(audio)
            return target

        def restore():
            panel.busy(False)
            panel.preview_btn.setText("试听训练音色")

        def done(path):
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            if not hasattr(self, "_rvc_player"):
                self._rvc_audio = QAudioOutput(self)
                self._rvc_player = QMediaPlayer(self)
                self._rvc_player.setAudioOutput(self._rvc_audio)
            self._rvc_player.setSource(QUrl.fromLocalFile(str(path)))
            self._rvc_player.play()
            restore()
            self.toast("完整链路合成成功，正在播放", "ok")

        run_async(work, done, lambda message: (restore(), self.toast(message, "err")))

    def closeEvent(self, event):
        # Network shutdown stays outside the UI event loop.
        import threading
        threading.Thread(target=self.rvc_session.stop, daemon=True).start()
        super().closeEvent(event)


    def test_voice(self) -> None:
        """在服务器上真合成一个「好」字，判断语音链路（含本地隧道）是否可用。"""
        page = self.pages["config"]
        if not self.manager:
            self.toast("未连接服务器，测不了语音", "err")
            return

        button = getattr(page, "btn_voice_test", None)

        def restore() -> None:
            if button is not None:
                button.setEnabled(True)
                button.setText("测试语音")

        if button is not None:
            button.setEnabled(False)
            button.setText("测试中…")
        page.note("正在服务器上合成一句…")

        def done(data: dict[str, Any]) -> None:
            restore()
            probe = (data or {}).get("probe") or {}
            if data.get("ok"):
                size = float(probe.get("bytes") or 0) / 1024
                ms = int(probe.get("ms") or 0)
                page.note(f"语音正常 · {ms} ms · {size:.0f} KB")
                self.toast(f"语音正常，{ms} ms 合成 {size:.0f} KB", "ok")
                return
            error = str((data or {}).get("error") or probe.get("error") or "未知错误")
            hint = "本地语音离线（检查模型与隧道）" if probe.get("local") or data.get("local") else error
            page.note(f"语音不可用 · {hint[:60]}")
            self.toast(f"语音不可用：{hint[:60]}", "err")

        def fail(message: str) -> None:
            restore()
            page.note(f"语音自检失败 · {message[:60]}")
            self.toast(f"语音自检失败：{message[:60]}", "err")

        run_async(lambda: remote.tts_probe(self.current_instance), done, fail)

    # -------------------------------------------------- 主机档案
    def save_host(self, payload: dict[str, Any]) -> None:
        def done(host: dict[str, Any]) -> None:
            self.hosts = remote.list_hosts()
            page = self.pages["connection"]
            page.current_host_id = str(host.get("id"))
            page.apply_hosts(self.hosts)
            page.result.setText("已保存，正在重连…")
            page.result.setStyleSheet(f"color:{T.OK};")
            self.connect_background()

        run_async(lambda: remote.upsert_host(payload), done, lambda m: QMessageBox.critical(self, "保存失败", m))

    def set_active_host(self, host_id: str) -> None:
        def done(_data: dict[str, Any]) -> None:
            self.hosts = remote.list_hosts()
            self.pages["connection"].apply_hosts(self.hosts)
            self.connect_background()

        run_async(lambda: remote.set_active_host(host_id), done, lambda m: QMessageBox.critical(self, "切换失败", m))

    def delete_host(self, host_id: str) -> None:
        if not QMessageBox.question(self, "确认删除", "删除这台主机的连接档案？不影响服务器上的任何东西。"):
            return

        def done(_data: dict[str, Any]) -> None:
            self.hosts = remote.list_hosts()
            page = self.pages["connection"]
            page.current_host_id = ""
            page.apply_hosts(self.hosts)
            self.connect_background()

        run_async(lambda: remote.delete_host(host_id), done, lambda m: QMessageBox.critical(self, "删除失败", m))

    def run_diagnose(self, payload: dict[str, Any]) -> None:
        page = self.pages["connection"]
        page.result.setText("体检中…")
        page.result.setStyleSheet(f"color:{T.INK_3};")

        def done(checks: list[dict[str, Any]]) -> None:
            page.apply_checks(checks)
            page.result.setText("体检完成")

        run_async(lambda: remote.diagnose(payload), done, lambda m: page.result.setText(f"体检失败 · {m}"))

    # -------------------------------------------------- 概览
    def refresh_overview(self, force: bool = False) -> None:
        if not self.manager or not self.connected:
            return
        instance_id = self.current_instance
        memo_get, memo_set = self._memo_get, self._memo_set
        manager_ref = self.manager

        def work() -> dict[str, Any]:
            manager = manager_ref.manager(instance_id)
            snap = manager.host_snapshot(force=force)
            status = snap.get("status") or {}
            metrics = snap.get("metrics") or {}
            napcat = memo_get("napcat", 30)
            if napcat is None:
                napcat = memo_set("napcat", remote.napcat_status())
            config = memo_get(f"config:{instance_id}", 30)
            if config is None:
                config = memo_set(f"config:{instance_id}", manager_ref.store.read_config(instance_id))
            usage = memo_get("usage", 45)
            if usage is None:
                usage = memo_set("usage", remote.read_usage())
            today = date.today().isoformat()
            today_item = (usage or {}).get("days", {}).get(today) or {}
            today_tokens = float(today_item.get("prompt") or 0) + float(today_item.get("completion") or 0)
            today_cost = day_cost(today_item, self.pricing, str((config or {}).get("model") or "deepseek-chat"))
            return {
                "status": status,
                "metrics": metrics,
                "stats": {"today_tokens": today_tokens, "today_cost": today_cost},
                "napcat": napcat,
                "config": config,
                "usage": usage,
                "pricing": self.pricing,
                "hostinfo": remote.read_deploy_env(),
                "logs": manager.snapshot(tail=16),
            }

        run_async(work, lambda data: self.pages["overview"].apply(data), lambda _m: None)

    def lifecycle(self, action: str) -> None:
        if not self.manager:
            QMessageBox.information(self, "未连接", "请先在「连接」页测试连接。")
            return
        instance_id = self.current_instance
        verb = {"start": "启动", "stop": "停止", "restart": "重启"}.get(action, action)
        self._set_status(f"正在{verb}…", "INK_3")
        run_async(
            lambda: getattr(self.manager, action)(instance_id),
            lambda _r: (
                self._memo.pop("napcat", None),
                self.refresh_overview(force=True),
                self.reload_instances(silent=True),
                self._set_status("已连接", T.OK),
            ),
            lambda message: QMessageBox.critical(self, "操作失败", message),
        )

    # -------------------------------------------------- 实例 / 配置 / 日志
    def reload_instances(self, silent: bool = False) -> None:
        if not self.manager:
            return

        def done(items: list[dict[str, Any]]) -> None:
            self.instances = items
            self.pages["instances"].apply(items)
            self._sync_instance_box()

        run_async(
            self.manager.list_instances,
            done,
            (lambda _m: None) if silent else (lambda message: QMessageBox.critical(self, "刷新失败", message)),
        )

    def instance_action(self, instance_id: str, action: str) -> None:
        if not self.manager:
            self.toast("未连接服务器，先在「云服务器」页完成连接", "err")
            return
        run_async(
            lambda: getattr(self.manager, action)(instance_id),
            lambda _r: (self.reload_instances(silent=True), self.refresh_overview(force=True)),
            lambda message: QMessageBox.critical(self, "操作失败", message),
        )

    def delete_instance(self, instance_id: str) -> None:
        if instance_id == "default":
            QMessageBox.warning(self, "不允许", "default 是单实例入口，不能删除。")
            return
        if not QMessageBox.question(
            self, "确认删除", f"删除实例「{instance_id}」？配置与数据会移动到 backups/instances/ 备份。"
        ):
            return

        def done(result: dict[str, Any]) -> None:
            QMessageBox.information(self, "已删除", f"备份位置：\n{result.get('backup') or '（未保留）'}")
            if self.current_instance == instance_id:
                self.current_instance = "default"
            self.reload_instances(silent=True)

        run_async(
            lambda: self.manager.store.delete(instance_id),
            done,
            lambda message: QMessageBox.critical(self, "删除失败", message),
        )

    def open_instance_config(self, instance_id: str) -> None:
        """打开某实例的配置二级窗口（实例列表双击 /「进入配置」）。"""
        if not self.manager:
            QMessageBox.information(self, "未连接", "请先连接服务器。")
            return
        dialog = ConfigDialog(self, instance_id)
        dialog.exec()

    def open_create_dialog(self) -> None:
        if not self.manager:
            QMessageBox.information(self, "未连接", "请先连接服务器。")
            return
        dialog = CreateDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            payload = dialog.payload()

            def done(instance: dict[str, Any]) -> None:
                self.reload_instances()
                new_id = str((instance or {}).get("id") or payload.get("instance_id") or "")
                QMessageBox.information(self, "已创建", f"实例「{new_id}」已创建，正在打开它的配置页。")
                if new_id:
                    self.set_instance(new_id)
                    self.switch_page("config")

            run_async(
                lambda: self.manager.store.create(**payload),
                done,
                lambda message: QMessageBox.critical(self, "创建失败", message),
            )

    def _require_connection(self, title: str = "未连接服务器") -> bool:
        """服务器侧操作的前置检查：缺连接时明说，而不是静默 return。"""
        if self.manager:
            return True
        QMessageBox.information(
            self, title, "还没有连上服务器。请先到「云服务器」页测试连接并保存。"
        )
        return False

    def load_config(self) -> None:
        # 进页面会自动调用，这里只静默标注，不弹窗打断
        if not self.manager:
            self.pages["config"].note("未连接，读不到配置")
            return
        instance_id = self.current_instance

        def work() -> dict[str, Any]:
            config = self.manager.store.read_config(instance_id)
            try:
                secrets = remote.read_secrets()
            except Exception:  # noqa: BLE001 - 读不到密钥不影响配置展示
                secrets = {}
            return {"config": config, "secrets": secrets}

        def done(data: dict[str, Any]) -> None:
            page = self.pages["config"]
            page.apply(data.get("config") or {})
            page.apply_secrets(data.get("secrets") or {})

        run_async(work, done, lambda message: QMessageBox.critical(self, "读取失败", message))

    def save_config(self) -> None:
        if not self._require_connection("保存配置"):
            self._set_status("未连接", T.INK_3)
            self.pages["config"].note("未连接，未保存")
            return
        instance_id = self.current_instance
        payload = self.pages["config"].collect()
        # 密钥不写 config.json（会随实例配置文件到处复制），单独进 .env
        new_secrets = {
            key: str(payload.pop(key, "") or "").strip()
            for key in ("api_key", "asr_api_key", "tts_api_key", "rvc_api_key")
        }
        new_secrets = {key: value for key, value in new_secrets.items() if value}
        self._set_status("保存中…", "INK_3")

        def work() -> dict[str, Any]:
            # 先落配置再改密钥：配置写失败时不会出现「密钥已换、配置没存」的半截状态
            self.manager.store.write_config(instance_id, payload)
            for key, value in new_secrets.items():
                remote.write_secret(key, value)
            return {"key_changed": bool(new_secrets)}

        def done(result: dict[str, Any]) -> None:
            self._memo.pop(f"config:{instance_id}", None)
            self.pages["config"].mark_saved()
            # 配置只在启动时加载，没有热更新：改任何一项都必须重启才生效
            self._set_status("已保存，正在重启…", "INK_3")
            self.toast(
                "密钥已写入服务器，正在重启 bot" if result.get("key_changed")
                else "配置已保存，正在重启 bot 生效",
                "ok",
            )

            def reloaded(_r: Any) -> None:
                self.toast("已重启，新配置已生效", "ok")
                self._set_status("已连接", T.OK)
                self.load_config()

            def restart_failed(message: str) -> None:
                self._set_status("重启失败", T.ERR)
                QMessageBox.critical(self, "重启失败", message)

            run_async(lambda: self.manager.restart(instance_id), reloaded, restart_failed)

        run_async(
            work,
            done,
            lambda message: (QMessageBox.critical(self, "保存失败", message), self.toast("保存失败", "err")),
        )

    def load_logs(self) -> None:
        if not self.manager:
            return
        manager = self.manager.manager(self.current_instance)
        run_async(
            lambda: manager.snapshot(tail=800),
            lambda lines: self.pages["logs"].apply(lines),
            lambda _m: None,
        )

    # -------------------------------------------------- 用量
    def load_usage(self, force: bool = False) -> None:
        if not self.connected:
            self.pages["usage"].apply(
                {"usage": {"available": False, "reason": "未连接服务器"}, "pricing": self.pricing}
            )
            return
        if not force:
            cached = self._memo_get("usage", 45)
            if cached is not None:
                self.pages["usage"].apply({"usage": cached, "pricing": self.pricing})
                return
        run_async(
            lambda: {"usage": remote.read_usage(), "pricing": self.pricing},
            lambda data: (self._memo_set("usage", data["usage"]), self.pages["usage"].apply(data)),
            lambda message: self.pages["usage"].apply(
                {"usage": {"available": False, "reason": message}, "pricing": self.pricing}
            ),
        )

    def save_pricing(self, model: str, payload: dict[str, float]) -> None:
        pricing = remote.load_pricing()
        pricing[model] = payload
        try:
            remote.save_pricing(pricing)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.pricing = pricing
        self._memo.pop("usage", None)
        self._set_status("单价已保存", T.OK)
        self.load_usage(force=True)

    # -------------------------------------------------- NapCat
    def load_napcat(self) -> None:
        run_async(
            lambda: self._memo_set("napcat", remote.napcat_status()),
            lambda info: self.pages["napcat"].apply(info),
            lambda message: self.pages["napcat"].apply({"running": False, "error": message}),
        )

    def update_napcat(self) -> None:
        """拉镜像 + 重启容器。NapCat 页的「更新」按钮接到这里。"""
        if not self.manager:
            self.toast("未连接服务器，无法更新 NapCat", "err")
            return
        answer = QMessageBox.question(
            self,
            "更新 NapCat",
            "会拉取当前镜像的最新版本并重启容器。\n"
            "重启期间 OneBot 会短暂断开，个别情况下需要重新扫码登录。\n\n继续吗？",
        )
        if not answer:
            return
        self._set_status("正在更新 NapCat…", "INK_3")
        run_async(
            remote.napcat_update,
            lambda info: (
                self._memo.pop("napcat", None),
                self.pages["napcat"].apply(info),
                self.toast(
                    "NapCat 已更新，容器已重启" if info.get("updated") else "已是最新版本，无需更新",
                    "ok",
                ),
                self._set_status("已连接", T.OK),
            ),
            lambda message: (
                self._set_status("更新失败", T.ERR),
                QMessageBox.critical(self, "更新失败", message),
            ),
        )

    def napcat_action(self, action: str) -> None:
        self._set_status(f"NapCat {action}…", "INK_3")
        run_async(
            lambda: remote.napcat_action(action),
            lambda info: (self.pages["napcat"].apply(info), self._memo.pop("napcat", None)),
            lambda message: QMessageBox.critical(self, "操作失败", message),
        )

    # -------------------------------------------------- 轮询
    def _tick(self) -> None:
        if not self.connected:
            return
        if self._current_key == "overview":
            self.refresh_overview()
        elif self._current_key == "logs" and self.pages["logs"].auto.isChecked():
            self.load_logs()
        elif self._current_key == "instances":
            self.reload_instances(silent=True)


# ---------------------------------------------------------------- 新建实例
class CreateDialog(QDialog):
    def __init__(self, window: ConsoleWindow) -> None:
        super().__init__(window)
        self.setWindowTitle("新建实例")
        self.setModal(True)
        self.setMinimumSize(580, 540)
        self.resize(600, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(14)

        layout.addWidget(label("新建实例", "PageTitle"))
        layout.addWidget(
            label("每个实例对应一个 QQ 号，需要独立的 NapCat 端口；创建后会自动生成独立配置。", "Muted")
        )

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.fields: dict[str, QLineEdit] = {}
        rows = (
            ("instance_id", "实例 ID", "bot2", "小写字母/数字/下划线，例如 bot2"),
            ("name", "名称", "小号", "显示在实例列表里的名字"),
            ("bot_qq", "QQ 号", "1000000001", "该实例登录的机器人 QQ"),
            ("persona", "人设", "aemeath", "内置 aemeath，也可填提示词文件路径"),
            ("onebot_ws_url", "OneBot 地址", "ws://127.0.0.1:3002", "每个实例需要独立的 NapCat 端口"),
        )
        for key, text, placeholder, hint in rows:
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setMinimumHeight(34)
            holder = QWidget()
            holder_layout = QVBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setSpacing(3)
            holder_layout.addWidget(edit)
            holder_layout.addWidget(label(hint, "Muted"))
            form.addRow(label(text, "Label"), holder)
            self.fields[key] = edit
        layout.addLayout(form)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        cancel = ghost_button("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        create = accent_button("创建实例")
        create.clicked.connect(self._submit)
        buttons.addWidget(create)
        layout.addLayout(buttons)

    def _submit(self) -> None:
        if not self.fields["instance_id"].text().strip():
            QMessageBox.warning(self, "缺少信息", "实例 ID 不能为空。")
            return
        self.accept()

    def payload(self) -> dict[str, Any]:
        data: dict[str, Any] = {key: edit.text().strip() for key, edit in self.fields.items()}
        try:
            data["bot_qq"] = int(data.get("bot_qq") or 0)
        except ValueError:
            data["bot_qq"] = 0
        return data


# ---------------------------------------------------------------- 实例配置（二级窗口）
class ConfigDialog(QDialog):
    """每个实例独立的配置二级窗口：从实例列表双击 /「进入配置」打开。"""

    def __init__(self, window: "ConsoleWindow", instance_id: str) -> None:
        super().__init__(window)
        self.win = window
        self.instance_id = instance_id
        info = next((item for item in window.instances if item.get("id") == instance_id), {})
        name = str(info.get("name") or instance_id)
        self.setWindowTitle(f"实例配置 · {name}（{instance_id}）")
        self.setModal(True)
        self.setMinimumSize(980, 700)
        self.resize(1060, 780)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        self.page = ConfigPage(window)
        self.page.override_instance = instance_id
        self.page.back_btn.hide()
        # 二级窗口用自己的底栏，顶部工具栏只留实例标题与搜索
        self.page.btn_reload.hide()
        self.page.btn_save.hide()
        self.page.save_bar.hide()  # 二级窗口用底栏保存，不用吸底条
        layout.addWidget(self.page, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.status = label("", "Muted")
        footer.addWidget(self.status, 1)
        reload_btn = ghost_button("重新载入", "refresh")
        reload_btn.clicked.connect(self._reload)
        footer.addWidget(reload_btn)
        close = ghost_button("关闭")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        save = accent_button("保存到服务器")
        save.clicked.connect(self._save)
        footer.addWidget(save)
        layout.addLayout(footer)

        QTimer.singleShot(60, self._reload)

    def reject(self) -> None:  # noqa: N802 - Qt 回调
        """v8 误触保护：有未保存修改时先确认，默认「留在此页」。"""
        if getattr(self.page, "_dirty", False):
            box = QMessageBox(self)
            box.setWindowTitle("有未保存的修改")
            box.setText("实例配置有未保存的修改，确定要关闭吗？")
            leave = box.addButton("放弃修改并关闭", QMessageBox.ButtonRole.DestructiveRole)
            stay = box.addButton("留在此页", QMessageBox.ButtonRole.AcceptRole)
            box.setDefaultButton(stay)
            box.exec()
            if box.clickedButton() is stay:
                return
            self.page.discard()
        super().reject()

    def _reload(self) -> None:
        manager = self.win.manager
        if manager is None:
            self.status.setText("未连接服务器，无法读取配置")
            return
        self.status.setText("读取中…")
        run_async(
            lambda: manager.store.read_config(self.instance_id),
            self._apply_config,
            lambda message: self.status.setText(f"读取失败：{message}"),
        )

    def _apply_config(self, config: dict[str, Any]) -> None:
        self.page.apply(config)
        self.status.setText(f"已读取 · {self.instance_id}（{len(config)} 项）")

    def _save(self) -> None:
        manager = self.win.manager
        if manager is None:
            self.status.setText("未连接服务器，无法保存")
            return
        payload = self.page.collect()
        self.status.setText("保存中…")
        run_async(
            lambda: manager.store.write_config(self.instance_id, payload),
            lambda _r: (
                self.page.mark_saved(f"上次保存 {datetime.now().strftime('%H:%M')}"),
                self.status.setText("已保存"),
                self.win.toast("配置已保存到服务器", "ok"),
            ),
            lambda message: self.status.setText(f"保存失败：{message}"),
        )


# ---------------------------------------------------------------- 单实例
_MUTEX: list = []


def _single_instance() -> bool:
    """同一时刻只允许一个控制台窗口。

    启动慢的时候用户会连点图标，每点一次就是一份新进程一份新窗口，
    看起来就是「弹出一堆小窗口」。这里用命名互斥量挡住重复实例，
    并把已经开着的那一个拉到前台。
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, "Local\\IMBridgeConsole_SingleInstance_" + ("personal" if IS_PERSONAL else "public"))
    if not handle:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        return False
    _MUTEX.append(handle)
    return True


# ---------------------------------------------------------------- 入口
class ConsoleApp(QApplication):
    """吞掉 ToolTip 事件。

    窗口出现的瞬间如果鼠标正好压在上面，Qt 会把「悬停提示」当成一个
    独立的顶层 popup 窗口弹出来（实测 136x66、158x52 这种尺寸），
    看起来就像启动时闪了几个小窗。界面本身不依赖 tooltip，直接不回。
    """

    def notify(self, receiver, event):  # noqa: N802 - Qt 回调
        if event.type() == QEvent.Type.ToolTip:
            return True
        return super().notify(receiver, event)


def _dark_palette() -> "QPalette":
    """把默认调色板也设成深色。

    QSS 只管我们自己画的控件；窗口在被系统画出来的头几帧、以及原生标题栏周边，
    用的是 QPalette。默认调色板是浅色的，于是启动瞬间会闪出白色空窗。
    """
    from PySide6.QtGui import QColor as _QColor
    from PySide6.QtGui import QPalette as _QPalette

    palette = _QPalette()
    background = _QColor(T.BG)
    surface = _QColor(T.PANEL_2)
    text = _QColor(T.INK)
    for role in (_QPalette.ColorRole.Window, _QPalette.ColorRole.Base, _QPalette.ColorRole.Button):
        palette.setColor(role, background if role != _QPalette.ColorRole.Base else surface)
    for role in (
        _QPalette.ColorRole.WindowText,
        _QPalette.ColorRole.Text,
        _QPalette.ColorRole.ButtonText,
        _QPalette.ColorRole.ToolTipText,
    ):
        palette.setColor(role, text)
    palette.setColor(_QPalette.ColorRole.Highlight, _QColor(T.ACCENT))
    palette.setColor(_QPalette.ColorRole.HighlightedText, _QColor(T.ACCENT_INK))
    return palette


def _ensure_fonts() -> None:
    """离屏/精简环境下 Qt 拿不到系统字体，中文会变方块。必须在 QApplication 之后调用。"""
    if QFontDatabase.families():
        return
    for path in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\consola.ttf",
    ):
        if Path(path).is_file():
            QFontDatabase.addApplicationFont(path)


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = ConsoleApp(sys.argv)
    if not _single_instance():
        return 0
    app.setApplicationName("IM Bridge 控制台")
    _ensure_fonts()
    # 主题先于窗口应用：跟随系统或读取上次手动选择
    T.apply_theme(T.initial_theme())
    app.setStyleSheet(T.STYLE)
    app.setPalette(T.palette())
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(13)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    window = ConsoleWindow()
    # 按当前屏幕可用区域适配窗口尺寸并居中，避免高 DPI 下窗口超出屏幕右侧
    screen = app.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        width = min(1280, max(940, int(available.width() * 0.86)))
        height = min(850, max(620, int(available.height() * 0.86)))
        window.resize(width, height)
        # 位置必须在 show() 之前定好：show 之后再 move 会让人看到界面整体
        # 从默认位置跳到新位置（用户反馈的「启动后全往右移」）。
        # 靠左上而不是屏幕正中：桌面工具从左侧展开更自然。
        left = available.left() + 80
        top = available.top() + 60
        window.move(left, top)
    else:
        window.resize(1280, 850)
    # 先把原生窗口按最终尺寸建出来，再 show：否则 Qt 会先用一个默认小尺寸
    # 建窗口，撑开的瞬间在屏幕上就是「闪一下小窗」。
    window.winId()
    window.show()
    app.processEvents()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
