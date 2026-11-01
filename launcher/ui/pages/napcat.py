"""NapCat 页 —— 容器状态卡 + 配置参数卡。

状态与操作全部来自主窗口（load_napcat / napcat_action），本页只做呈现；
`apply(info)` 对字段缺失一律显示「—」，不补任何本地臆测值。
"""
from __future__ import annotations

import sys
from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from PySide6.QtCore import Qt

from .. import components as C
from .. import icons as _icons
from .. import tokens as K
from ..poster_art import PosterArt
from ..edition import IS_PERSONAL
from ..icons_data import ICONS as _ICONS

# pages/common.py 内部按「同级包」写 `from . import components / icons / tokens`，
# 而它们真实位于上一层 ui/。这里不改共用文件，只给本包补上同名别名。
for _name, _module in (("components", C), ("icons", _icons), ("tokens", K)):
    sys.modules.setdefault("%s.%s" % (__package__, _name), _module)

from .common import KeyValue  # noqa: E402

# icons_data 里暂无 download 图形；缺省时退回「循环」图标，避免退化成一条横线
UPDATE_ICON = "download" if "download" in _ICONS else "rotate-cw"

# OneBot 地址 / 端口在状态接口里可能出现的字段名（拿不到就显示「—」）
WS_KEYS = ("onebot_ws_url", "ws", "url", "onebot_url")
PORT_KEYS = ("port", "onebot_port", "ws_port")


def _first(info: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = info.get(key)
        if value not in (None, ""):
            return value
    return ""


def _format_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    return text[:19].replace("T", " ")


class NapcatPage(QWidget):
    """NapCat：运行状态、版本、启动时间与动作行，下面是配置参数。"""

    def __init__(self, window: Any) -> None:
        super().__init__()
        self.win = window
        self.setObjectName("Page")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(K.CARD_GAP)
        content = QHBoxLayout()
        content.setSpacing(K.CARD_GAP)
        controls = QVBoxLayout()
        controls.setSpacing(K.CARD_GAP)
        controls.addWidget(self._build_status_card())
        controls.addWidget(self._build_config_card())
        controls.addStretch(1)
        content.addLayout(controls, 1)
        self.page_art = None
        if IS_PERSONAL:
            self.page_art = PosterArt("napcat-portrait.jpg")
            self.page_art.setFixedSize(190, 400)
            content.addWidget(self.page_art, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(content, 1)

    # ---------------------------------------------------------- 结构
    def _build_status_card(self) -> QWidget:
        card = C.Card(padding=(20, 18, 20, 18), spacing=10)

        head = C.SectionTitle("NapCat")
        self.badge = C.Badge("未知", "off")
        head.add_action(self.badge)
        card.box.addWidget(head)

        self.row_state = KeyValue("连接状态", "—")
        self.row_version = KeyValue("版本", "—", mono=True)
        self.row_started = KeyValue("启动时间", "—", mono=True)
        for row in (self.row_state, self.row_version, self.row_started):
            card.box.addWidget(row)

        self.detail = C.label("", "Muted")
        self.detail.hide()
        self.detail.setWordWrap(True)
        card.box.addWidget(self.detail)

        card.box.addWidget(C.hline())

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_start = C.primary_button("启动")
        self.btn_stop = C.secondary_button("停止")
        self.btn_update = C.quiet_button("更新", UPDATE_ICON)
        self.btn_reload = C.quiet_button("刷新", "refresh-cw")
        for button in (self.btn_start, self.btn_stop, self.btn_update, self.btn_reload):
            actions.addWidget(button)
        actions.addStretch(1)
        card.box.addLayout(actions)

        self.btn_start.clicked.connect(lambda: self.win.napcat_action("start"))
        self.btn_stop.clicked.connect(lambda: self.win.napcat_action("stop"))
        self.btn_reload.clicked.connect(lambda: self.win.load_napcat())
        # 主窗口目前没有对外暴露更新入口：有则接上，没有就禁用而不是瞎调接口
        updater = getattr(self.win, "update_napcat", None)
        if callable(updater):
            self.btn_update.clicked.connect(lambda: updater())
        else:
            self.btn_update.setEnabled(False)
            self.btn_update.setToolTip("当前窗口未提供 NapCat 更新接口")
        return card

    def _build_config_card(self) -> QWidget:
        card = C.Card(padding=(20, 18, 20, 18), spacing=6)
        card.box.addWidget(C.SectionTitle("配置参数"))
        self.row_ws = KeyValue("OneBot 地址", "—", mono=True)
        self.row_port = KeyValue("端口", "—", mono=True)
        card.box.addWidget(self.row_ws)
        card.box.addWidget(self.row_port)
        return card

    # ---------------------------------------------------------- 数据
    def enter(self) -> None:
        if bool(getattr(self.win, "connected", False)):
            self.win.load_napcat()

    def apply(self, info: dict) -> None:
        info = info or {}
        state_raw = str(info.get("state") or "").strip()
        running = info.get("running")
        if running is None and state_raw:
            running = state_raw == "running"

        if running is None:
            self.badge.set_text("未知")
            self.badge.set_tone("off")
            self.row_state.set_text("—")
        else:
            text = "运行中" if running else "已停止"
            self.badge.set_text(text)
            self.badge.set_tone("ok" if running else "off")
            self.row_state.set_text(text)

        self.row_version.set_text(str(info.get("version") or "—"))
        self.row_started.set_text(_format_time(info.get("started_at")))
        detail = str(info.get("detail") or "").strip()
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.row_ws.set_text(str(_first(info, WS_KEYS) or "—"))
        self.row_port.set_text(str(_first(info, PORT_KEYS) or "—"))
