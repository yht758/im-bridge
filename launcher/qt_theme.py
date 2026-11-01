"""主题兼容层 —— 对外保留 v7/v8 的 API，内部转发到 `ui.tokens` / `ui.style` / `ui.icons`。

新代码请直接用 `ui.tokens`、`ui.style`、`ui.icons`；本模块只服务于尚未迁移的旧页面。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QEasingCurve
from PySide6.QtGui import QColor, QIcon, QPalette, QPixmap

from ui import icons as _icons
from ui import style as _style
from ui import tokens as _tokens

ROOT = Path(__file__).resolve().parent.parent
THEME_FILE = ROOT / "data" / "desktop_theme.json"

FONT_UI = _tokens.FONT_UI
FONT_MONO = _tokens.FONT_MONO

RADIUS_SM = _tokens.RADIUS_SM
RADIUS_MD = _tokens.RADIUS_MD
RADIUS_LG = _tokens.RADIUS_LG
RADIUS_XL = _tokens.RADIUS_XL
SIDEBAR_W = _tokens.SIDEBAR_W
PAGE_PAD = _tokens.PAGE_PAD
TOPBAR_H = 56
CONTENT_MAX = _tokens.CONTENT_MAX

DURATION_PAGE = _tokens.DUR_UI
DURATION_HOVER = _tokens.DUR_MICRO
DURATION_NUM = 420
DURATION_REVEAL = 380
EASE_OUT = (0.22, 1.0, 0.36, 1.0)

# 旧键 → 新键
_ALIAS: dict[str, str] = {
    "BG": "APP_BG",
    "PANEL": "SURFACE",
    "PANEL_2": "SURFACE_2",
    "PANEL_3": "SURFACE_3",
    "BORDER": "LINE",
    "BORDER_2": "LINE_2",
    "BORDER_HOVER": "LINE_2",
    "SIDEBAR_BG": "SIDEBAR",
    "SIDEBAR_BORDER": "SIDEBAR_LINE",
    "SIDEBAR_ACTIVE_BG": "SIDEBAR_ACTIVE",
    "OK_SOFT_BG": "OK_SOFT",
    "ERR_SOFT_BG": "ERR_SOFT",
    "HOVER_BG": "SURFACE_2",
    "GHOST_BG": "SURFACE_2",
    "GHOST_HOVER": "SURFACE_3",
    "SCROLL_HANDLE": "LINE_2",
    "SCROLL_HOVER": "INK_3",
    "LOG_BG": "SURFACE",
    "LOG_TEXT": "INK",
    "SELECT_BG": "SURFACE_2",
    "TOPBAR": "APP_BG",
    "SHADOW": "SHADOW",
    "CARD_SHADOW_ALPHA": "SHADOW_ALPHA",
}

_THEME_KEYS = (
    "APP_BG", "SURFACE", "SURFACE_2", "SURFACE_3", "INK", "INK_2", "INK_3",
    "LINE", "LINE_2", "OK", "OK_SOFT", "WARN", "ERR", "ERR_SOFT",
    "ACCENT", "ACCENT_HOVER", "ACCENT_INK", "SIDEBAR", "SIDEBAR_INK",
    "SIDEBAR_INK_2", "SIDEBAR_ACTIVE", "SIDEBAR_HOVER", "SIDEBAR_LINE",
    "SHADOW", "SHADOW_ALPHA", "SERIES",
)

THEMES = {"dark": _tokens.DARK, "light": _tokens.LIGHT}
THEME_NAMES = _tokens.THEME_NAMES
CURRENT_THEME = "light"


def _publish(name: str) -> None:
    """把当前主题的令牌写进模块全局，旧代码的 T.INK 等才能取到值。"""
    globals()["CURRENT_THEME"] = name
    for key in _THEME_KEYS:
        globals()[key] = _tokens.THEMES[name][key]
    for old, new in _ALIAS.items():
        globals()[old] = _tokens.THEMES[name][new]


def build_style() -> str:
    return _style.build()


def apply_theme(name: str) -> str:
    name = _tokens.set_theme(name)
    _publish(name)
    _icons.clear_cache()
    globals()["STYLE"] = build_style()
    return name


def current_theme() -> str:
    return CURRENT_THEME


def bezier_curve() -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.OutCubic)
    return curve


def icon(name: str, color: str = "", size: int = 16, width: float = 1.7) -> QIcon:
    return _icons.icon(name, color or str(_tokens.t("INK_2")), size)


def icon_size(size: int = 16):
    return _icons.icon_size(size)


def glyph(name: str, color: str = "", size: int = 16) -> QPixmap:
    return _icons.pixmap(name, color or str(_tokens.t("INK_2")), size)


# ---------------------------------------------------------------- 主题偏好
def system_theme() -> str:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        finally:
            key.Close()
        return "light" if int(value) == 1 else "dark"
    except Exception:  # noqa: BLE001
        return "light"


def load_theme_pref() -> str:
    try:
        data = json.loads(THEME_FILE.read_text(encoding="utf-8-sig"))
        pref = str(data.get("pref") or "system")
    except (OSError, ValueError, TypeError):
        return "system"
    return pref if pref in ("system", "dark", "light") else "system"


def save_theme_pref(pref: str) -> None:
    try:
        THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
        THEME_FILE.write_text(json.dumps({"pref": pref}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def resolve_theme(pref: str) -> str:
    if pref in ("dark", "light"):
        return pref
    return system_theme()


def initial_theme() -> str:
    return resolve_theme(load_theme_pref())


def palette() -> QPalette:
    result = QPalette()
    bg = QColor(str(APP_BG))
    surface = QColor(str(SURFACE_2))
    text = QColor(str(INK))
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        result.setColor(role, bg if role != QPalette.ColorRole.Base else surface)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.ToolTipText,
    ):
        result.setColor(role, text)
    result.setColor(QPalette.ColorRole.Highlight, QColor(str(ACCENT)))
    result.setColor(QPalette.ColorRole.HighlightedText, QColor(str(ACCENT_INK)))
    result.setColor(QPalette.ColorRole.ToolTipBase, QColor(str(SURFACE)))
    return result


_publish(_tokens.current_theme())
STYLE = build_style()

# ---------------------------------------------------------------- 配置字段元数据（沿用 v6）
FIELD_UI: dict[str, tuple[str, str, str]] = {
    "base_url": ("接口地址", "模型与密钥", "形如 https://api.deepseek.com/v1"),
    "model": ("模型名称", "模型与密钥", "例如 deepseek-chat"),
    "api_key": ("API Key", "模型与密钥", "留空保持不变；填写后写入服务器 .env 并重启 bot"),
    "bot_qq": ("Bot QQ", "账号与权限", "该实例登录的 QQ 号"),
    "respond_to_groups": ("响应群聊", "账号与权限", "关闭后只回私聊"),
    "allowed_users": ("白名单", "账号与权限", "逗号分隔；留空表示不限制"),
    "admin_users": ("管理员", "账号与权限", "可执行管理指令的 QQ，逗号分隔"),
    "napcat_ws_url": ("OneBot 地址", "账号与权限", "每个实例需要独立的 NapCat 端口"),
    "max_history": ("上下文条数", "上下文与记忆", "送入模型的历史消息条数"),
    "summary_enabled": ("启用摘要", "上下文与记忆", "超长对话压缩成摘要节省 token"),
    "summary_threshold": ("摘要触发条数", "上下文与记忆", "达到该条数后开始压缩"),
    "summary_keep_recent": ("保留最近条数", "上下文与记忆", "压缩时原样保留的近期消息"),
    "summary_async": ("异步摘要", "上下文与记忆", "后台生成，不阻塞回复"),
    "summary_token_threshold": ("摘要 token 阈值", "上下文与记忆", "按 token 触发压缩"),
    "usage_enabled": ("用量记账", "上下文与记忆", "记录每次调用的 token 消耗"),
    "streaming_enabled": ("流式输出", "输出节奏", "边生成边发送"),
    "chunking_enabled": ("分条发送", "输出节奏", "把长回复拆成多条，更像真人"),
    "chunk_min_delay": ("分条最短间隔", "输出节奏", "秒"),
    "chunk_max_delay": ("分条最长间隔", "输出节奏", "秒"),
    "chunk_max_segments": ("最多分几条", "输出节奏", "建议 5~7"),
    "message_max_chars": ("单条最大字数", "输出节奏", "超过则拆分"),
    "proactive_enabled": ("主动开口", "主动会话", "长时间无对话时主动发起"),
    "proactive_idle_hours": ("空闲触发时长", "主动会话", "小时"),
    "proactive_max_per_day": ("每日上限", "主动会话", "次"),
    "proactive_quiet_hours": ("免打扰时段", "主动会话", "例如 23:00-08:00"),
    "proactive_targets": ("主动对话对象", "主动会话", "逗号分隔的 QQ 号；留空则不主动发任何人"),
    "proactive_jitter_minutes": ("随机延迟上限", "主动会话", "分钟；到点后再随机等这么久才发"),
    "persona": ("人设", "人设与语音", "内置 aemeath，也可填提示词文件路径"),
    "sticker_enabled": ("表情包开关", "表情包", "从 data/stickers 随机发图，默认关闭"),
    "sticker_probability": ("发送概率", "表情包", "0~1；模型给出标记后再按概率决定"),
    "sticker_cooldown": ("冷却（秒）", "表情包", "两条表情之间最少间隔"),
    "sticker_max_per_day": ("每人每日上限", "表情包", "超过后当天不再发"),
    "voice_input_enabled": ("语音输入", "人设与语音", "识别对方语音"),
    "voice_reply_enabled": ("语音回复", "人设与语音", "用 TTS 回语音"),
    "asr_endpoint": ("ASR 接口", "人设与语音", "语音识别服务地址"),
    "voice_reply_probability": ("语音概率", "人设与语音", "0~1；每条回复后按概率补一条语音"),
    "tts_endpoint": ("TTS 接口", "人设与语音", "语音合成服务地址"),
    "tts_api_key": ("TTS Key", "人设与语音", "留空则复用主模型 Key；换服务商时单独填"),
    "asr_api_key": ("ASR Key", "人设与语音", "语音识别 Key，留空复用主模型 Key"),
    "tts_model": ("TTS 模型", "人设与语音", "如 tts-1 或 FunAudioLLM/CosyVoice2-0.5B"),
    "tts_voice": ("TTS 音色", "人设与语音", "如 alloy / nova / alex"),
    "voice_max_seconds": ("语音最长时长", "人设与语音", "秒"),
    "voice_path_map": ("语音路径映射", "人设与语音", "容器内路径与宿主机路径的对应"),
}

FIELD_UI.update({
    'voice_mode': ('语音来源', "本地变声", 'local_tts=本机直接合成；rvc=云端底音加本机变声；cloud_tts=云端合成'),
    'local_tts_python': ('本地合成解释器', "本地变声", 'GPT-SoVITS 独立 Python 环境'),
    'local_tts_script': ('本地合成 API 脚本', "本地变声", '提供 /health 和 /tts 的本机 API 脚本'),

    'rvc_enabled': ('启用本地语音', "本地变声", '本地模型可用时发送训练音色'),
    'rvc_fallback': ('本机离线时', "本地变声", 'off=只发文字；cloud=使用基础 TTS 音色'),
    'rvc_endpoint': ('云端连接地址', "本地变声", '一键连接会填写 SSH 隧道地址；留空使用同机转换'),
    'rvc_api_key': ('连接密钥', "本地变声", '一键连接自动生成；通过 SSH 同步到服务器'),
    'rvc_default_voice': ('默认音色', "本地变声", '注册音色 ID，默认 aemeath'),
    'rvc_index_rate': ('索引强度', "本地变声", '0~1，建议 0.75'),
    'rvc_f0_method': ('音高算法', "本地变声", 'rmvpe 或 pm'),
    'rvc_pitch': ('变调半音', "本地变声", '0=不变调'),
    'rvc_device': ('推理设备', "本地变声", 'auto / cpu；在本机生效'),
    'rvc_python': ('RVC 解释器', "本地变声", '本机独立 RVC 环境的 python.exe'),
    'rvc_convert_script': ('转换脚本', "本地变声", '本机 aemeath_vc/convert.py 的绝对路径'),
})

GROUP_ORDER = ["模型与密钥", "账号与权限", "上下文与记忆", "输出节奏", "主动会话", "人设与语音", "本地变声", "表情包"]

GROUP_ICONS = {
    "模型与密钥": "key",
    "账号与权限": "shield",
    "上下文与记忆": "brain",
    "输出节奏": "activity",
    "主动会话": "message",
    "人设与语音": "mic",
    "表情包": "image",
}
