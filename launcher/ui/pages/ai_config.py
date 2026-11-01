"""AI 配置页 —— 左侧分组二级导航 + 右侧分组表单 + 底部吸底保存条。

字段元数据全部来自 `qt_theme.FIELD_UI` / `GROUP_ORDER`，本页只负责呈现、
脏标记与收集；真正的读写由 `ConsoleWindow`（load_config / save_config）完成。
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QComboBox,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import components as C
from .. import icons as _icons
from .. import tokens as K
from ..poster_art import PosterArt
from ..edition import IS_PERSONAL

# 主题常量与开关组件位于 launcher/ 根目录（与 desktop_qt 相同的导入约定：
# 运行时 sys.path 已包含 launcher/）。
import config_store
import qt_theme as T

_SCHEMA = {spec['key']: spec for spec in config_store.SCHEMA}

from .common import FormRow, SearchBox, SubNav  # noqa: E402

# 布尔字段（配置里以 "1" / "" / "true" 之类字符串存储）
BOOL_KEYS = {
    "respond_to_groups",
    "summary_enabled",
    "summary_async",
    "usage_enabled",
    "streaming_enabled",
    "chunking_enabled",
    "proactive_enabled",
    "voice_input_enabled",
    "voice_reply_enabled",
    "sticker_enabled",
    "rvc_enabled",
}

_TRUE_VALUES = {"1", "true", "yes", "on", "y", "t"}

# 等宽字体：接口地址类字段
_MONO_SUFFIX = ("_url", "_endpoint")


def _flatten_ids(value: Any) -> list[str]:
    """把 list / "123,456" / "['123']" / ["['123']"] 统一摊平成 ID 字符串列表。

    历史版本的管理台把列表字段写成了字符串化列表（甚至套了两层），
    直接展示会变成 ``["['10001']"]``，用户根本改不回来。
    """
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            items.extend(_flatten_ids(item))
        return items
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        parsed: Any = None
        try:
            parsed = json.loads(text)
        except ValueError:
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                parsed = None
        if isinstance(parsed, (list, tuple, str)):
            return _flatten_ids(parsed)
    return [item.strip() for item in text.replace("，", ",").split(",") if item.strip()]


def _truthy(value: Any) -> bool:
    """把配置里的 1 / "true" / "" 等写法统一成 bool。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in _TRUE_VALUES


def _is_mono(key: str) -> bool:
    return key == "base_url" or key.endswith(_MONO_SUFFIX)


def _toggle_class() -> type:
    """开关优先取 components 的导出，缺失时回落到 widgets.ToggleSwitch。"""
    exported = getattr(C, "ToggleSwitch", None)
    if exported is not None:
        return exported
    import widgets as W

    return W.ToggleSwitch


class _Choice(QComboBox):
    def text(self):
        return self.currentData()

    def setText(self, value):
        self.setCurrentIndex(max(0, self.findData(value)))


