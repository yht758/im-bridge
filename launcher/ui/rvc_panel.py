"""Compact controls above the RVC settings form."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit
from . import components as C


class RvcPanel(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self._error = ""
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        title = C.label("本地音色，随时连接。", "H2")
        box.addWidget(title)
        self.status = C.label("尚未连接；本地离线时只发文字", "Muted")
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        row = QHBoxLayout()
        self.connect_btn = C.primary_button("启用并连接", "link")
        self.stop_btn = C.secondary_button("断开本地语音")
        row.addWidget(self.connect_btn)
        row.addWidget(self.stop_btn)
        box.addLayout(row)
        row = QHBoxLayout()
        self.text = QLineEdit("你好，我在这里。")
        self.text.setMaxLength(100)
        self.text.setPlaceholderText("输入试听文字，最多 100 字")
        self.preview_btn = C.secondary_button("试听训练音色", "play")
        row.addWidget(self.text, 1)
        row.addWidget(self.preview_btn)
        box.addLayout(row)
        hint = C.label("电脑离线时，云端继续回复文字。连接会保存本组设置并重启当前 Bot；关闭控制台将断开语音。", "Muted")
        hint.setWordWrap(True)
        box.addWidget(hint)
        self.connect_btn.clicked.connect(lambda: page.win.start_rvc(self))
        self.stop_btn.clicked.connect(lambda: page.win.stop_rvc(self))
        self.preview_btn.clicked.connect(lambda: page.win.preview_rvc(self))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)

    def show_error(self, message):
        self._error = message
        self.status.setText(message)

    def refresh(self):
        if self._error:
            return
        session = getattr(self.page.win, "rvc_session", None)
        if session:
            self.status.setText(session.snapshot())

    def busy(self, busy):
        if busy:
            self._error = ""
        self.connect_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(not busy)
        self.preview_btn.setEnabled(not busy)
