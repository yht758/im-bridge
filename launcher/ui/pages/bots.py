"""QQ Bot 实例页 —— 实例表格 + 选中态下的启动 / 停止 / 重启 / 进入配置。

业务全部委托给主窗口（reload_instances / instance_action / open_instance_config /
open_create_dialog），本页只负责呈现与选中态。
"""
from __future__ import annotations

import sys
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QVBoxLayout, QWidget

from .. import components as C
from .. import icons as _icons
from .. import tokens as K

# pages/common.py 内部按「同级包」写 `from . import components / icons / tokens`，
# 而它们真实位于上一层 ui/。这里不改共用文件，只给本包补上同名别名。
for _name, _module in (("components", C), ("icons", _icons), ("tokens", K)):
    sys.modules.setdefault("%s.%s" % (__package__, _name), _module)

from .common import EmptyState, cell, make_table  # noqa: E402

HEADERS = ["实例", "QQ 号", "OneBot 地址", "状态", "PID"]
# 状态 / PID 两列固定宽度，其余列随窗口伸展
COLUMN_WIDTHS = {3: 92, 4: 84}
ROW_HEIGHT = 42


class BotsPage(QWidget):
    """实例列表：工具条 / 操作栏 / 表格 / 空状态。"""

    def __init__(self, window: Any) -> None:
        super().__init__()
        self.win = window
        self.setObjectName("Page")
        self._items: list[dict[str, Any]] = []
        self._selected_id = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(K.CARD_GAP)
        root.addLayout(self._build_header())
        root.addWidget(self._build_table_card(), 1)

    # ---------------------------------------------------------- 结构
    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(C.label("实例", "H2"))
        self.count = C.label("0 个", "Muted")
        row.addWidget(self.count)
        row.addStretch(1)
        self.btn_reload = C.quiet_button("刷新", "refresh-cw")
        self.btn_new = C.primary_button("新建实例", "plus")
        row.addWidget(self.btn_reload)
        row.addWidget(self.btn_new)

        self.btn_reload.clicked.connect(lambda: self.win.reload_instances())
        self.btn_new.clicked.connect(lambda: self.win.open_create_dialog())
        return row

    def _build_table_card(self) -> QWidget:
        card = C.Card(padding=(18, 14, 18, 14), spacing=10)

        # 操作栏：表格上方右侧，未选中时整体禁用
        self.actions_bar = QWidget()
        bar = QHBoxLayout(self.actions_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        bar.addStretch(1)
        self.btn_run = C.secondary_button("启动")
        self.btn_stop = C.secondary_button("停止")
        self.btn_restart = C.secondary_button("重启")
        self.btn_enter = C.quiet_button("进入配置")
        for button in (self.btn_run, self.btn_stop, self.btn_restart, self.btn_enter):
            bar.addWidget(button)
        card.box.addWidget(self.actions_bar)

        self.table = make_table(HEADERS)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        header = self.table.horizontalHeader()
        for column, width in COLUMN_WIDTHS.items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        card.box.addWidget(self.table, 1)

        self.empty = EmptyState("还没有实例", "新建第一个实例开始使用", "server")
        btn_empty_new = C.primary_button("新建实例")
        btn_empty_new.clicked.connect(lambda: self.win.open_create_dialog())
        self.empty.add_action(btn_empty_new)
        card.box.addWidget(self.empty, 1)

        self.btn_run.clicked.connect(lambda: self._action("start"))
        self.btn_stop.clicked.connect(lambda: self._action("stop"))
        self.btn_restart.clicked.connect(lambda: self._action("restart"))
        self.btn_enter.clicked.connect(self._open_config)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.doubleClicked.connect(lambda _index: self._open_config())
        return card

    # ---------------------------------------------------------- 数据
    def enter(self) -> None:
        self.apply(list(getattr(self.win, "instances", None) or []))
        if bool(getattr(self.win, "connected", False)):
            self.win.reload_instances(silent=True)

    def apply(self, instances: list[dict]) -> None:
        self._items = [dict(item) for item in instances or []]
        self.count.setText("%d 个" % len(self._items))

        keep = self._selected_id
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            status = item.get("status") or {}
            running = bool(status.get("running"))
            values = (
                str(item.get("name") or item.get("id") or "未命名"),
                str(item.get("bot_qq") or "—"),
                str(item.get("onebot_ws_url") or "—"),
                "运行中" if running else "已停止",
                str(status.get("pid") or "—"),
            )
            # 只有状态列着色，其余保持正文色，避免整行变绿
            for column, text in enumerate(values):
                entry = cell(text)
                if column == 3:
                    entry.setForeground(K.qcolor(K.t("OK") if running else K.t("INK_2")))
                self.table.setItem(row, column, entry)
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, str(item.get("id") or ""))

        self.table.clearSelection()
        self._selected_id = ""
        if keep:
            for row, item in enumerate(self._items):
                if str(item.get("id") or "") == keep:
                    self.table.selectRow(row)
                    self._selected_id = keep
                    break
        self.table.blockSignals(False)

        has_items = bool(self._items)
        self.actions_bar.setVisible(has_items)
        self.table.setVisible(has_items)
        self.empty.setVisible(not has_items)
        self._sync_actions()

    # ---------------------------------------------------------- 选中
    def _current_id(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 0)
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or item.text())

    def _item(self, instance_id: str) -> dict[str, Any] | None:
        for item in self._items:
            if str(item.get("id") or "") == instance_id:
                return item
        return None

    def _on_selection_changed(self) -> None:
        self._selected_id = self._current_id()
        self._sync_actions()

    def _sync_actions(self) -> None:
        item = self._item(self._selected_id) if self._selected_id else None
        selected = item is not None
        running = bool(((item or {}).get("status") or {}).get("running"))
        self.btn_enter.setEnabled(selected)
        self.btn_run.setEnabled(selected and not running)
        self.btn_stop.setEnabled(selected and running)
        self.btn_restart.setEnabled(selected and running)

    # ---------------------------------------------------------- 动作
    def _action(self, action: str) -> None:
        instance_id = self._selected_id or self._current_id()
        if not instance_id:
            self.win.toast("请先在列表中选中一个实例", "err")
            return
        self.win.instance_action(instance_id, action)

    def _open_config(self) -> None:
        instance_id = self._selected_id or self._current_id()
        if not instance_id:
            self.win.toast("请先在列表中选中一个实例", "err")
            return
        self.win.open_instance_config(instance_id)