class AIConfigPage(QWidget):
    """按分组编辑当前实例的 AI 相关配置。"""

    def __init__(self, window: Any) -> None:
        super().__init__()
        self.win = window
        self._dirty = False
        self._loading = False
        self._raw: dict[str, Any] | None = None
        self._saved_note = ""
        self._keyword = ""
        self._current_group = T.GROUP_ORDER[0] if T.GROUP_ORDER else ""
        self._controls: dict[str, tuple[QWidget, str]] = {}
        self._rows: dict[str, FormRow] = {}
        self._row_group: dict[str, str] = {}
        self._row_text: dict[str, str] = {}
        self._panels: dict[str, QWidget] = {}
        self._toggle_cls = _toggle_class()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        root.addLayout(self._build_head())
        root.addLayout(self._build_body(), 1)
        root.addWidget(self._build_save_bar())

        self.search.textChanged.connect(self._on_search)
        self.nav.changed.connect(self._show_group)
        if self._current_group:
            self.nav.select(self._current_group)

    # ---------------------------------------------------------------- 构建
    def _build_head(self) -> QHBoxLayout:
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        self.instance_label = C.label("", "Muted")
        head.addWidget(self.instance_label, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self.search = SearchBox("搜索配置项")
        self.search.setFixedWidth(240)
        head.addWidget(self.search, 0, Qt.AlignmentFlag.AlignVCenter)
        self._sync_instance()
        return head

    def _build_body(self) -> QHBoxLayout:
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)

        self.nav = SubNav([(group, group) for group in T.GROUP_ORDER])
        side = QWidget()
        side.setFixedWidth(168)
        side_box = QVBoxLayout(side)
        side_box.setContentsMargins(0, 0, 0, 0)
        side_box.setSpacing(16)
        side_box.addWidget(self.nav, 0, Qt.AlignmentFlag.AlignTop)
        self.page_art = None
        if IS_PERSONAL:
            self.page_art = PosterArt("ai-portrait.jpg")
            self.page_art.setFixedSize(156, 248)
            side_box.addWidget(self.page_art, 0, Qt.AlignmentFlag.AlignHCenter)
        side_box.addStretch(1)
        body.addWidget(side, 0)

        self.card = C.Card(padding=(20, 16, 20, 16), spacing=8)
        self.card.setMinimumHeight(510)
        self.stack = QStackedWidget()
        self.card.box.addWidget(self.stack, 1)
        body.addWidget(self.card, 1)

        for group in T.GROUP_ORDER:
            panel = self._build_panel(group)
            self._panels[group] = panel
            self.stack.addWidget(panel)
        return body

    def _build_panel(self, group: str) -> QWidget:
        members = [(key, spec) for key, spec in T.FIELD_UI.items() if spec[1] == group]

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(10)
        panel_layout.addWidget(C.SectionTitle(group, f"共 {len(members)} 项"))
        if group == "本地变声":
            from ..rvc_panel import RvcPanel
            self.rvc_panel = RvcPanel(self)
            panel_layout.addWidget(self.rvc_panel)
        if group == "人设与语音":
            panel_layout.addWidget(self._build_voice_presets())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(2, 4, 10, 8)
        holder_layout.setSpacing(4)
        advanced_layout = None
        if group == "本地变声":
            self.rvc_advanced_button = C.quiet_button("高级设置  ·  路径与算法", "chevron-down")
            self.rvc_advanced_button.setCheckable(True)
            self.rvc_advanced = QWidget()
            advanced_layout = QVBoxLayout(self.rvc_advanced)
            advanced_layout.setContentsMargins(0, 0, 0, 0)
            advanced_layout.setSpacing(4)
            self.rvc_advanced.hide()
            self.rvc_advanced_button.toggled.connect(self.rvc_advanced.setVisible)
        for key, (text, _group, hint) in members:
            field, control, kind = self._build_field(key, hint)
            row = FormRow(text, field, hint)
            if advanced_layout is not None and key not in {"voice_mode", "rvc_enabled", "rvc_fallback", "rvc_default_voice"}:
                advanced_layout.addWidget(row)
            else:
                holder_layout.addWidget(row)
            self._controls[key] = (control, kind)
            self._rows[key] = row
            self._row_group[key] = group
            self._row_text[key] = f"{text} {key} {hint}".lower()
        if advanced_layout is not None:
            holder_layout.addWidget(self.rvc_advanced_button)
            holder_layout.addWidget(self.rvc_advanced)
        holder_layout.addStretch(1)

        scroll.setWidget(holder)
        panel_layout.addWidget(scroll, 1)
        return panel

    #: 两套语音方案，一键填完三个字段，省得手抄模型名
    VOICE_PRESETS: dict[str, dict[str, str]] = {
        "cloud": {
            "tts_endpoint": "https://api.siliconflow.cn/v1",
            "tts_model": "FunAudioLLM/CosyVoice2-0.5B",
            "tts_voice": "FunAudioLLM/CosyVoice2-0.5B:anna",
        },
        "local": {
            "tts_endpoint": "http://127.0.0.1:9881/v1",
            "tts_model": "aemeath",
            "tts_voice": "aemeath",
        },
    }

    def _build_voice_presets(self) -> QWidget:
        """「人设与语音」面板顶部的方案切换：云端随时可用，本机音色最像但要开机。"""
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 6)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(C.label("语音方案", "Muted"), 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_voice_cloud = C.secondary_button("用云端")
        self.btn_voice_local = C.secondary_button("用本机模型")
        row.addWidget(self.btn_voice_cloud, 0)
        row.addWidget(self.btn_voice_local, 0)
        row.addStretch(1)
        outer.addLayout(row)

        self.voice_hint = C.label(
            "云端：不依赖你的电脑，随时能发。本机模型：音色最像爱弥斯，但需要笔记本开着并连上隧道。",
            "Muted",
        )
        self.voice_hint.setWordWrap(True)
        outer.addWidget(self.voice_hint)

        self.btn_voice_cloud.clicked.connect(lambda: self.apply_voice_preset("cloud"))
        self.btn_voice_local.clicked.connect(lambda: self.apply_voice_preset("local"))
        return box

    def apply_voice_preset(self, name: str) -> None:
        preset = self.VOICE_PRESETS.get(name)
        if not preset:
            return
        self._loading = True
        for key, value in preset.items():
            entry = self._controls.get(key)
            if entry is None:
                continue
            entry[0].setText(value)
        toggle = self._controls.get("voice_reply_enabled")
        if toggle is not None and toggle[1] == "bool":
            toggle[0].setChecked(True)
        self._loading = False
        self._set_dirty(True)
        self.note("已填入语音方案，点「保存更改」生效")

    def _build_field(self, key: str, hint: str) -> tuple[QWidget, QWidget, str]:
        """返回 (行内控件, 取值控件, 类型)；布尔字段的类型为 bool。"""
        choices = {
            "voice_mode": [("本机直接合成（推荐）", "local_tts"), ("云端底音 + 本机 RVC", "rvc"), ("云端语音", "cloud_tts")],
            "rvc_fallback": [("只发文字", "off"), ("使用基础 TTS 音色", "cloud")],
            "rvc_f0_method": [("RMVPE（推荐）", "rmvpe"), ("PM", "pm")],
            "rvc_device": [("自动选择", "auto"), ("CPU", "cpu")],
        }
        if key in choices:
            control = _Choice()
            for label, value in choices[key]:
                control.addItem(label, value)
            control.currentIndexChanged.connect(self._mark_dirty)
            return control, control, "text"
        if key in BOOL_KEYS:
            switch = self._toggle_cls(False)
            state_text = C.label("关闭", "Muted")
            switch.toggled.connect(
                lambda checked, target=state_text: target.setText("开启" if checked else "关闭")
            )
            switch.toggled.connect(self._mark_dirty)
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(switch)
            row.addWidget(state_text)
            row.addStretch(1)
            return holder, switch, "bool"

        if key == "api_key" or key.endswith("_api_key"):
            # 密钥统一处理：掩码输入 + 眼睛切换，留空表示不改
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            secret = QLineEdit()
            secret.setFixedHeight(34)
            secret.setEchoMode(QLineEdit.EchoMode.Password)
            secret.setPlaceholderText("粘贴你的 API Key")
            secret.setFont(C.font(13, 400, mono=True))
            secret.textChanged.connect(self._mark_dirty)
            reveal = C.icon_button("eye", "显示 / 隐藏", 15)
            reveal.setCheckable(True)
            reveal.toggled.connect(
                lambda checked: secret.setEchoMode(
                    QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
                )
            )
            reveal.toggled.connect(
                lambda checked: reveal.setIcon(
                    _icons.icon("eye-off" if checked else "eye", str(K.t("INK_2")), 15)
                )
            )
            row.addWidget(secret, 1)
            row.addWidget(reveal, 0)
            if not hasattr(self, "_secret_edits"):
                self._secret_edits = {}
            self._secret_edits[key] = secret
            if key == "api_key":
                self._secret_edit = secret
            return holder, secret, "text"

        edit = QLineEdit()
        edit.setFixedHeight(34)
        edit.setPlaceholderText("未设置")
        if _is_mono(key):
            edit.setFont(C.font(13, 400, mono=True))
        edit.textChanged.connect(self._mark_dirty)
        return edit, edit, "text"

    def _build_save_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("SaveBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(8)
        self.status_label = C.label("尚未保存", "Muted")
        layout.addWidget(self.status_label, 1)
        self.btn_voice_test = C.secondary_button("测试语音")
        self.btn_discard = C.secondary_button("放弃修改")
        self.btn_save = C.primary_button("保存更改")
        layout.addWidget(self.btn_voice_test, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.btn_discard, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.btn_save, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_voice_test.clicked.connect(self._test_voice)
        self.btn_discard.clicked.connect(self.discard)
        self.btn_save.clicked.connect(self._save)
        return bar

    def _test_voice(self) -> None:
        tester = getattr(self.win, "test_voice", None)
        if callable(tester):
            tester()
        else:
            self.note("当前版本不支持语音自检")

    # ---------------------------------------------------------------- 交互
    def _show_group(self, group: str) -> None:
        panel = self._panels.get(group)
        if panel is None:
            return
        self._current_group = group
        if self.page_art is not None:
            self.page_art.set_image("voice-portrait.jpg" if group == "本地变声" else "ai-portrait.jpg")
        self.stack.setCurrentWidget(panel)
        self._filter(self._keyword)

    def _on_search(self, keyword: str) -> None:
        self._keyword = keyword
        if keyword.strip() and hasattr(self, "rvc_advanced_button"):
            self.rvc_advanced_button.setChecked(True)
        self._filter(keyword)

    def _filter(self, keyword: str) -> None:
        keyword = (keyword or "").strip().lower()
        for key, row in self._rows.items():
            if keyword and self._row_group.get(key) != self._current_group:
                continue
            visible = not keyword or keyword in self._row_text.get(key, "")
            row.setVisible(visible)

    def _mark_dirty(self, *_args: Any) -> None:
        if self._loading:
            return
        self._set_dirty(True)

    def _set_dirty(self, value: bool) -> None:
        self._dirty = bool(value)
        if self._dirty:
            self.status_label.setText("有未保存的修改")
        elif self._saved_note:
            self.status_label.setText(self._saved_note)
        else:
            self.status_label.setText("尚未保存")

    def _save(self) -> None:
        save = getattr(self.win, "save_config", None)
        if not callable(save):
            return
        status = getattr(self.win, "_set_status", None)
        if callable(status):
            status("保存中…", "INK_3")
        save()

    # ---------------------------------------------------------------- 接口
    def enter(self) -> None:
        self._sync_instance()
        load = getattr(self.win, "load_config", None)
        if callable(load):
            load()

    def _sync_instance(self) -> None:
        instance = str(getattr(self.win, "current_instance", "") or "default")
        self.instance_label.setText(f"当前实例：{instance}")

    def apply(self, config: dict) -> None:
        self._raw = dict(config or {})
        self._loading = True
        for key, (control, kind) in self._controls.items():
            value = self._raw.get(key, config_store.DEFAULTS.get(key))
            if kind == "bool":
                control.setChecked(_truthy(value))
                continue
            spec = _SCHEMA.get(key) or {}
            if spec.get("type") == "list":
                # 历史数据可能被存成 "['123']" 这类字符串化列表，先摊平再展示
                control.setText(",".join(_flatten_ids(value)))
                continue
            control.setText("" if value is None else str(value))
        self._loading = False
        self._set_dirty(False)

    def apply_secrets(self, secrets: dict) -> None:
        """把服务器上的密钥掩码显示成占位提示，不回填明文。"""
        data = secrets or {}
        edits = getattr(self, "_secret_edits", {})
        for key, edit in edits.items():
            masked = str(data.get(key) or "")
            configured = bool(data.get(f"{key}_set"))
            label = "API Key" if key == "api_key" else key.replace("_api_key", "").upper() + " Key"
            edit.setPlaceholderText(
                f"已配置 {masked}，留空保持不变" if configured else f"粘贴你的 {label}"
            )

    def collect(self) -> dict:
        """按 schema 还原类型（bool / list / int / float / str），未知键不提交。"""
        payload: dict[str, object] = {}
        for key, (control, kind) in self._controls.items():
            spec = _SCHEMA.get(key)
            if spec is None:
                continue
            if kind == "bool":
                payload[key] = control.isChecked()
                continue
            text = control.text().strip()
            field_type = spec.get("type")
            if field_type == "list":
                ids = _flatten_ids(text)
                if ids:
                    payload[key] = ids
                continue
            if field_type in ("int", "float"):
                # 留空表示「沿用原值」，不要写 0 把 .env 里的配置冲掉
                if not text:
                    continue
                try:
                    number = float(text)
                except ValueError:
                    continue
                payload[key] = int(number) if field_type == "int" else number
                continue
            payload[key] = text
        return payload

    def retheme(self) -> None:
        self.nav.retheme()

    def note(self, text: str) -> None:
        """在保存条左侧显示一句状态（失败/未连接时用，不改变脏标记）。"""
        self.status_label.setText(text)

    def mark_saved(self, note: str = "") -> None:
        stamp = datetime.now().strftime("%H:%M")
        self._saved_note = note or f"上次保存 {stamp}"
        self._loading = True
        self._set_dirty(False)
        self._loading = False

    def discard(self) -> None:
        if self._raw is not None:
            self.apply(dict(self._raw))
            return
        self._loading = True
        for key, (control, kind) in self._controls.items():
            if kind == "bool":
                control.setChecked(False)
            else:
                control.setText("")
        self._loading = False
        self._set_dirty(False)
