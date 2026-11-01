"""首页试点：紧凑页头、状态网格、运行操作与近期活动。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import components as C
from . import icons, motion
from . import tokens as K
from .poster_art import PosterArt
from .edition import IS_PERSONAL
from .format import fmt_money, fmt_tokens, fmt_uptime, mask_host

ASSETS = Path(__file__).resolve().parent.parent / "assets"
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _greeting() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 11:
        return "早上好"
    if 11 <= hour < 13:
        return "中午好"
    if 13 <= hour < 18:
        return "下午好"
    return "晚上好"


class HeroCard(QFrame):
    """Compact workspace heading; artwork has a single, bounded home."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HeroCard" if IS_PERSONAL else "WorkspaceHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(K.HOME_HEADER_H)
        self.art = PosterArt("home-portrait.jpg", parent=self, crop=(0, .17, 1, .67)) if IS_PERSONAL else None
        row = QHBoxLayout(self)
        row.setContentsMargins(26, 22, 24, 22)
        row.setSpacing(K.HOME_GAP)
        text = QVBoxLayout()
        text.setSpacing(8)
        text.addWidget(C.label("WORKSPACE  /  工作空间", "HeroDetail" if IS_PERSONAL else "Muted"))
        self.greeting = C.label("", "HeroTitle" if IS_PERSONAL else "HomeTitle")
        text.addWidget(self.greeting)
        self.hero_status = C.label("连接服务器后，在这里查看运行状态。", "HeroDetail" if IS_PERSONAL else "Muted")
        self.hero_status.setWordWrap(True)
        text.addWidget(self.hero_status)
        row.addLayout(text, 1)
        self.avatar = C.Avatar(str(ASSETS / "app_256.png"), 36)
        detail = QVBoxLayout()
        detail.setSpacing(8)
        detail.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignRight)
        self.today = C.label("", "HeroDetail" if IS_PERSONAL else "Muted")
        self.instance_chip = C.Badge("实例 default", "off")
        detail.addWidget(self.today, 0, Qt.AlignmentFlag.AlignRight)
        detail.addWidget(self.instance_chip, 0, Qt.AlignmentFlag.AlignRight)
        detail.addStretch(1)
        row.addLayout(detail)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(60000)
        self._tick()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.art is not None:
            self.art.setGeometry(int(self.width() * .51), 1, int(self.width() * .34), self.height() - 2)
            self.art.lower()

    def _tick(self):
        now = datetime.now()
        self.greeting.setText(f"{_greeting()}，欢迎回来。")
        self.today.setText(f"{now.month}月{now.day}日 · {WEEKDAYS[now.weekday()]}")


