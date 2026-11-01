"""日志记录页 —— 胶囊筛选 + 只读按级别着色 + 自动刷新。

数据由主窗口 `load_logs()` 拉取后经 `apply(lines)` 灌入，本页不碰网络，
只负责筛选、着色、复制与清屏。渲染走 `QTextCursor` + `QTextCharFormat`，
不拼 HTML —— 日志正文里的尖括号不会被当成标签。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import components as C
from .. import tokens as K

MAX_LINES = 2000

# (键, 文案) —— 顺序即工具条顺序
FILTERS: tuple[tuple[str, str], ...] = (
    ("all", "全部"),
    ("error", "错误"),
    ("warn", "警告"),
    ("info", "信息"),
)

# 筛选键 → 在 level / text 里匹配的关键字
_MATCH = {"error": "ERROR", "warn": "WARN", "info": "INFO"}


def _pill_qss(active: bool) -> str:
    """胶囊按钮三态样式：选中黑底白字（深色主题下自动反转）。"""
    ink = K.t("ACCENT_INK") if active else K.t("INK_2")
    background = K.t("ACCENT") if active else K.t("SURFACE_2")
    hover = K.t("ACCENT_HOVER") if active else K.t("SURFACE_3")
    hover_ink = K.t("ACCENT_INK") if active else K.t("INK")
    return (
        "QPushButton { border: none; border-radius: 14px; padding: 0 14px;"
        " font-size: 12px; color: %s; background: %s; }"
        "QPushButton:hover { color: %s; background: %s; }"
        % (ink, background, hover_ink, hover)
    )


class LogsPage(QWidget):
    """日志：筛选条 + 只读文本视图。"""

    def __init__(self, window: Any) -> None:
        super().__init__()
        self.win = window
        self.setObjectName("Page")

        self._filter = "all"
        self._lines: list[dict[str, Any]] = []
        self._shown = 0
        self._theme_seen = str(K.current_theme())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        layout.addLayout(self._build_toolbar())

        card = C.Card(padding=(16, 14, 16, 14), spacing=0)
        self.view = QPlainTextEdit()
        self.view.setObjectName("LogView")
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setReadOnly(True)
        self.view.setUndoRedoEnabled(False)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setFont(C.font(12, 400, mono=True))
        card.box.addWidget(self.view, 1)
        layout.addWidget(card, 1)

        self._style_view()
        self._sync_filters()

    # ---------------------------------------------------------- 结构
    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)

        self.filters: dict[str, QPushButton] = {}
        for key, text in FILTERS:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(28)
            button.clicked.connect(lambda _checked=False, k=key: self._set_filter(k))
            self.filters[key] = button
            toolbar.addWidget(button)

        toolbar.addStretch(1)

        # 外层 _tick 会读 self.pages["logs"].auto.isChecked()
        self.auto = QCheckBox("自动刷新")
        self.auto.setChecked(True)
        self.auto.setCursor(Qt.CursorShape.PointingHandCursor)
        toolbar.addWidget(self.auto)
        toolbar.addSpacing(4)

        self.btn_copy = C.quiet_button("复制", "copy")
        self.btn_clear = C.quiet_button("清屏", "trash")
        self.btn_reload = C.quiet_button("刷新", "refresh-cw")
        for button in (self.btn_copy, self.btn_clear, self.btn_reload):
            toolbar.addWidget(button)

        self.btn_copy.clicked.connect(self._copy)
        self.btn_clear.clicked.connect(self._clear)
        self.btn_reload.clicked.connect(lambda: self._reload(manual=True))
        return toolbar

    # ---------------------------------------------------------- 样式
    def _style_view(self) -> None:
        """卡片内的文本视图：无边框、背景透明（用 id 选择器压过全局表单样式）。"""
        self.view.setStyleSheet(
            "QPlainTextEdit#LogView { background: transparent; border: none;"
            " border-radius: 0px; padding: 0px; min-height: 0px; }"
        )
        self.view.viewport().setAutoFillBackground(False)

    def _sync_filters(self) -> None:
        for key, button in self.filters.items():
            active = key == self._filter
            button.setChecked(active)
            button.setStyleSheet(_pill_qss(active))

    def retheme(self) -> None:
        """主题切换后按新令牌重刷本页局部样式。"""
        self._style_view()
        self._sync_filters()
        self._render()

    # ---------------------------------------------------------- 交互
    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._sync_filters()
        self._render()

    def _reload(self, manual: bool = False) -> None:
        # 断连时「刷新」要是真有反应：手点给提示，自动轮询则安静跳过
        if manual and not bool(getattr(self.win, "connected", False)):
            toast = getattr(self.win, "toast", None)
            if callable(toast):
                toast("未连接服务器，读不到日志", "err")
            return
        loader = getattr(self.win, "load_logs", None)
        if callable(loader):
            loader()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.view.toPlainText())
        toast = getattr(self.win, "toast", None)
        if callable(toast):
            toast("已复制 %d 行" % self._shown, "ok")

    def _clear(self) -> None:
        self._lines.clear()
        self._shown = 0
        self.view.clear()
        toast = getattr(self.win, "toast", None)
        if callable(toast):
            toast("已清屏", "ok")

    # ---------------------------------------------------------- 数据
    def enter(self) -> None:
        self._reload()

    def apply(self, lines: list[dict[str, Any]]) -> None:
        self._lines = [dict(item) for item in (lines or []) if isinstance(item, dict)][-MAX_LINES:]
        self._render()

    def _visible(self) -> list[dict[str, Any]]:
        if self._filter == "all":
            return self._lines[-MAX_LINES:]
        keyword = _MATCH.get(self._filter, "")
        picked = [
            line
            for line in self._lines
            if keyword in str(line.get("level") or "").upper()
            or keyword in str(line.get("text") or "").upper()
        ]
        return picked[-MAX_LINES:]

    @staticmethod
    def _split_log_line(text: str) -> tuple[str, str, str]:
        """把 "10:24:01 bot[283] 消息" 拆成（时间, 来源, 消息），用于对齐三列。"""
        head, _, rest = text.partition(" ")
        if len(head) == 8 and head.count(":") == 2:
            source, _, message = rest.partition(" ")
            return head, source.split("[")[0], message or rest
        return "", "", text

    @staticmethod
    def _format_for(level: str) -> QTextCharFormat:
        """INFO → INK_2；WARN → INK 加粗；ERROR → ERR。"""
        fmt = QTextCharFormat()
        if "ERROR" in level:
            fmt.setForeground(K.qcolor(K.t("ERR")))
        elif "WARN" in level:
            fmt.setForeground(K.qcolor(K.t("INK")))
            fmt.setFontWeight(QFont.Weight.DemiBold)
        else:
            fmt.setForeground(K.qcolor(K.t("INK_2")))
        return fmt

    def _render(self) -> None:
        # 外层从侧栏切换主题时不会回调本页，这里按当前令牌顺手补一次局部样式
        theme = str(K.current_theme())
        if theme != self._theme_seen:
            self._theme_seen = theme
            self._style_view()
            self._sync_filters()

        shown = self._visible()
        self._shown = len(shown)

        bar = self.view.verticalScrollBar()
        prev = bar.value()
        at_bottom = prev >= bar.maximum() - 4
        self.view.clear()

        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        head_fmt = QTextCharFormat()
        head_fmt.setForeground(K.qcolor(K.t("INK_3")))

        for index, line in enumerate(shown):
            if index:
                cursor.insertBlock()
            raw = str(line.get("text") or "") or " "
            time_part, source, message = self._split_log_line(raw)
            if time_part or source:
                cursor.insertText("%-9s%-10s" % (time_part, source), head_fmt)
            cursor.insertText(message, self._format_for(str(line.get("level") or "").upper()))

        if at_bottom or self.auto.isChecked():
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(prev, bar.maximum()))
