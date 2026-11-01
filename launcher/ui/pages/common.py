"""页面骨架：工具栏 / 搜索框 / 表单行 / 二级导航 / 空状态 / 表格。

所有页面共用这里，保证间距、字号、控件状态一致。页面只负责数据与交互。
"""
from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import components as C
from .. import icons
from .. import tokens as K


class Toolbar(QWidget):
    """页面工具条：左侧标题/说明，右侧动作按钮。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(8)
        self.row.addStretch(1)

    def add_left(self, widget: QWidget) -> None:
        self.row.insertWidget(0, widget)

    def add_action(self, widget: QWidget) -> None:
        self.row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)


class SearchBox(QLineEdit):
    def __init__(self, placeholder: str = "搜索…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setFixedHeight(34)
        self.setMinimumWidth(180)
        self.addAction(icons.icon("search", str(K.t("INK_3")), 15), QLineEdit.ActionPosition.LeadingPosition)


class FormRow(QWidget):
    """行式表单：120px 标签 + 控件 + 可选说明。整行悬停有底色。"""

    def __init__(
        self,
        title: str,
        field: QWidget,
        hint: str = "",
        label_width: int = 120,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "FormRow { background: transparent; border-radius: 8px; }"
            "FormRow:hover { background: rgba(127, 127, 127, 0.06); }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(12)
        label = C.label(title, "Label")
        label.setFixedWidth(label_width)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
        box = QVBoxLayout()
        box.setSpacing(3)
        box.addWidget(field)
        if hint:
            hint_label = C.label(hint, "Hint")
            hint_label.setWordWrap(True)
            box.addWidget(hint_label)
        row.addLayout(box, 1)


class SubNav(QWidget):
    """左侧二级导航（AI 配置 / 设置）。"""

    changed = Signal(str)

    def __init__(self, items: Iterable[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(168)
        self._buttons: dict[str, QPushButton] = {}
        self._current = ""
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        for key, text in items:
            button = QPushButton(text)
            button.setObjectName("Quiet")
            button.setCheckable(False)
            button.setFixedHeight(34)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                "QPushButton { text-align: left; padding: 0 10px; border: none;"
                " border-radius: 9px; color: %s; font-size: 13px; background: transparent; }"
                "QPushButton:hover { background: %s; color: %s; }"
                % (K.t("INK_2"), K.t("SURFACE_2"), K.t("INK"))
            )
            button.clicked.connect(lambda _c=False, k=key: self.select(k))
            box.addWidget(button)
            self._buttons[key] = button
        box.addStretch(1)

    def retheme(self) -> None:
        if self._current:
            self.select(self._current)

    def select(self, key: str) -> None:
        if key not in self._buttons:
            return
        self._current = key
        for name, button in self._buttons.items():
            active = name == key
            button.setStyleSheet(
                "QPushButton { text-align: left; padding: 0 10px; border: none;"
                " border-radius: 9px; font-size: 13px; font-weight: %s;"
                " color: %s; background: %s; }"
                "QPushButton:hover { background: %s; }"
                % (
                    "600" if active else "400",
                    K.t("INK") if active else K.t("INK_2"),
                    K.t("SURFACE_3") if active else "transparent",
                    K.t("SURFACE_2"),
                )
            )
        self.changed.emit(key)


class EmptyState(QWidget):
    """空状态：图标 + 标题 + 说明 + 可选动作。"""

    def __init__(self, title: str, hint: str = "", icon_name: str = "folder", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 40, 0, 40)
        box.setSpacing(8)
        box.addStretch(1)
        tile = C.IconTile(icon_name, 44)
        box.addWidget(tile, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title = C.label(title, "H3")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.title)
        self.hint = C.label(hint, "Muted")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setWordWrap(True)
        box.addWidget(self.hint)
        self.row = QHBoxLayout()
        self.row.setSpacing(8)
        self.row.addStretch(1)
        box.addLayout(self.row)
        self.row.addStretch(1)
        box.addStretch(1)

    def add_action(self, widget: QWidget) -> None:
        self.row.insertWidget(self.row.count() - 1, widget)


def make_table(headers: list[str], widths: list[int] | None = None) -> QTableWidget:
    """统一表格：无网格、行高 40、表头左对齐、整行选中。"""
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    table.verticalHeader().setDefaultSectionSize(42)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    header.setHighlightSections(False)
    if widths:
        for index, width in enumerate(widths):
            if width:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(index, width)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return table


def cell(text: str, mono: bool = False, align_right: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if mono:
        item.setFont(C.font(12, 400, mono=True))
    if align_right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


class KeyValue(QWidget):
    """两列键值行（连接信息、版本信息）。"""

    def __init__(self, key: str, value: str = "—", mono: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 5, 0, 5)
        row.setSpacing(12)
        row.addWidget(C.label(key, "Label"), 1)
        self.value = C.label(value, "Mono" if mono else "Body")
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.value, 2)

    def set_text(self, text: str) -> None:
        self.value.setText(text)


class PageBody(QWidget):
    """页面内容容器：统一纵向间距。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(14)