class HomePage(QWidget):
    def __init__(self, window: Any) -> None:
        super().__init__()
        self.win = window
        self._data: dict | None = None
        self.setObjectName("Page")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(K.HOME_GAP)

        self.hero = HeroCard()
        root.addWidget(self.hero)

        # ---- 四状态卡 ----
        cards = QGridLayout()
        self._status_grid = cards
        self._status_columns = 0
        cards.setSpacing(K.HOME_GAP)
        self.card_server = C.StatusCard("云服务器", "server")
        self.card_ai = C.StatusCard("AI 配置", "sparkles")
        self.card_napcat = C.StatusCard("NapCat", "plug")
        self.card_bot = C.StatusCard("QQ Bot", "message-circle")
        self._cards = [self.card_server, self.card_ai, self.card_napcat, self.card_bot]
        for card in self._cards:
            card._shadow.setEnabled(False)
        self._layout_status(4)
        root.addLayout(cards)

        # ---- 概览 / 日志 / 品牌 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(K.HOME_GAP)
        bottom.addWidget(self._build_server_card(), 11)
        bottom.addWidget(self._build_log_card(), 11)
        self.brand = self._build_brand_card() if IS_PERSONAL else None
        if self.brand is not None:
            bottom.addWidget(self.brand, 5)
        root.addLayout(bottom, 1)

        # ---- 快捷操作 ----
        footer = QHBoxLayout()
        self.token_note = C.label("今日用量 —", "Mono")
        self.updated = C.label("等待同步", "Hint")
        footer.addWidget(self.token_note)
        footer.addStretch(1)
        footer.addWidget(self.updated)
        self.btn_refresh = C.icon_button("refresh-cw", "刷新数据", 16)
        self.btn_refresh.clicked.connect(lambda: self.win.refresh_overview(force=True))
        footer.addWidget(self.btn_refresh)
        root.addLayout(footer)
        self.apply({})

        self.card_server.clicked.connect(lambda: self.win.switch_page("connection"))
        self.card_ai.clicked.connect(lambda: self.win.switch_page("config"))
        self.card_napcat.clicked.connect(lambda: self.win.switch_page("napcat"))
        self.card_bot.clicked.connect(lambda: self.win.switch_page("instances"))
        self.btn_add_server.clicked.connect(lambda: self.win.switch_page("connection"))

    # ---------------------------------------------------------- 结构
    def _build_brand_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("BrandCard")
        card.setMinimumWidth(150)
        box = QGridLayout(card)
        box.setContentsMargins(0, 0, 0, 0)
        art = PosterArt("home-postcard.jpg")
        box.addWidget(art, 0, 0)
        text = QWidget()
        layout = QVBoxLayout(text)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.addWidget(C.label("im-bridge", "PosterSign"))
        layout.addStretch(1)
        caption = C.label("少一点重复操作，\n多一点创造时间。", "HeroDetail")
        layout.addWidget(caption)
        text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        box.addWidget(text, 0, 0)
        return card

    def _build_server_card(self) -> QWidget:
        card = C.Card(padding=(20, 18, 20, 18), spacing=14)
        head = C.SectionTitle("运行概览")
        self.server_badge = C.Badge("未连接", "off")
        head.add_action(self.server_badge)
        card.box.addWidget(head)

        inset = QFrame()
        inset.setObjectName("Inset")
        inset.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inset_box = QHBoxLayout(inset)
        inset_box.setContentsMargins(12, 10, 12, 10)
        inset_box.setSpacing(10)
        inset_box.addWidget(C.IconTile("server", 34), 0, Qt.AlignmentFlag.AlignVCenter)
        name_box = QVBoxLayout()
        name_box.setSpacing(1)
        self.host_name = C.ElidedLabel("云服务器", "H3")
        self.host_addr = C.ElidedLabel("—", "Muted")
        name_box.addWidget(self.host_name)
        name_box.addWidget(self.host_addr)
        inset_box.addLayout(name_box, 1)
        self.host_badge = C.Badge("未连接", "off")
        inset_box.addWidget(self.host_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        card.box.addWidget(inset)

        card.box.addWidget(C.label("当前 Bot", "H3"))
        self.server_meta = C.ElidedLabel("—", "Muted")
        card.box.addWidget(self.server_meta)
        card.box.addLayout(self._build_actions())
        divider = QFrame()
        divider.setObjectName("HLine")
        divider.setFixedHeight(1)
        divider.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.box.addWidget(divider)

        metrics = QHBoxLayout()
        metrics.setSpacing(18)
        self.m_cpu = C.MetricCell("CPU")
        self.m_mem = C.MetricCell("内存")
        self.m_disk = C.MetricCell("磁盘")
        for metric in (self.m_cpu, self.m_mem, self.m_disk):
            metrics.addWidget(metric, 1)
        card.box.addLayout(metrics)

        trend_head = QHBoxLayout()
        trend_head.setSpacing(6)
        trend_head.addWidget(C.label("近 7 天 token", "Hint"))
        trend_head.addStretch(1)
        self.week_note = C.label("", "Hint")
        trend_head.addWidget(self.week_note)
        card.box.addLayout(trend_head)
        self.spark = C.Sparkline(height=64)
        self.spark.setFixedHeight(52)
        card.box.addWidget(self.spark)
        card.box.addStretch(1)


        self.btn_add_server = C.quiet_button("管理服务器", "arrow-right")
        self.btn_add_server.setMinimumHeight(38)
        head.add_action(self.btn_add_server)
        return card

    def _build_log_card(self) -> QWidget:
        card = C.Card(padding=(20, 18, 20, 14), spacing=8)
        head = C.SectionTitle("最近活动")
        more = C.quiet_button("查看更多", "arrow-right")
        more.clicked.connect(lambda: self.win.switch_page("logs"))
        head.add_action(more)
        card.box.addWidget(head)
        self.logs = C.LogList(limit=6)
        card.box.addWidget(self.logs, 1)
        card.box.addWidget(C.label("最近的运行记录 · 查看全部可打开日志页", "Hint"))
        return card

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_start = C.primary_button("启动", "play")
        self.btn_stop = C.secondary_button("停止", "square")
        self.btn_restart = C.secondary_button("重启", "rotate-cw")
        self.btn_logs = C.icon_button("file-text", "查看日志", 16)
        for button in (self.btn_start, self.btn_stop, self.btn_restart):
            button.setMinimumHeight(34)
            button.setFixedWidth(68)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_restart)
        row.addWidget(self.btn_logs)
        row.addStretch(1)

        self.btn_start.clicked.connect(lambda: self.win.lifecycle("start"))
        self.btn_stop.clicked.connect(lambda: self.win.lifecycle("stop"))
        self.btn_restart.clicked.connect(lambda: self.win.lifecycle("restart"))
        self.btn_logs.clicked.connect(lambda: self.win.switch_page("logs"))
        return row

    # ---------------------------------------------------------- 数据
    def enter(self) -> None:
        self.hero._tick()

    def apply(self, data: dict[str, Any]) -> None:
        self._data = data
        connected = bool(getattr(self.win, "connected", False))
        status = data.get("status") or {}
        running = bool(status.get("running"))
        hostinfo = data.get("hostinfo") or {}
        napcat = data.get("napcat") or {}
        config = data.get("config") or {}
        metrics = data.get("metrics") or {}
        stats = data.get("stats") or {}
        instances = getattr(self.win, "instances", []) or []

        self.hero.instance_chip.setText(f"当前实例：{getattr(self.win, 'current_instance', 'default')}")

        host_name = str(hostinfo.get("alias") or hostinfo.get("name") or hostinfo.get("host") or "")
        self.host_name.setText(host_name or "云服务器")
        addr = mask_host(hostinfo.get("host"))
        port = hostinfo.get("port") or "22"
        self.host_addr.setText(f"{addr}:{port}" if addr != "—" else "尚未配置主机")

        # ---- 四状态卡 ----
        if connected:
            count = len(instances)
            self.card_server.set_state("ok", "已连接", f"{count} 个实例" if count else "主机已连接")
            self.server_badge.set_text("已连接")
            self.server_badge.set_tone("ok")
            self.host_badge.set_text("已连接")
            self.host_badge.set_tone("ok")
        else:
            self.card_server.set_state("idle", "未连接", "添加主机并验证 SSH 连接")
            self.server_badge.set_text("未连接")
            self.server_badge.set_tone("off")
            self.host_badge.set_text("未连接")
            self.host_badge.set_tone("off")

        model = str(config.get("model") or "")
        if model:
            self.card_ai.set_state("ok", "已配置", f"模型 {model}")
        else:
            self.card_ai.set_state("idle", "未配置" if connected else "未连接", "—")

        if napcat.get("running"):
            version = str(napcat.get("version") or "").strip()
            self.card_napcat.set_state("ok", "运行中", f"版本 {version}" if version else "容器运行中")
        elif napcat and not napcat.get("ok", True):
            self.card_napcat.set_state("err", "离线", str(napcat.get("detail") or "")[:24])
        elif napcat:
            self.card_napcat.set_state("idle", "已停止", str(napcat.get("detail") or "容器未运行")[:24])
        else:
            self.card_napcat.set_state("idle", "待检测" if connected else "未连接", "需在服务器预先安装")

        if running:
            pid = status.get("pid")
            info = fmt_uptime(status.get("uptime"))
            if pid:
                info = f"{info} · PID {pid}"
            self.card_bot.set_state("ok", "运行中", info)
            self.hero.hero_status.setText(
                f"bot 运行中 · 已运行 {fmt_uptime(status.get('uptime'))} · {len(instances)} 个实例"
            )
        elif connected:
            self.card_bot.set_state("idle", "已停止", "服务当前未启动")
            self.hero.hero_status.setText("bot 已停止 · 可在下方一键启动")
        else:
            self.card_bot.set_state("idle", "未连接", "—")
            self.hero.hero_status.setText("尚未连接服务器 · 先去「云服务器」页完成连接")

        # ---- 服务器概览指标 ----
        if metrics.get("ok"):
            self.m_cpu.set_value(metrics.get("cpu_pct"))
            self.m_mem.set_value(metrics.get("mem_pct"))
            self.m_disk.set_value(metrics.get("disk_pct"))
        else:
            for metric in (self.m_cpu, self.m_mem, self.m_disk):
                metric.set_value(None)

        instance_id = getattr(self.win, "current_instance", "default")
        service = "im-bridge.service" if instance_id == "default" else f"im-bridge@{instance_id}.service"
        if running:
            pid = status.get("pid")
            meta = f"运行 {fmt_uptime(status.get('uptime'))}"
            if pid:
                meta += f" · PID {pid}"
            self.server_meta.setText(f"{meta} · {service}")
        elif connected:
            self.server_meta.setText(f"{service} 当前未启动")
        else:
            self.server_meta.setText("连接服务器后显示运行时长与进程")

        self._apply_usage(data, stats)
        self.logs.set_lines(data.get("logs") or [])

        # ---- 按钮矩阵 ----
        self.btn_start.setEnabled(connected and not running)
        self.btn_stop.setEnabled(connected and running)
        self.btn_restart.setEnabled(connected and running)
        self.btn_logs.setEnabled(connected)
        self.updated.setText(f"更新于 {datetime.now().strftime('%H:%M')}" if data else "等待连接")

    def _apply_usage(self, data: dict[str, Any], stats: dict[str, Any]) -> None:
        usage = data.get("usage") or {}
        days = usage.get("days") or {}
        series: list[float] = []
        for offset in range(6, -1, -1):
            day = (date.today() - timedelta(days=offset)).isoformat()
            item = days.get(day) or {}
            series.append(float(item.get("prompt", 0)) + float(item.get("completion", 0)))
        self.spark.set_data(series)
        total = sum(series)
        self.week_note.setText(f"合计 {fmt_tokens(total)} tok" if total else "暂无数据")

        today_tokens = stats.get("today_tokens")
        today_cost = stats.get("today_cost")
        if today_tokens is None:
            self.token_note.setText("今日 —")
            return
        self.token_note.setText(f"今日 {fmt_tokens(today_tokens)} tok · {fmt_money(today_cost)}")

    def _layout_status(self, columns):
        if self._status_columns == columns:
            return
        self._status_columns = columns
        for card in self._cards:
            self._status_grid.removeWidget(card)
        for col in range(4):
            self._status_grid.setColumnStretch(col, 1 if col < columns else 0)
        for i, card in enumerate(self._cards):
            self._status_grid.addWidget(card, i // columns, i % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_status(2 if self.width() < 760 else 4)
        if getattr(self, "brand", None) is not None:
            self.brand.setVisible(self.width() >= 920)

    def retheme(self) -> None:
        if self._data:
            self.apply(self._data)

    def play_intro(self) -> None:
        motion.stagger([self.hero, *self._cards])
