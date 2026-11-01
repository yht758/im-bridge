"""设置页 —— 左侧二级导航，右侧「用量与计费 / 外观 / 关于」三块。

业务全部委托给主窗口（load_usage / save_pricing / _apply_theme），
本页只负责呈现、收集单价与切换主题。数据从 `apply(data)` 的
`usage` / `pricing` 取，缺什么就显示 "—"，不编数字。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import components as C
from .. import icons as _icons
from .. import tokens as K
from ..format import fmt_int, fmt_money, fmt_tokens

# common.py 内部按「同级包」导入了 components / icons / tokens，而它们实际在上一层
# ui/ 里。为了不动共用文件，这里先给本包补上同名别名，common.py 就能正常导入。
for _name, _module in (("components", C), ("icons", _icons), ("tokens", K)):
    sys.modules.setdefault("%s.%s" % (__package__, _name), _module)

from .common import KeyValue, SubNav, cell, make_table  # noqa: E402

# 主题开发依赖 launcher/ 根目录（运行时 sys.path 已包含 launcher/）
import config_store  # noqa: E402
import remote  # noqa: E402

SECTIONS: tuple[tuple[str, str], ...] = (
    ("usage", "用量与计费"),
    ("appearance", "外观"),
    ("about", "关于"),
)

DEFAULT_MODELS: tuple[str, ...] = ("deepseek-chat", "deepseek-reasoner")

ABOUT_NOTES: tuple[str, ...] = (
    "本控制台只做本地编排：配置、连接、日志与用量都经 SSH 读取，不在本地保存明文凭据。",
    "用量与花费来自服务器上的 usage 汇总；单价只存本地，用来把 token 换算成钱。",
    "主题手动切换后立即生效，不需要重启。",
)


def _day_cost(day_item: dict[str, Any], pricing: dict[str, Any], fallback: str = "deepseek-chat") -> float:
    """当天花费：多模型时按各模型分别计价再求和（与主窗口口径一致）。"""
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


def _pill_qss(active: bool) -> str:
    """胶囊按钮三态样式：选中黑底白字（深色主题下自动反转）。"""
    ink = K.t("ACCENT_INK") if active else K.t("INK_2")
    background = K.t("ACCENT") if active else K.t("SURFACE_2")
    hover = K.t("ACCENT_HOVER") if active else K.t("SURFACE_3")
    hover_ink = K.t("ACCENT_INK") if active else K.t("INK")
    return (
        "QPushButton { border: none; border-radius: 14px; padding: 0 16px;"
        " font-size: 12px; color: %s; background: %s; }"
        "QPushButton:hover { color: %s; background: %s; }"
        % (ink, background, hover_ink, hover)
    )


class SettingsPage(QWidget):
    """设置：用量与计费 / 外观 / 关于。"""

    def __init__(self, window: Any) -> None:
        super().__init__()
        self.win = window
        self.setObjectName("Page")

        self._usage: dict[str, Any] = {}
        self._pricing: dict[str, Any] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(24)

        self.nav = SubNav(SECTIONS)
        root.addWidget(self.nav, 0, Qt.AlignmentFlag.AlignTop)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_usage())
        self.stack.addWidget(self._build_appearance())
        self.stack.addWidget(self._build_about())
        root.addWidget(self.stack, 1)

        self.nav.changed.connect(self._switch)
        self.nav.select("usage")
        self._sync_theme()

    # ---------------------------------------------------------- 结构：用量
    def _build_usage(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(K.CARD_GAP)

        stats_card = C.Card(padding=(20, 16, 20, 16), spacing=12)
        head = C.SectionTitle("用量与计费", "服务器 usage 汇总")
        self.usage_status = C.label("—", "Muted")
        head.add_action(self.usage_status)
        stats_card.box.addWidget(head)

        row = QHBoxLayout()
        row.setSpacing(40)
        self.stat_today = C.Stat("今日 Token", "—", "tok")
        self.stat_cost = C.Stat("今日花费", "—", "")
        self.stat_week = C.Stat("近 7 天", "—", "tok")
        self.stat_total = C.Stat("累计", "—", "tok")
        for stat in (self.stat_today, self.stat_cost, self.stat_week, self.stat_total):
            row.addWidget(stat)
        row.addStretch(1)
        stats_card.box.addLayout(row)
        box.addWidget(stats_card)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(K.CARD_GAP)
        bottom.addWidget(self._build_recent(), 4)
        bottom.addWidget(self._build_pricing(), 2)
        box.addLayout(bottom, 1)
        return panel

    def _build_recent(self) -> QWidget:
        card = C.Card(padding=(20, 16, 20, 14), spacing=10)
        card.box.addWidget(C.SectionTitle("最近调用", "最多 20 条"))
        self.table = make_table(["时间", "模型", "输入", "输出", "估算"], widths=[104, 150, 88, 88, 96])
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        card.box.addWidget(self.table, 1)
        return card

    def _build_pricing(self) -> QWidget:
        card = C.Card(padding=(20, 16, 20, 16), spacing=10)
        card.setMinimumWidth(260)
        card.box.addWidget(C.SectionTitle("单价", "元 / 百万 token"))

        self.price_model = QComboBox()
        self.price_model.setEditable(True)
        for model in DEFAULT_MODELS:
            self.price_model.addItem(model)
        card.box.addWidget(self.price_model)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.price_input = QLineEdit()
        self.price_output = QLineEdit()
        self.price_cached = QLineEdit()
        for text, field in (("输入", self.price_input), ("输出", self.price_output), ("缓存", self.price_cached)):
            column = QVBoxLayout()
            column.setSpacing(3)
            column.addWidget(C.label(text, "Label"))
            column.addWidget(field)
            row.addLayout(column, 1)
        card.box.addLayout(row)

        self.btn_save_price = C.primary_button("保存单价", "save")
        card.box.addWidget(self.btn_save_price, 0, Qt.AlignmentFlag.AlignLeft)
        hint = C.label("只存本地，用来把 token 换算成钱。", "Muted")
        hint.setWordWrap(True)
        card.box.addWidget(hint)
        card.box.addStretch(1)

        self.btn_save_price.clicked.connect(self._save_price)
        self.price_model.currentTextChanged.connect(self._load_price_row)
        return card

    # ---------------------------------------------------------- 结构：外观
    def _build_appearance(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(K.CARD_GAP)

        card = C.Card(padding=(20, 16, 20, 16), spacing=12)
        card.box.addWidget(C.SectionTitle("外观", "随时切换，立即生效"))

        row = QHBoxLayout()
        row.setSpacing(6)
        self.theme_buttons: dict[str, QPushButton] = {}
        for key, icon_name in (("light", "sun"), ("dark", "moon")):
            button = QPushButton(K.THEME_NAMES.get(key, key))
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(30)
            button.setIcon(_icons.icon(icon_name, str(K.t("INK_2")), 14))
            button.clicked.connect(lambda _checked=False, k=key: self._pick_theme(k))
            self.theme_buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)
        card.box.addLayout(row)

        self.theme_note = C.label("当前主题：—", "Muted")
        card.box.addWidget(self.theme_note)
        card.box.addStretch(1)

        box.addWidget(card)
        box.addStretch(1)
        return panel

    # ---------------------------------------------------------- 结构：关于
    def _build_about(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(K.CARD_GAP)

        card = C.Card(padding=(20, 16, 20, 16), spacing=10)
        card.box.addWidget(C.SectionTitle("关于"))
        card.box.addWidget(KeyValue("版本号", str(config_store.APP_VERSION), mono=True))
        card.box.addWidget(KeyValue("项目路径", str(config_store.ROOT), mono=True))
        card.box.addWidget(C.hline())
        for note in ABOUT_NOTES:
            line = C.label(note, "Muted")
            line.setWordWrap(True)
            card.box.addWidget(line)
        card.box.addStretch(1)
        box.addWidget(card, 1)
        return panel

    # ---------------------------------------------------------- 交互
    def _switch(self, key: str) -> None:
        index = {name: position for position, (name, _text) in enumerate(SECTIONS)}.get(key, 0)
        self.stack.setCurrentIndex(index)
        if key == "usage":
            self._load_price_row(self.price_model.currentText())

    def _toast(self, text: str, kind: str = "ok") -> None:
        toast = getattr(self.win, "toast", None)
        if callable(toast):
            toast(text, kind)

    def _pick_theme(self, theme: str) -> None:
        apply_theme = getattr(self.win, "_apply_theme", None)
        if callable(apply_theme):
            apply_theme(theme)
        else:
            K.set_theme(theme)
        if hasattr(self.win, "_theme"):
            self.win._theme = theme
        self.retheme()

    def retheme(self) -> None:
        """主题切换后按新令牌重刷本页局部样式与已渲染的数字。"""
        nav = getattr(self, "nav", None)
        if hasattr(nav, "retheme"):
            nav.retheme()
        self._sync_theme()
        self._render_usage()

    def _sync_theme(self) -> None:
        current = str(getattr(self.win, "_theme", "") or K.current_theme())
        self.theme_note.setText("当前主题：%s" % K.THEME_NAMES.get(current, current))
        for key, button in self.theme_buttons.items():
            active = key == current
            button.setChecked(active)
            button.setStyleSheet(_pill_qss(active))
            color = K.t("ACCENT_INK") if active else K.t("INK_2")
            icon_name = "sun" if key == "light" else "moon"
            button.setIcon(_icons.icon(icon_name, str(color), 14))

    # ---------------------------------------------------------- 单价
    def _load_price_row(self, model: str) -> None:
        pricing = self._pricing or getattr(self.win, "pricing", None) or remote.load_pricing()
        price = remote.price_of(pricing, str(model or "").strip())
        self.price_input.setText(str(price.get("input", 0)))
        self.price_output.setText(str(price.get("output", 0)))
        self.price_cached.setText(str(price.get("cached", 0)))

    def _save_price(self) -> None:
        model = self.price_model.currentText().strip()
        if not model:
            self._toast("请先选择或输入模型名", "err")
            return
        try:
            payload = {
                "input": float(self.price_input.text() or 0),
                "output": float(self.price_output.text() or 0),
                "cached": float(self.price_cached.text() or 0),
            }
        except ValueError:
            self._toast("单价必须是数字", "err")
            return
        self._pricing[model] = payload
        saver = getattr(self.win, "save_pricing", None)
        if callable(saver):
            saver(model, payload)
        else:
            self._toast("保存入口不可用，单价未写入", "err")
            return
        self._toast("单价已保存", "ok")

    # ---------------------------------------------------------- 数据
    def enter(self) -> None:
        self._sync_theme()
        self._load_price_row(self.price_model.currentText())
        if getattr(self.win, "connected", False):
            loader = getattr(self.win, "load_usage", None)
            if callable(loader):
                loader()

    def apply(self, data: dict[str, Any]) -> None:
        payload = data or {}
        usage = payload.get("usage") or {}
        pricing = payload.get("pricing") or {}
        self._usage = dict(usage) if isinstance(usage, dict) else {}
        self._pricing = dict(pricing) if isinstance(pricing, dict) else {}
        if self._pricing and hasattr(self.win, "pricing"):
            self.win.pricing = self._pricing
        self._load_price_row(self.price_model.currentText())
        self._render_usage()

    def _sync_models(self, names: list[str]) -> None:
        """把 usage 里出现的模型补进下拉，不打断用户当前输入。"""
        existing = [self.price_model.itemText(i) for i in range(self.price_model.count())]
        current = self.price_model.currentText()
        self.price_model.blockSignals(True)
        for name in names:
            if name and name not in existing:
                self.price_model.addItem(name)
        self.price_model.setCurrentText(current)
        self.price_model.blockSignals(False)

    def _render_usage(self) -> None:
        usage = self._usage
        pricing = self._pricing
        stats = (self.stat_today, self.stat_cost, self.stat_week, self.stat_total)

        if not usage.get("available"):
            self.usage_status.setText(str(usage.get("reason") or "暂无用量数据"))
            for stat in stats:
                stat.set("—", "", "")
            self.table.setRowCount(0)
            return

        updated = str(usage.get("updated_at") or "")[:19].replace("T", " ")
        path = str(usage.get("path") or "")
        self.usage_status.setText(("%s · 更新于 %s" % (path, updated or "未知")).strip(" ·"))

        days = usage.get("days") or {}
        total = usage.get("total") or {}
        models = usage.get("models") or {}
        self._sync_models([str(name) for name in models.keys()])
        fallback = str(next(iter(models.keys()), "deepseek-chat"))

        today_item = days.get(date.today().isoformat()) or {}
        today_tokens = float(today_item.get("prompt", 0)) + float(today_item.get("completion", 0))
        self.stat_today.set(
            fmt_tokens(today_tokens),
            "tok",
            "%s 次" % fmt_int(today_item.get("calls", 0)),
        )
        if pricing:
            self.stat_cost.set(fmt_money(_day_cost(today_item, pricing, fallback)), "", "")
        else:
            self.stat_cost.set("—", "", "未设置单价")

        week = 0.0
        for offset in range(7):
            item = days.get((date.today() - timedelta(days=offset)).isoformat()) or {}
            week += float(item.get("prompt", 0)) + float(item.get("completion", 0))
        self.stat_week.set(fmt_tokens(week), "tok", "")

        total_tokens = float(total.get("prompt", 0)) + float(total.get("completion", 0))
        self.stat_total.set(fmt_tokens(total_tokens), "tok", "缓存 %s" % fmt_tokens(total.get("cached", 0)))

        self._render_recent(list(usage.get("recent") or [])[:20], pricing)

    def _render_recent(self, recent: list[dict[str, Any]], pricing: dict[str, Any]) -> None:
        self.table.setRowCount(len(recent))
        for row, item in enumerate(recent):
            stamp = item.get("ts") or 0
            when = datetime.fromtimestamp(float(stamp)).strftime("%m-%d %H:%M") if stamp else "—"
            model = str(item.get("model") or "—")
            prompt = float(item.get("prompt", 0))
            completion = float(item.get("completion", 0))
            if pricing:
                cost = fmt_money(
                    remote.cost_of(pricing, model, prompt, completion, float(item.get("cached", 0)))
                )
            else:
                cost = "—"

            cells = (
                cell(when, mono=True),
                cell(model, mono=True),
                cell(fmt_int(prompt), mono=True, align_right=True),
                cell(fmt_int(completion), mono=True, align_right=True),
                cell(cost, mono=True, align_right=True),
            )
            cells[4].setForeground(K.qcolor(K.t("ACCENT")))
            cells[0].setForeground(K.qcolor(K.t("INK_2")))
            cells[1].setForeground(K.qcolor(K.t("INK_2")))
            cells[2].setForeground(K.qcolor(K.t("INK")))
            cells[3].setForeground(K.qcolor(K.t("INK")))
            for column, widget in enumerate(cells):
                self.table.setItem(row, column, widget)
