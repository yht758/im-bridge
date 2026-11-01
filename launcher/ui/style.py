"""QSS —— 由 tokens 生成，两套主题共用同一份模板。"""
from __future__ import annotations

from string import Template

from . import tokens as K

_TEMPLATE = Template(
    """
* { outline: none; }

QWidget {
    color: $INK;
    font-family: $FONT_UI;
    font-size: 13px;
}
QWidget#Root, QMainWindow, QDialog { background: $APP_BG; }
QToolTip {
    background: $ACCENT; color: $ACCENT_INK;
    border: none; border-radius: 8px; padding: 6px 9px; font-size: 11.5px;
}

/* ---------------------------------------------------------- 侧栏 */
QFrame#Sidebar { background: $SIDEBAR; border: none; }
QFrame#Sidebar QLabel { color: $SIDEBAR_INK; background: transparent; }
QLabel#BrandName { color: $SIDEBAR_INK; font-size: 17px; font-weight: 600; letter-spacing: 0.2px; }
QLabel#BrandTag { color: $SIDEBAR_INK_2; font-size: 10.5px; letter-spacing: 0.4px; }
QFrame#Sidebar QLabel#NavGroup {
    color: $SIDEBAR_INK_3; font-size: 10px; font-weight: 600; letter-spacing: 1.2px;
    padding-left: 10px;
}
QFrame#Sidebar QLabel#Body { color: $SIDEBAR_INK; }
QFrame#Sidebar QLabel#Muted, QFrame#Sidebar QLabel#Mono { color: $SIDEBAR_INK_2; }
QFrame#Sidebar QPushButton#Nav {
    text-align: left; padding: 0 14px; border: 1px solid transparent;
    border-radius: 21px; margin: 2px 0;
    color: $SIDEBAR_INK_2; font-size: 13px; background: transparent;
}
QFrame#Sidebar QPushButton#Nav:hover { background: $SIDEBAR_HOVER; color: $SIDEBAR_INK; border-color: transparent; }
QFrame#Sidebar QPushButton#Nav[active="true"] {
    background: $SIDEBAR_ACTIVE; color: $SIDEBAR_INK; font-weight: 600;
}
QFrame#Sidebar QFrame#HLine { background: $SIDEBAR_LINE; }

/* ---------------------------------------------------------- 卡片 */
QFrame#Card, QFrame#Block, QFrame#BlockAlt, QFrame#StatusCard, QFrame#SaveBar {
    background: $SURFACE; border: 1px solid $LINE; border-radius: 18px;
}
QFrame#WorkspaceHeader { background: $SURFACE; border: 1px solid $LINE; border-radius: 22px; }
QFrame#BlockAlt { background: $SURFACE_2; }
QFrame#Inset { background: $SURFACE_2; border: 1px solid $LINE; border-radius: 14px; }
QFrame#InsetFlat { background: $SURFACE_2; border: none; border-radius: 14px; }
QFrame#HLine { background: $LINE; border: none; }
QFrame#HeroCard { background: $POSTER_BG; border: 1px solid $POSTER_LINE; border-radius: 22px; }
QFrame#BrandCard { background: $POSTER_BG; border: 1px solid $POSTER_LINE; border-radius: 18px; }
QFrame#Topbar { background: $APP_BG; border: none; }

/* ---------------------------------------------------------- 文字 */
QLabel { background: transparent; }
QLabel#Display { font-size: 30px; font-weight: 600; color: $HERO_INK; letter-spacing: 0.2px; }
QLabel#HeroTitle { font-size: 28px; font-weight: 600; color: $HERO_INK; }
QLabel#HeroDetail { font-size: 12px; color: $HERO_INK_2; }
QLabel#PosterSign { font-size: 18px; font-weight: 600; color: $HERO_INK; }
QLabel#HomeTitle { font-size: ${HOME_TITLE_SIZE}px; font-weight: 600; color: $INK; }
QLabel#H1, QLabel#PageTitle { font-size: 20px; font-weight: 600; color: $INK; }
QLabel#CardTitle, QLabel#SectionTitle { font-size: 15px; font-weight: 600; color: $INK; }
QLabel#H2 { font-size: 15px; font-weight: 600; color: $INK; }
QLabel#H3 { font-size: 13px; font-weight: 600; color: $INK; }
QLabel#Body { font-size: 13px; color: $INK; }
QLabel#Value { font-size: 13px; font-weight: 600; color: $INK; }
QLabel#PageDesc, QLabel#Caption, QLabel#Muted { font-size: 12px; color: $INK_2; }
QLabel#Label, QLabel#StatLabel, QLabel#KVKey { font-size: 12px; color: $INK_2; }
QLabel#Hint { font-size: 12px; color: $INK_3; }
QLabel#Mono { font-family: $FONT_MONO; font-size: 12px; color: $INK_2; }
QLabel#MonoInk { font-family: $FONT_MONO; font-size: 12px; color: $INK; }
QLabel#StatValue { font-size: 24px; font-weight: 600; color: $INK; }
QLabel#StatSub { font-size: 11.5px; color: $INK_3; }
QLabel#Eyebrow { font-size: 11px; font-weight: 600; letter-spacing: 1.2px; color: $INK_3; }
QLabel#Gold { color: $INK; }
QLabel#BrandSign {
    color: $INK; font-family: "Segoe Script", "Segoe UI", "Microsoft YaHei UI";
    font-size: 21px; font-style: italic;
}

/* 旧页面遗留的语义别名 */
QLabel#StateOk { color: $OK; font-size: 12px; }
QLabel#StateIdle { color: $INK_2; font-size: 12px; }
QLabel#StateOff { color: $INK_3; font-size: 12px; }
QLabel#StateErr { color: $ERR; font-size: 12px; }

/* ---------------------------------------------------------- 徽章 */
QLabel#BadgeOk, QLabel#BadgeOff, QLabel#BadgeWarn, QLabel#BadgeErr, QLabel#BadgeAccent, QLabel#Badge {
    border-radius: 10px; padding: 2px 9px; font-size: 11.5px; font-weight: 500;
}
QLabel#BadgeOk { background: $OK_SOFT; color: $OK; }
QLabel#BadgeOff { background: $SURFACE_3; color: $INK_2; }
QLabel#BadgeWarn { background: $SURFACE_3; color: $WARN; }
QLabel#BadgeErr { background: $ERR_SOFT; color: $ERR; }
QLabel#BadgeAccent, QLabel#Badge { background: $ACCENT; color: $ACCENT_INK; }

/* ---------------------------------------------------------- 按钮 */
QPushButton {
    font-family: $FONT_UI; font-size: 13px; border: none; border-radius: 10px;
    padding: 0 16px; min-height: 34px; color: $INK; background: transparent;
}
QPushButton#Primary {
    background: $ACCENT; color: $ACCENT_INK; font-weight: 500; min-height: 38px;
}
QPushButton#Primary:hover { background: $ACCENT_HOVER; }
QPushButton#Primary:pressed { background: $ACCENT; }
QPushButton#Primary:disabled {
    background: $SURFACE_3; color: $INK_3; border: 1px solid $LINE;
}
QPushButton#Secondary {
    background: transparent; border: 1px solid $LINE_2; color: $INK; min-height: 38px;
}
QPushButton#Secondary:hover { background: $SURFACE_2; border-color: $INK_3; }
QPushButton#Secondary:pressed { background: $SURFACE_3; }
QPushButton#Ghost {
    background: $SURFACE_2; color: $INK; min-height: 34px;
}
QPushButton#Ghost:hover { background: $SURFACE_3; }
QPushButton#Quiet {
    background: transparent; color: $INK_2; padding: 0 8px; min-height: 28px; font-size: 12.5px;
}
QPushButton#Quiet:hover { color: $INK; background: $SURFACE_2; }
QPushButton#Danger { background: transparent; color: $ERR; }
QPushButton#Danger:hover { background: $ERR_SOFT; }
QPushButton#IconBtn {
    background: transparent; border-radius: 9px; padding: 0; min-height: 0;
}
QPushButton#IconBtn:hover { background: $SURFACE_2; }
QPushButton#IconBtn:pressed { background: $SURFACE_3; }
QPushButton:focus { border: 1px solid $INK_3; }
QPushButton#Primary:focus { border: 1px solid $INK_2; }
QPushButton#Quiet:focus, QPushButton#IconBtn:focus { border: 1px solid $LINE_2; }
QFrame#Sidebar QPushButton#Nav:focus { border: 1px solid rgba(255, 255, 255, 0.28); background: $SIDEBAR_HOVER; }
QPushButton:disabled { color: $INK_3; }
QPushButton#Ghost:disabled, QPushButton#Quiet:disabled, QPushButton#Secondary:disabled {
    background: transparent; border-color: $LINE; color: $INK_3;
}

/* ---------------------------------------------------------- 表单 */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
    background: $SURFACE_2; border: 1px solid transparent; border-radius: 10px;
    padding: 0 10px; min-height: 34px; color: $INK; selection-background-color: $INK_3;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { background: $SURFACE_3; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QTextEdit:focus, QPlainTextEdit:focus {
    background: $SURFACE; border: 1px solid $INK_3;
}
QLineEdit:disabled, QComboBox:disabled { color: $INK_3; }
QLineEdit::placeholder { color: $INK_3; }
QTextEdit, QPlainTextEdit { padding: 8px 10px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QAbstractItemView {
    background: $SURFACE; border: 1px solid $LINE; border-radius: 10px;
    padding: 4px; outline: none; selection-background-color: $SURFACE_3;
    selection-color: $INK;
}
QCheckBox { spacing: 8px; color: $INK; font-size: 13px; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid $LINE_2; background: $SURFACE;
}
QCheckBox::indicator:checked { background: $ACCENT; border-color: $ACCENT; }

/* ---------------------------------------------------------- 滚动 */
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: $LINE_2; border-radius: 4px; min-height: 36px;
}
QScrollBar::handle:vertical:hover { background: $INK_3; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $LINE_2; border-radius: 4px; min-width: 36px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ---------------------------------------------------------- 表格 */
QTableWidget, QTableView {
    background: $SURFACE; border: none; gridline-color: transparent;
    selection-background-color: $SURFACE_2; selection-color: $INK;
    alternate-background-color: $SURFACE;
}
QTableWidget::item, QTableView::item { border-bottom: 1px solid $LINE; padding: 0 10px; }
QTableWidget::item:selected, QTableView::item:selected { background: $SURFACE_2; color: $INK; }
QHeaderView { background: transparent; }
QHeaderView::section {
    background: transparent; color: $INK_3; border: none; border-bottom: 1px solid $LINE;
    padding: 8px 10px; font-size: 11.5px; font-weight: 500;
}
QTableCornerButton::section { background: transparent; border: none; }

/* ---------------------------------------------------------- 分组 / 其他 */
QFrame#SaveBar { border-radius: 16px; }
QMenu {
    background: $SURFACE; border: 1px solid $LINE; border-radius: 12px; padding: 6px;
}
QMenu::item { padding: 7px 18px; border-radius: 8px; }
QMenu::item:selected { background: $SURFACE_2; }
"""
)


def build() -> str:
    values = {key: str(value) for key, value in K.THEMES[K.current_theme()].items()}
    values["HOME_TITLE_SIZE"] = str(K.HOME_TITLE_SIZE)
    values["FONT_UI"] = K.FONT_UI
    values["FONT_MONO"] = K.FONT_MONO
    return _TEMPLATE.safe_substitute(values)
