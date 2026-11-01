"""云服务器页 —— 左栏服务器列表，右栏连接信息表单与连接体检。

业务全部委托给主窗口（test_connection / save_host / set_active_host /
delete_host / run_diagnose），本页只负责呈现与收集参数。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import components as C
from .. import icons as _icons
from .. import tokens as K
from ..format import mask_host

from .common import EmptyState, FormRow, SearchBox  # noqa: E402

# (字段名, 标签, 占位提示) —— 与旧连接页的 collect() 结构保持一致
FIELDS: tuple[tuple[str, str, str], ...] = (
    ("name", "名称", "生产服务器"),
    ("host", "地址", "203.0.113.10"),
    ("port", "SSH 端口", "22"),
    ("user", "用户名", "root"),
    ("key", "私钥路径", r"D:\爱弥斯_bot\.tools\deploy\id_im_bridge"),
    ("dir", "项目目录", "/opt/im-bridge"),
)


class _StatusLine(QLabel):
    """表单底部的结果行：没有内容时不占高度。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setObjectName("Muted")
        self.setWordWrap(True)
        self.setVisible(False)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.setVisible(bool(text.strip()))


class _HostRow(QWidget):
    """服务器列表里的一整条：图标 + 名称/地址 + 右侧徽章，整条可点。"""

    clicked = Signal(str)

    def __init__(self, host: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.host_id = str(host.get("id") or "")
        self.setObjectName("HostRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 10, 6)
        row.setSpacing(10)
        row.addWidget(C.IconTile("server", 34), 0, Qt.AlignmentFlag.AlignVCenter)

        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        text_box.addWidget(C.label(str(host.get("name") or host.get("host") or "未命名服务器"), "H3"))
        text_box.addWidget(C.label(mask_host(host.get("host")), "Mono"))
        row.addLayout(text_box, 1)

        active = bool(host.get("active"))
        row.addWidget(
            C.Badge("当前" if active else "备用", "ok" if active else "off"),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        self.set_selected(False)

    def set_selected(self, value: bool) -> None:
        color = str(K.t("SURFACE_2")) if value else "transparent"
        self.setStyleSheet(
            "QWidget#HostRow { background: %s; border-radius: %dpx; }" % (color, K.RADIUS_MD)
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.host_id)
        super().mouseReleaseEvent(event)


class ServersPage(QWidget):
    """云服务器：列表 / 连接信息 / 连接体检。"""

    def __init__(self, window: Any) -> None:
        super().__init__()
        self.win = window
        self.setObjectName("Page")
        self.current_host_id = ""
        self._hosts: list[dict[str, Any]] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(K.CARD_GAP)
        root.addWidget(self._build_list(), 0)

        right = QVBoxLayout()
        right.setSpacing(K.CARD_GAP)
        right.addWidget(self._build_form(), 0)
        right.addWidget(self._build_checks(), 1)
        root.addLayout(right, 1)

        self._sync_state()

    # ---------------------------------------------------------- 结构
    def _build_list(self) -> QWidget:
        card = C.Card(padding=(16, 16, 16, 14), spacing=10)
        card.setFixedWidth(300)
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        head = C.SectionTitle("服务器列表")
        self.btn_add = C.primary_button("添加", "plus")
        self.btn_add.clicked.connect(self._new_host)
        head.add_action(self.btn_add)
        card.box.addWidget(head)

        self.search = SearchBox("搜索服务器…")
        self.search.textChanged.connect(lambda _text: self._rebuild_list())
        card.box.addWidget(self.search)

        self._list_box = QVBoxLayout()
        self._list_box.setContentsMargins(0, 0, 0, 0)
        self._list_box.setSpacing(2)
        card.box.addLayout(self._list_box, 1)

        self.empty = EmptyState("还没有服务器", "点「添加」录入第一台", "server")
        card.box.addWidget(self.empty)
        return card

    def _build_form(self) -> QWidget:
        card = C.Card(padding=(20, 18, 20, 18), spacing=10)
        head = C.SectionTitle("连接信息", "SSH 密钥登录，密码不落盘")
        self.status_badge = C.Badge("未连接", "off")
        head.add_action(self.status_badge)
        card.box.addWidget(head)

        self.fields: dict[str, QLineEdit] = {}
        for key, title, placeholder in FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            self.fields[key] = edit
            field: QWidget = edit
            if key == "key":
                field = self._with_browse(edit)
            card.box.addWidget(FormRow(title, field, label_width=96))

        self.result = _StatusLine()
        card.box.addWidget(self.result)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_test = C.primary_button("测试连接")
        self.btn_save = C.secondary_button("保存")
        self.btn_active = C.quiet_button("设为当前")
        self.btn_delete = C.quiet_button("删除")
        self.btn_delete.setStyleSheet("QPushButton#Quiet { color: %s; }" % K.t("ERR"))
        actions.addWidget(self.btn_test)
        actions.addWidget(self.btn_save)
        actions.addWidget(self.btn_active)
        actions.addWidget(self.btn_delete)
        actions.addStretch(1)
        card.box.addLayout(actions)

        self.btn_test.clicked.connect(self._test)
        self.btn_save.clicked.connect(self._save)
        self.btn_active.clicked.connect(self._set_active)
        self.btn_delete.clicked.connect(self._delete)
        return card

    def _with_browse(self, edit: QLineEdit) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(edit, 1)
        browse = C.quiet_button("选择")
        browse.clicked.connect(self._pick_key)
        row.addWidget(browse)
        return holder

    def _build_checks(self) -> QWidget:
        card = C.Card(padding=(20, 18, 20, 18), spacing=8)
        head = C.SectionTitle("连接体检", "任一项失败都会指出是哪一步")
        self.btn_diagnose = C.quiet_button("重新体检", "activity")
        self.btn_diagnose.clicked.connect(lambda: self.win.run_diagnose(self.collect()))
        head.add_action(self.btn_diagnose)
        card.box.addWidget(head)

        self._checks_box = QVBoxLayout()
        self._checks_box.setContentsMargins(0, 0, 0, 0)
        self._checks_box.setSpacing(0)
        card.box.addLayout(self._checks_box)

        self.checks_hint = C.label("还没有体检结果，点「重新体检」检查这台服务器。", "Muted")
        card.box.addWidget(self.checks_hint)
        card.box.addStretch(1)
        return card

    # ---------------------------------------------------------- 数据
    def enter(self) -> None:
        self.apply_hosts(list(getattr(self.win, "hosts", None) or []))

    def collect(self) -> dict:
        payload: dict[str, Any] = {key: edit.text().strip() for key, edit in self.fields.items()}
        payload["id"] = self.current_host_id
        try:
            payload["port"] = int(payload.get("port") or 22)
        except ValueError:
            payload["port"] = 22
        return payload

    def apply_hosts(self, hosts: list[dict]) -> None:
        self._hosts = [dict(host) for host in hosts or []]
        if self.current_host_id and not any(
            str(host.get("id") or "") == self.current_host_id for host in self._hosts
        ):
            self.current_host_id = ""
        self._rebuild_list()
        if not self.current_host_id:
            active = next((host for host in self._hosts if host.get("active")), None)
            if active:
                self._load_host(active)
        self._sync_state()

    def apply_checks(self, checks: list[dict]) -> None:
        while self._checks_box.count():
            item = self._checks_box.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.checks_hint.setVisible(not checks)
        for index, check in enumerate(checks):
            if index:
                self._checks_box.addWidget(C.hline())
            self._checks_box.addWidget(self._check_row(check))

    # ---------------------------------------------------------- 列表
    def _rebuild_list(self) -> None:
        while self._list_box.count():
            item = self._list_box.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        keyword = self.search.text().strip().lower()
        matched = [host for host in self._hosts if self._matches(host, keyword)]
        for host in matched:
            row = _HostRow(host)
            row.clicked.connect(self._select_host)
            row.set_selected(str(host.get("id") or "") == self.current_host_id)
            self._list_box.addWidget(row)
        if self._hosts and not matched:
            self._list_box.addWidget(C.label("没有匹配的服务器", "Muted"))
        self._list_box.addStretch(1)
        self.empty.setVisible(not self._hosts)

    @staticmethod
    def _matches(host: dict[str, Any], keyword: str) -> bool:
        if not keyword:
            return True
        haystack = " ".join(
            str(host.get(key) or "") for key in ("name", "host", "user", "dir")
        ).lower()
        return keyword in haystack

    def _check_row(self, check: dict[str, Any]) -> QWidget:
        ok = bool(check.get("ok"))
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 6, 0, 6)
        box.setSpacing(9)
        box.addWidget(C.StatusDot("ok" if ok else "err", 6), 0, Qt.AlignmentFlag.AlignVCenter)
        name = C.label(str(check.get("label") or ""), "Body")
        name.setFixedWidth(118)
        box.addWidget(name)
        detail = C.label(str(check.get("detail") or ""), "Muted")
        detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        box.addWidget(detail, 1)
        return row

    def _select_host(self, host_id: str) -> None:
        for host in self._hosts:
            if str(host.get("id") or "") == host_id:
                self._load_host(host)
                break
        self._rebuild_list()
        self._sync_state()

    def _load_host(self, host: dict[str, Any]) -> None:
        self.current_host_id = str(host.get("id") or "")
        for key, _title, _placeholder in FIELDS:
            self.fields[key].setText(str(host.get(key) or ""))
        self.result.setText("")

    # ---------------------------------------------------------- 动作
    def _new_host(self) -> None:
        self.current_host_id = ""
        for edit in self.fields.values():
            edit.clear()
        self.fields["port"].setText("22")
        self.fields["user"].setText("root")
        self.fields["dir"].setText("/opt/im-bridge")
        self.search.clear()
        self._rebuild_list()
        self._sync_state()
        self.result.setText("新服务器：填好后点「保存」")

    def _pick_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 SSH 私钥", str(Path.home() / ".ssh"))
        if path:
            self.fields["key"].setText(path)

    def _test(self) -> None:
        payload = self.collect()
        if not payload.get("host"):
            QMessageBox.warning(self, "缺少信息", "先填服务器地址，再点「测试连接」。")
            return
        self.win.test_connection(payload)

    def _save(self) -> None:
        payload = self.collect()
        if not payload.get("host"):
            QMessageBox.warning(self, "缺少信息", "服务器地址不能为空。")
            return
        self.win.save_host(payload)

    def _set_active(self) -> None:
        if not self.current_host_id:
            QMessageBox.information(self, "提示", "先保存这台服务器。")
            return
        self.win.set_active_host(self.current_host_id)

    def _delete(self) -> None:
        if not self.current_host_id:
            return
        answer = QMessageBox.question(
            self,
            "确认删除",
            "删除这台服务器的连接档案？不影响服务器上的任何东西。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.win.delete_host(self.current_host_id)

    # ---------------------------------------------------------- 状态
    def _sync_state(self) -> None:
        connected = bool(getattr(self.win, "connected", False))
        self.status_badge.set_text("已连接" if connected else "未连接")
        self.status_badge.set_tone("ok" if connected else "off")
        # 表单与「测试 / 保存」始终可用：新增主机时本来就没连上，
        # 把这两个按钮绑在 connected 上会让首次配置无路可走（按钮看着在、点不动）。
        self.btn_test.setEnabled(True)
        self.btn_save.setEnabled(True)
        has_host = bool(self.current_host_id)
        self.btn_active.setEnabled(has_host)
        self.btn_delete.setEnabled(has_host)
