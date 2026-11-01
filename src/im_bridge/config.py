"""
配置加载与校验。

加载优先级（后者被前者覆盖）：
    环境变量 > .env 文件 > config.secret.json > config.json > 内置默认值

设计原则：
- 不在代码中硬编码任何密钥或个人账号；
- 所有阈值都给出可运行的默认值，缺省即可启动；
- 非法值不会让进程在导入期崩溃，而是收集到 ``Config.validate()`` 中统一提示。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# 项目根目录：src/im_bridge/config.py -> 上溯三层
BASE_DIR = Path(__file__).resolve().parents[2]

DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

# ---------- 内置默认值（均为可公开的占位） ----------
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BOT_QQ = 123456789
DEFAULT_NAPCAT_WS_URL = "ws://127.0.0.1:3001"
DEFAULT_MAX_HISTORY = 40          # 上下文窗口：消息条数上限（约 20 轮对话）
DEFAULT_SUMMARY_THRESHOLD = 24    # 消息条数超过该值时触发摘要压缩
DEFAULT_SUMMARY_KEEP_RECENT = 8   # 压缩后保留的最近消息条数
DEFAULT_CHUNK_MIN_DELAY = 1.0     # 分片发送最小间隔（秒）
DEFAULT_CHUNK_MAX_DELAY = 2.0     # 分片发送最大间隔（秒）
DEFAULT_PERSONA = "aemeath"

API_KEY_PLACEHOLDER = "sk-your-api-key-here"

_TRUE_VALUES = {"1", "true", "yes", "on", "y", "t"}
_FALSE_VALUES = {"0", "false", "no", "off", "n", "f"}


class ConfigError(ValueError):
    """配置解析或校验失败。"""


# ============================================================
# 字段表 —— 单一事实来源
# ============================================================
@dataclass(frozen=True)
class FieldSpec:
    """一个配置字段的完整描述。

    ``kind`` 决定解析方式与校验规则：
        bool / int / float / str / url / ws_url / id_list / secret
    """

    env: str                     # 环境变量名，如 AMIS_API_KEY
    key: str                     # config.json / config.secret.json 中的键
    attr: str                    # Config 对象上的属性名
    kind: str                    # 见上
    default: object              # 内置默认值
    label: str                   # 中文说明，用于 --check-config 展示
    hint: str = ""               # 出问题时的修复建议
    required: bool = False
    minimum: float | None = None
    maximum: float | None = None
    example: str = ""            # .env.example 中应出现的示例值
    param: str = ""              # 构造参数名；为空时与 attr 相同

    @property
    def arg(self) -> str:
        return self.param or self.attr


@dataclass(frozen=True)
class ConfigIssue:
    """一条配置问题，四要素齐全：字段名、来源、当前值、原因+建议。"""

    field: str
    source: str
    value: object
    reason: str
    hint: str = ""
    level: str = "error"         # error | warning

    def __str__(self) -> str:
        mark = "错误" if self.level == "error" else "警告"
        text = f"[{mark}] {self.field}（来源：{self.source}）= {self.value!r} —— {self.reason}"
        if self.hint:
            text += f"。修复：{self.hint}"
        return text


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        env="AMIS_API_KEY", key="api_key", attr="api_key", kind="secret",
        default="", label="大模型 API Key", required=True,
        hint="在 .env 中写入 AMIS_API_KEY=sk-你的真实密钥",
        example=API_KEY_PLACEHOLDER,
    ),
    FieldSpec(
        env="AMIS_BASE_URL", key="base_url", attr="llm_base_url", kind="url",
        default=DEFAULT_BASE_URL, label="OpenAI 兼容接口地址",
        hint="只写到 /v1 为止，不要带 /chat/completions",
        example=DEFAULT_BASE_URL, param="base_url",
    ),
    FieldSpec(
        env="AMIS_MODEL", key="model", attr="model", kind="str",
        default=DEFAULT_MODEL, label="模型名称",
        hint="填服务商文档里的模型 ID，如 deepseek-chat",
        example=DEFAULT_MODEL,
    ),
    FieldSpec(
        env="AMIS_BOT_QQ", key="bot_qq", attr="bot_qq", kind="int",
        default=DEFAULT_BOT_QQ, label="机器人 QQ 号",
        hint="填纯数字的 QQ 号", minimum=1, example="123456789",
    ),
    FieldSpec(
        env="AMIS_RESPOND_TO_GROUPS", key="respond_to_groups", attr="respond_to_groups",
        kind="bool", default=False, label="是否响应群聊",
        hint="填 true 或 false", example="false",
    ),
    FieldSpec(
        env="AMIS_ALLOWED_USERS", key="allowed_users", attr="allowed_users",
        kind="id_list", default=(), label="用户白名单",
        hint="逗号分隔的 QQ 号，留空表示不限制", example="",
    ),
    FieldSpec(
        env="AMIS_ADMIN_USERS", key="admin_users", attr="admin_users",
        kind="id_list", default=(), label="管理员",
        hint="逗号分隔的 QQ 号，可执行 /status /allow /deny /log /stop", example="",
    ),
    FieldSpec(
        env="AMIS_NAPCAT_WS_URL", key="napcat_ws_url", attr="napcat_ws_url",
        kind="ws_url", default=DEFAULT_NAPCAT_WS_URL, label="OneBot WebSocket 地址",
        hint="必须是 ws:// 或 wss:// 开头，端口与 NapCat 配置一致",
        example=DEFAULT_NAPCAT_WS_URL,
    ),
    FieldSpec(
        env="AMIS_MAX_HISTORY", key="max_history", attr="max_history", kind="int",
        default=DEFAULT_MAX_HISTORY, label="上下文窗口消息条数",
        hint="建议 20~60，至少为 4", minimum=4, example="40",
    ),
    FieldSpec(
        env="AMIS_SUMMARY_ENABLED", key="summary_enabled", attr="summary_enabled",
        kind="bool", default=True, label="是否启用历史摘要",
        hint="填 true 或 false", example="true",
    ),
    FieldSpec(
        env="AMIS_SUMMARY_THRESHOLD", key="summary_threshold", attr="summary_threshold",
        kind="int", default=DEFAULT_SUMMARY_THRESHOLD, label="触发摘要的消息条数",
        hint="必须大于 KEEP_RECENT 且小于 MAX_HISTORY", minimum=2, example="24",
    ),
    FieldSpec(
        env="AMIS_SUMMARY_KEEP_RECENT", key="summary_keep_recent", attr="summary_keep_recent",
        kind="int", default=DEFAULT_SUMMARY_KEEP_RECENT, label="摘要后保留的最近条数",
        hint="建议 4~12", minimum=1, example="8",
    ),
    FieldSpec(
        env="AMIS_STREAMING_ENABLED", key="streaming_enabled", attr="streaming_enabled",
        kind="bool", default=True, label="是否使用 SSE 流式",
        hint="填 true 或 false", example="true",
    ),
    FieldSpec(
        env="AMIS_CHUNKING_ENABLED", key="chunking_enabled", attr="chunking_enabled",
        kind="bool", default=True, label="是否按标点分片发送",
        hint="填 true 或 false", example="true",
    ),
    FieldSpec(
        env="AMIS_CHUNK_MIN_DELAY", key="chunk_min_delay", attr="chunk_min_delay",
        kind="float", default=DEFAULT_CHUNK_MIN_DELAY, label="分片最小间隔（秒）",
        hint="不能为负，且不能大于 MAX_DELAY", minimum=0, example="1.0",
    ),
    FieldSpec(
        env="AMIS_CHUNK_MAX_DELAY", key="chunk_max_delay", attr="chunk_max_delay",
        kind="float", default=DEFAULT_CHUNK_MAX_DELAY, label="分片最大间隔（秒）",
        hint="不能小于 MIN_DELAY，建议 1~3", example="2.0",
    ),
    # ---------- 分片与长度上限（Issue #1） ----------
    FieldSpec(
        env="AMIS_CHUNK_MAX_SEGMENTS", key="chunk_max_segments", attr="chunk_max_segments",
        kind="int", default=8, label="单次回复最多分几条",
        hint="0 表示不限制；建议 4~10，太多会刷屏", minimum=0, example="8",
    ),
    FieldSpec(
        env="AMIS_MESSAGE_MAX_CHARS", key="message_max_chars", attr="message_max_chars",
        kind="int", default=1000, label="单条消息字数上限",
        hint="超过该长度会自动拆条，QQ 建议不超过 1500", minimum=50, example="1000",
    ),
    # ---------- 摘要异步化（Issue #2） ----------
    FieldSpec(
        env="AMIS_SUMMARY_ASYNC", key="summary_async", attr="summary_async",
        kind="bool", default=True, label="摘要后台异步执行",
        hint="true 不阻塞回复；false 恢复同步压缩", example="true",
    ),
    FieldSpec(
        env="AMIS_SUMMARY_TOKEN_THRESHOLD", key="summary_token_threshold",
        attr="summary_token_threshold", kind="int", default=1800,
        label="触发摘要的 token 估算阈值",
        hint="中文约 1 字≈1 token，建议 1200~3000", minimum=200, example="1800",
    ),
    FieldSpec(
        env="AMIS_USAGE_ENABLED", key="usage_enabled", attr="usage_enabled",
        kind="bool", default=True, label="是否记录 token 用量",
        hint="开启后写入 data/usage/，供桌面端统计花费", example="true",
    ),
    # ---------- 主动会话（Issue #4） ----------
    FieldSpec(
        env="AMIS_PROACTIVE_ENABLED", key="proactive_enabled", attr="proactive_enabled",
        kind="bool", default=False, label="是否允许主动发起聊天",
        hint="默认关闭；开启前请确认静默时段与每日上限", example="false",
    ),
    FieldSpec(
        env="AMIS_PROACTIVE_IDLE_HOURS", key="proactive_idle_hours",
        attr="proactive_idle_hours", kind="float", default=24.0,
        label="闲置多少小时后可主动搭话",
        hint="建议 12~72，太小会显得黏人", minimum=1, example="24",
    ),
    FieldSpec(
        env="AMIS_PROACTIVE_MAX_PER_DAY", key="proactive_max_per_day",
        attr="proactive_max_per_day", kind="int", default=1,
        label="每人每天最多主动几条",
        hint="建议 1~2；填 0 表示不主动", minimum=0, example="1",
    ),
    FieldSpec(
        env="AMIS_PROACTIVE_QUIET_HOURS", key="proactive_quiet_hours",
        attr="proactive_quiet_hours", kind="str", default="23:00-08:00",
        label="静默时段（不主动打扰）",
        hint="格式 HH:MM-HH:MM，可跨零点；留空表示不限制", example="23:00-08:00",
    ),
    FieldSpec(
        env="AMIS_PROACTIVE_TARGETS", key="proactive_targets", attr="proactive_targets",
        kind="id_list", default=(), label="主动对话对象",
        hint="逗号分隔的 QQ 号，只给名单里的人主动发消息；"
             "留空表示不主动发，避免打扰不熟的好友",
        example="10001,10002",
    ),
    FieldSpec(
        env="AMIS_PROACTIVE_JITTER_MINUTES", key="proactive_jitter_minutes",
        attr="proactive_jitter_minutes", kind="int", default=120,
        label="主动消息随机延迟（分钟）",
        hint="到了闲置阈值后再随机等 0~该分钟数才开口，避免每天同一时刻像定时任务",
        minimum=0, maximum=720, example="120",
    ),
    # ---------- 语音（Issue #5） ----------
    FieldSpec(
        env="AMIS_VOICE_INPUT_ENABLED", key="voice_input_enabled", attr="voice_input_enabled",
        kind="bool", default=False, label="是否把收到的语音转成文字",
        hint="需要配置 AMIS_ASR_ENDPOINT", example="false",
    ),
    FieldSpec(
        env="AMIS_VOICE_REPLY_ENABLED", key="voice_reply_enabled", attr="voice_reply_enabled",
        kind="bool", default=False, label="是否用语音回复",
        hint="需要配置 AMIS_TTS_ENDPOINT", example="false",
    ),
    FieldSpec(
        env="AMIS_ASR_ENDPOINT", key="asr_endpoint", attr="asr_endpoint", kind="str",
        default="", label="语音转写接口地址",
        hint="OpenAI 兼容的 /v1/audio/transcriptions 地址", example="",
    ),
    FieldSpec(
        env="AMIS_ASR_API_KEY", key="asr_api_key", attr="asr_api_key", kind="secret",
        default="", label="语音转写 API Key",
        hint="留空则复用 AMIS_API_KEY", example="",
    ),
    FieldSpec(
        env="AMIS_VOICE_REPLY_PROBABILITY", key="voice_reply_probability",
        attr="voice_reply_probability", kind="float", default=0.35,
        label="语音回复概率",
        hint="0~1；每条文字回复后按该概率补发一条语音，0 表示不发",
        minimum=0, maximum=1, example="0.35",
    ),
    FieldSpec(
        env="AMIS_TTS_ENDPOINT", key="tts_endpoint", attr="tts_endpoint", kind="str",
        default="", label="语音合成接口地址",
        hint="OpenAI 兼容的 /v1/audio/speech 地址", example="",
    ),
    FieldSpec(
        env="AMIS_TTS_API_KEY", key="tts_api_key", attr="tts_api_key", kind="secret",
        default="", label="语音合成 API Key",
        hint="留空则复用 AMIS_API_KEY", example="",
    ),
    FieldSpec(
        env="AMIS_TTS_MODEL", key="tts_model", attr="tts_model", kind="str",
        default="tts-1", label="语音合成模型",
        hint="OpenAI 填 tts-1；硅基流动填 FunAudioLLM/CosyVoice2-0.5B，按服务商文档照抄",
        example="tts-1",
    ),
    FieldSpec(
        env="AMIS_TTS_VOICE", key="tts_voice", attr="tts_voice", kind="str",
        default="alloy", label="语音音色",
        hint="模型支持的音色名，如 alloy / nova / alex；填错会返回 400",
        example="alloy",
    ),
    FieldSpec(
        env="AMIS_VOICE_MAX_SECONDS", key="voice_max_seconds", attr="voice_max_seconds",
        kind="int", default=60, label="语音最长时长（秒）",
        hint="超过该时长的语音不转写，直接忽略", minimum=1, example="60",
    ),
    FieldSpec(
        env="AMIS_VOICE_PATH_MAP", key="voice_path_map", attr="voice_path_map",
        kind="str", default="", label="语音文件路径映射",
        hint="格式 容器路径=宿主机路径，多个用逗号分隔；"
             "NapCat 在 Docker 里时必填，否则读不到语音文件",
        example="/app/.config/QQ=/www/wwwroot/napcat/qq",
    ),
    FieldSpec(
        env="AMIS_RVC_ENDPOINT", key="rvc_endpoint", attr="rvc_endpoint", kind="str",
        default="", label="RVC HTTP 桥接地址", hint="SSH 隧道地址；留空使用同机子进程", example="",
    ),
    FieldSpec(
        env="AMIS_RVC_API_KEY", key="rvc_api_key", attr="rvc_api_key", kind="secret",
        default="", label="RVC 连接密钥", example="",
    ),
    FieldSpec(env="AMIS_VOICE_MODE", key="voice_mode", attr="voice_mode", kind="str",
              default='local_tts', label='语音来源', hint='local_tts=本机直接合成；rvc=云端底音加本机变声；cloud_tts=云端合成', example=""),
    FieldSpec(env="AMIS_LOCAL_TTS_PYTHON", key="local_tts_python", attr="local_tts_python", kind="str",
              default='', label='本地合成解释器', hint='GPT-SoVITS 独立 Python 环境', example=""),
    FieldSpec(env="AMIS_LOCAL_TTS_SCRIPT", key="local_tts_script", attr="local_tts_script", kind="str",
              default='', label='本地合成 API 脚本', hint='提供 /health 和 /tts 的本机 API 脚本', example=""),
    # ---------- RVC 本地变声后端（用户自定义音色 / 本机开机时启用） ----------
    FieldSpec(
        env="AMIS_RVC_ENABLED", key="rvc_enabled", attr="rvc_enabled", kind="bool",
        default=True, label="是否启用 RVC 本地变声",
        hint="开启后本机可用时，语音回复会经 RVC 转成注册音色；"
             "本机不可用时按 AMIS_RVC_FALLBACK 回退",
        example="true",
    ),
    FieldSpec(
        env="AMIS_RVC_FALLBACK", key="rvc_fallback", attr="rvc_fallback", kind="str",
        default="off", label="本机不可用时的回退策略",
        hint="cloud=改用云 TTS 音色；off=不发语音", example="cloud",
    ),
    FieldSpec(
        env="AMIS_RVC_CONVERT_SCRIPT", key="rvc_convert_script", attr="rvc_convert_script",
        kind="str", default="",
        label="RVC 转换脚本路径",
        hint="指向 aemeath_vc/convert.py（脚本自带 rvc 引擎引用）",
        example="",
    ),
    FieldSpec(
        env="AMIS_RVC_PYTHON", key="rvc_python", attr="rvc_python", kind="str",
        default="",
        label="RVC 引擎 Python 解释器",
        hint="运行转换脚本的解释器（独立 venv，含 torch cu128）",
        example="",
    ),
    FieldSpec(
        env="AMIS_RVC_DEFAULT_VOICE", key="rvc_default_voice", attr="rvc_default_voice",
        kind="str", default="aemeath", label="默认本地音色 ID",
        hint="voices 注册表中的音色 id，/voice use 可切换", example="aemeath",
    ),
    FieldSpec(
        env="AMIS_RVC_INDEX_RATE", key="rvc_index_rate", attr="rvc_index_rate", kind="float",
        default=0.75, label="特征索引强度", minimum=0, maximum=1, example="0.75",
    ),
    FieldSpec(
        env="AMIS_RVC_F0_METHOD", key="rvc_f0_method", attr="rvc_f0_method", kind="str",
        default="rmvpe", label="音高提取算法", hint="rmvpe 或 pm", example="rmvpe",
    ),
    FieldSpec(
        env="AMIS_RVC_PITCH", key="rvc_pitch", attr="rvc_pitch", kind="int",
        default=0, label="变调半音数", example="0",
    ),
    FieldSpec(
        env="AMIS_RVC_DEVICE", key="rvc_device", attr="rvc_device", kind="str",
        default="auto", label="RVC 推理设备", hint="auto / gpu / cpu", example="auto",
    ),
    FieldSpec(
        env="AMIS_IMAGE_INPUT_ENABLED", key="image_input_enabled", attr="image_input_enabled",
        kind="bool", default=True, label="图片识别开关",
        hint="开启后入站图片会先转成文字描述再交给模型；接口不支持视觉请关闭",
        example="true",
    ),
    FieldSpec(
        env="AMIS_VISION_ENDPOINT", key="vision_endpoint", attr="vision_endpoint",
        kind="str", default="", label="图片识别接口地址",
        hint="OpenAI 兼容接口，只写到 /v1 为止；留空则复用 AMIS_BASE_URL", example="",
    ),
    FieldSpec(
        env="AMIS_VISION_API_KEY", key="vision_api_key", attr="vision_api_key",
        kind="secret", default="", label="图片识别 API Key",
        hint="留空则复用 AMIS_API_KEY", example="",
    ),
    FieldSpec(
        env="AMIS_VISION_MODEL", key="vision_model", attr="vision_model", kind="str",
        default="", label="图片识别模型",
        hint="留空则复用 AMIS_MODEL；模型必须支持图片输入", example="",
    ),
    FieldSpec(
        env="AMIS_VISION_MAX_BYTES", key="vision_max_bytes", attr="vision_max_bytes",
        kind="int", default=8_000_000, label="图片体积上限（字节）",
        hint="超过该大小的图片直接跳过识别，避免超时与费用失控",
        minimum=1024, example="8000000",
    ),
    FieldSpec(
        env="AMIS_IMAGE_MAX_COUNT", key="image_max_count", attr="image_max_count",
        kind="int", default=4, label="单条消息最多识别图片数",
        hint="一次发多张图时只识别前 N 张", minimum=1, example="4",
    ),
    FieldSpec(
        env="AMIS_PERSONA", key="persona", attr="persona", kind="str",
        default=DEFAULT_PERSONA, label="人设",
        hint="内置 aemeath，也可填 .txt/.md 提示词文件路径", example=DEFAULT_PERSONA,
    ),
    FieldSpec(
        env="AMIS_STICKER_ENABLED", key="sticker_enabled", attr="sticker_enabled",
        kind="bool", default=False, label="表情包开关",
        hint="开启后从 data/stickers 随机发送表情，默认关闭", example="false",
    ),
    FieldSpec(
        env="AMIS_STICKER_PROBABILITY", key="sticker_probability", attr="sticker_probability",
        kind="float", default=0.25, label="表情发送概率",
        hint="0~1；模型给出 [[表情:标签]] 后再按该概率决定是否真的发", example="0.25",
    ),
    FieldSpec(
        env="AMIS_STICKER_COOLDOWN", key="sticker_cooldown", attr="sticker_cooldown",
        kind="int", default=180, label="表情冷却（秒）",
        hint="两条表情之间至少间隔多少秒", minimum=0, example="180",
    ),
    FieldSpec(
        env="AMIS_STICKER_MAX_PER_DAY", key="sticker_max_per_day", attr="sticker_max_per_day",
        kind="int", default=30, label="每人每日表情上限",
        hint="超过后当天不再发送表情", minimum=0, example="30",
    ),
)

FIELD_BY_ENV: dict[str, FieldSpec] = {spec.env: spec for spec in FIELD_SPECS}
FIELD_BY_KEY: dict[str, FieldSpec] = {spec.key: spec for spec in FIELD_SPECS}


def load_dotenv(path: Path, override: bool = False) -> set[str]:
    """加载极简 .env 文件（不引入额外依赖）。

    支持 ``KEY=VALUE``、``# 注释``、``export KEY=VALUE`` 与成对引号。
    默认不覆盖真实环境变量。

    返回**本次真正写入**的键集合，用于区分「来自 .env」和「来自真实环境变量」。
    """
    loaded: set[str] = set()
    if not path.exists():
        return loaded
    try:
        lines = path.read_text("utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return loaded

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
            loaded.add(key)
    return loaded


def _read_json(path: Path) -> dict:
    """读取 JSON 配置；文件不存在或损坏时返回空字典。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _as_bool(value: object, spec: FieldSpec, source: str, issues: list[ConfigIssue]) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_VALUES:
            return True
        if text in _FALSE_VALUES:
            return False
    issues.append(
        ConfigIssue(
            field=spec.env,
            source=source,
            value=value,
            reason="不是合法的布尔值（应为 true/false）",
            hint=spec.hint or "填 true 或 false",
        )
    )
    return bool(spec.default)


def _as_int(value: object, spec: FieldSpec, source: str, issues: list[ConfigIssue]) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        issues.append(
            ConfigIssue(
                field=spec.env,
                source=source,
                value=value,
                reason="必须是整数",
                hint=spec.hint or "填写一个整数",
            )
        )
        return int(spec.default)  # type: ignore[arg-type]


def _as_float(value: object, spec: FieldSpec, source: str, issues: list[ConfigIssue]) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        issues.append(
            ConfigIssue(
                field=spec.env,
                source=source,
                value=value,
                reason="必须是数字",
                hint=spec.hint or "填写一个数字，如 1.0",
            )
        )
        return float(spec.default)  # type: ignore[arg-type]


def _as_id_list(value: object, spec: FieldSpec, source: str, issues: list[ConfigIssue]) -> list[str]:
    """把列表、逗号分隔字符串、或「字符串形式的列表」统一成字符串 ID 列表。

    兼容历史数据：管理台保存时曾把 ``["123"]`` 写成字符串 ``'["123"]'`` /
    ``"['123']"``，直接按逗号分割会把它当成一个非法 QQ 号，导致启动校验失败。
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        items: list[str]
        if text.startswith("[") and text.endswith("]"):
            parsed: object = None
            try:
                parsed = json.loads(text)
            except ValueError:
                try:
                    import ast

                    parsed = ast.literal_eval(text)
                except (ValueError, SyntaxError):
                    parsed = None
            if isinstance(parsed, (list, tuple)):
                items = [str(item) for item in parsed]
            else:
                items = text.split(",")
        else:
            items = text.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        issues.append(
            ConfigIssue(
                field=spec.env,
                source=source,
                value=value,
                reason="应为数组或逗号分隔的字符串",
                hint=spec.hint,
            )
        )
        return []

    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if not text.isdigit():
            issues.append(
                ConfigIssue(
                    field=spec.env,
                    source=source,
                    value=text,
                    reason="不是纯数字 QQ 号",
                    hint=spec.hint,
                )
            )
            continue
        result.append(text)
    return result


class Config:
    """运行期配置对象。"""

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        bot_qq: int = DEFAULT_BOT_QQ,
        respond_to_groups: bool = False,
        allowed_users: list[str] | None = None,
        admin_users: list[str] | None = None,
        napcat_ws_url: str = DEFAULT_NAPCAT_WS_URL,
        max_history: int = DEFAULT_MAX_HISTORY,
        summary_enabled: bool = True,
        summary_threshold: int = DEFAULT_SUMMARY_THRESHOLD,
        summary_keep_recent: int = DEFAULT_SUMMARY_KEEP_RECENT,
        streaming_enabled: bool = True,
        chunking_enabled: bool = True,
        chunk_min_delay: float = DEFAULT_CHUNK_MIN_DELAY,
        chunk_max_delay: float = DEFAULT_CHUNK_MAX_DELAY,
        chunk_max_segments: int = 8,
        message_max_chars: int = 1000,
        summary_async: bool = True,
        summary_token_threshold: int = 1800,
        usage_enabled: bool = True,
        proactive_enabled: bool = False,
        proactive_idle_hours: float = 24.0,
        proactive_max_per_day: int = 1,
        proactive_quiet_hours: str = "23:00-08:00",
        proactive_targets: list[str] | None = None,
        proactive_jitter_minutes: int = 120,
        voice_input_enabled: bool = False,
        voice_reply_enabled: bool = False,
        voice_reply_probability: float = 0.35,
        asr_endpoint: str = "",
        asr_api_key: str = "",
        tts_endpoint: str = "",
        tts_api_key: str = "",
        tts_model: str = "tts-1",
        tts_voice: str = "alloy",
        voice_max_seconds: int = 60,
        voice_path_map: str = "",
        voice_mode: str = 'local_tts',
        local_tts_python: str = '',
        local_tts_script: str = '',
        rvc_endpoint: str = "",
        rvc_api_key: str = "",
        rvc_enabled: bool = True,
        rvc_fallback: str = "off",
        rvc_convert_script: str = "",
        rvc_python: str = "",
        rvc_default_voice: str = "aemeath",
        rvc_index_rate: float = 0.75,
        rvc_f0_method: str = "rmvpe",
        rvc_pitch: int = 0,
        rvc_device: str = "auto",
        image_input_enabled: bool = True,
        vision_endpoint: str = "",
        vision_api_key: str = "",
        vision_model: str = "",
        vision_max_bytes: int = 8_000_000,
        image_max_count: int = 4,
        sticker_enabled: bool = False,
        sticker_probability: float = 0.25,
        sticker_cooldown: int = 180,
        sticker_max_per_day: int = 30,
        persona: str = DEFAULT_PERSONA,
        data_dir: Path | str | None = None,
        config_path: Path | None = None,
        load_errors: list[ConfigIssue] | None = None,
        sources: dict[str, str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.llm_base_url = base_url
        self.model = model
        self.bot_qq = bot_qq
        self.respond_to_groups = respond_to_groups
        self.allowed_users = allowed_users or []
        self.admin_users = admin_users or []
        self.napcat_ws_url = napcat_ws_url
        self.max_history = max_history
        self.summary_enabled = summary_enabled
        self.summary_threshold = summary_threshold
        self.summary_keep_recent = summary_keep_recent
        self.streaming_enabled = streaming_enabled
        self.chunking_enabled = chunking_enabled
        self.chunk_min_delay = chunk_min_delay
        self.chunk_max_delay = chunk_max_delay
        self.chunk_max_segments = chunk_max_segments
        self.message_max_chars = message_max_chars
        self.summary_async = summary_async
        self.summary_token_threshold = summary_token_threshold
        self.usage_enabled = usage_enabled
        self.proactive_enabled = proactive_enabled
        self.proactive_idle_hours = proactive_idle_hours
        self.proactive_max_per_day = proactive_max_per_day
        self.proactive_quiet_hours = proactive_quiet_hours
        self.proactive_targets = proactive_targets or []
        self.proactive_jitter_minutes = proactive_jitter_minutes
        self.voice_input_enabled = voice_input_enabled
        self.voice_reply_enabled = voice_reply_enabled
        self.voice_reply_probability = voice_reply_probability
        self.asr_endpoint = asr_endpoint
        self.asr_api_key = asr_api_key
        self.tts_endpoint = tts_endpoint
        self.tts_api_key = tts_api_key
        self.tts_model = tts_model
        self.tts_voice = tts_voice
        self.voice_max_seconds = voice_max_seconds
        self.voice_path_map = voice_path_map
        self.voice_mode = voice_mode
        self.local_tts_python = local_tts_python
        self.local_tts_script = local_tts_script
        self.rvc_endpoint = rvc_endpoint.strip()
        self.rvc_api_key = rvc_api_key
        self.rvc_enabled = rvc_enabled
        self.rvc_fallback = (rvc_fallback or "off").strip().lower()
        self.rvc_convert_script = rvc_convert_script
        self.rvc_python = rvc_python
        self.rvc_default_voice = rvc_default_voice
        self.rvc_index_rate = rvc_index_rate
        self.rvc_f0_method = rvc_f0_method
        self.rvc_pitch = rvc_pitch
        self.rvc_device = (rvc_device or "auto").strip().lower()
        self.image_input_enabled = image_input_enabled
        self.vision_endpoint = vision_endpoint
        self.vision_api_key = vision_api_key
        self.vision_model = vision_model
        self.vision_max_bytes = vision_max_bytes
        self.image_max_count = image_max_count
        self.sticker_enabled = sticker_enabled
        self.sticker_probability = sticker_probability
        self.sticker_cooldown = sticker_cooldown
        self.sticker_max_per_day = sticker_max_per_day
        self.persona = persona
        # 数据目录：多实例下每实例独立（data/<id>），单实例沿用根 data/
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.config_path = config_path or (BASE_DIR / "config.json")
        self.load_errors = list(load_errors or [])
        self.sources = dict(sources or {})

    # ---------- 运行期可变配置 ----------
    @property
    def masked_api_key(self) -> str:
        key = (self.api_key or "").strip()
        if not key:
            return "<未设置>"
        prefix = "sk-***" if key.startswith("sk-") else "***"
        return f"{prefix}（已配置，长度 {len(key)}）"

    def save(self) -> None:
        """把动态白名单/管理员列表写回 config.json（不落盘密钥）。"""
        path = self.config_path
        data = _read_json(path)
        data["allowed_users"] = self.allowed_users
        data["admin_users"] = self.admin_users
        try:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=4) + "\n", "utf-8"
            )
        except OSError:
            pass

    def add_allowed_user(self, user_id: str) -> bool:
        user_id = str(user_id).strip()
        if not user_id or user_id in self.allowed_users:
            return False
        self.allowed_users.append(user_id)
        self.save()
        return True

    def remove_allowed_user(self, user_id: str) -> bool:
        user_id = str(user_id).strip()
        if user_id not in self.allowed_users:
            return False
        self.allowed_users.remove(user_id)
        self.save()
        return True

    def is_admin(self, user_id: str) -> bool:
        return str(user_id) in self.admin_users

    def is_allowed(self, user_id: str) -> bool:
        """白名单为空表示不限制；管理员始终允许。"""
        if not self.allowed_users:
            return True
        return str(user_id) in self.allowed_users or self.is_admin(user_id)

    # ---------- 校验 ----------
    def _source_of(self, spec: FieldSpec) -> str:
        return self.sources.get(spec.env, "默认值")

    def _display_value(self, spec: FieldSpec) -> object:
        """密钥类字段只展示是否已配置，绝不输出原文。"""
        if spec.kind == "secret":
            value = (getattr(self, spec.attr, "") or "").strip()
            return "***（已配置）" if value else "<未设置>"
        return getattr(self, spec.attr, None)

    def _issue(
        self, spec: FieldSpec, reason: str, hint: str = "", level: str = "error"
    ) -> ConfigIssue:
        return ConfigIssue(
            field=spec.env,
            source=self._source_of(spec),
            value=self._display_value(spec),
            reason=reason,
            hint=hint or spec.hint,
            level=level,
        )

    def validate_issues(self) -> list[ConfigIssue]:
        """返回结构化问题列表（含 warning），供 --check-config 渲染。"""
        issues: list[ConfigIssue] = list(self.load_errors)

        # --- 必填与格式 ---
        spec = FIELD_BY_ENV["AMIS_API_KEY"]
        key = (self.api_key or "").strip()
        if not key:
            issues.append(self._issue(spec, "未配置大模型 API Key，无法调用模型"))
        elif key == API_KEY_PLACEHOLDER:
            issues.append(self._issue(spec, "大模型 API Key 仍是示例占位符，不是真实密钥"))

        spec = FIELD_BY_ENV["AMIS_BASE_URL"]
        url = (self.llm_base_url or "").strip()
        if not url.startswith(("http://", "https://")):
            issues.append(self._issue(spec, "必须以 http:// 或 https:// 开头"))
        elif url.rstrip("/").endswith("/chat/completions"):
            issues.append(
                self._issue(spec, "末尾多了 /chat/completions，客户端会自动拼接该路径")
            )
        elif "/v1" not in url:
            issues.append(
                self._issue(
                    spec,
                    "地址里没看到 /v1，多数 OpenAI 兼容服务需要它",
                    hint="确认服务商要求；DeepSeek 应为 https://api.deepseek.com/v1",
                    level="warning",
                )
            )

        spec = FIELD_BY_ENV["AMIS_NAPCAT_WS_URL"]
        if not (self.napcat_ws_url or "").strip().startswith(("ws://", "wss://")):
            issues.append(self._issue(spec, "必须以 ws:// 或 wss:// 开头"))

        spec = FIELD_BY_ENV["AMIS_MODEL"]
        if not (self.model or "").strip():
            issues.append(self._issue(spec, "不能为空"))

        spec = FIELD_BY_ENV["AMIS_PERSONA"]
        if not (self.persona or "").strip():
            issues.append(self._issue(spec, "不能为空"))

        # --- 数值范围（由字段表驱动，新增字段自动生效） ---
        for spec in FIELD_SPECS:
            if spec.kind not in ("int", "float"):
                continue
            value = getattr(self, spec.attr, None)
            if not isinstance(value, (int, float)):
                continue
            if spec.minimum is not None and value < spec.minimum:
                issues.append(self._issue(spec, f"不能小于 {spec.minimum}"))
            if spec.maximum is not None and value > spec.maximum:
                issues.append(self._issue(spec, f"不能大于 {spec.maximum}"))

        # --- 交叉约束 ---
        spec = FIELD_BY_ENV["AMIS_SUMMARY_KEEP_RECENT"]
        if self.summary_keep_recent < 2:
            issues.append(self._issue(spec, "不能小于 2"))

        if self.summary_enabled:
            spec = FIELD_BY_ENV["AMIS_SUMMARY_THRESHOLD"]
            if self.summary_threshold <= self.summary_keep_recent:
                issues.append(
                    self._issue(
                        spec,
                        "必须大于 AMIS_SUMMARY_KEEP_RECENT"
                        f"（当前 {self.summary_threshold} <= {self.summary_keep_recent}）",
                    )
                )
            elif self.summary_threshold >= self.max_history:
                issues.append(
                    self._issue(
                        spec,
                        "必须小于 AMIS_MAX_HISTORY"
                        f"（当前 {self.summary_threshold} >= {self.max_history}），"
                        "否则窗口会先裁剪、摘要永不触发",
                    )
                )

        min_spec = FIELD_BY_ENV["AMIS_CHUNK_MIN_DELAY"]
        max_spec = FIELD_BY_ENV["AMIS_CHUNK_MAX_DELAY"]
        if self.chunk_min_delay > self.chunk_max_delay:
            issues.append(
                ConfigIssue(
                    field=f"{min_spec.env} / {max_spec.env}",
                    source=f"{self._source_of(min_spec)} / {self._source_of(max_spec)}",
                    value=f"{self.chunk_min_delay} > {self.chunk_max_delay}",
                    reason="最小间隔不能大于最大间隔",
                    hint="让 AMIS_CHUNK_MIN_DELAY <= AMIS_CHUNK_MAX_DELAY",
                )
            )

        return issues

    def validate(self) -> list[str]:
        """返回错误文本列表（仅 error，含四要素），空列表表示通过。"""
        return [str(item) for item in self.validate_issues() if item.level == "error"]

    def warnings(self) -> list[str]:
        """返回警告文本列表（不阻断启动）。"""
        return [str(item) for item in self.validate_issues() if item.level == "warning"]

    def summary(self) -> str:
        """用于启动日志和 --check-config 的脱敏摘要。"""
        return (
            f"base_url={self.llm_base_url} model={self.model} "
            f"bot_qq={self.bot_qq} api_key={self.masked_api_key}\n"
            f"max_history={self.max_history} "
            f"summary={self.summary_enabled}"
            f"({self.summary_threshold}/{self.summary_keep_recent}) "
            f"streaming={self.streaming_enabled} chunking={self.chunking_enabled}"
            f"({self.chunk_min_delay}~{self.chunk_max_delay}s) persona={self.persona}"
        )

    # ---------- 构建 ----------
    @classmethod
    def from_env_or_file(
        cls,
        config_path: Path | str | None = None,
        env_dir: Path | str | None = None,
        data_dir: Path | str | None = None,
    ) -> "Config":
        """加载配置。

        多实例场景下三个目录是分开的：
        - ``config_path``：实例自己的 config.json（如 ``instances/bot2/config.json``）
        - ``env_dir``：共享密钥目录（根目录的 .env / config.secret.json），默认与配置文件同目录
        - ``data_dir``：该实例独立的数据目录，默认 ``<配置文件目录>/data``
        """
        path = Path(config_path).expanduser() if config_path else (BASE_DIR / "config.json")
        base_dir = path.resolve().parent
        secret_dir = Path(env_dir).expanduser().resolve() if env_dir else base_dir
        dotenv_keys = load_dotenv(secret_dir / ".env")

        data = _read_json(path)
        secret = _read_json(secret_dir / "config.secret.json")
        issues: list[ConfigIssue] = []
        sources: dict[str, str] = {}

        def pick(spec: FieldSpec) -> tuple[object, str]:
            """按优先级取原始值，并记录它到底来自哪里（用于报错提示）。"""
            env_value = os.environ.get(spec.env)
            if env_value is not None and env_value.strip() != "":
                return env_value, (
                    ".env 文件" if spec.env in dotenv_keys else "环境变量"
                )
            # 密钥类优先读 config.secret.json，普通字段优先读 config.json
            if spec.kind == "secret":
                stores = ((secret, "config.secret.json"), (data, "config.json"))
            else:
                stores = ((data, "config.json"), (secret, "config.secret.json"))
            for store, label in stores:
                if spec.key in store and store[spec.key] not in (None, ""):
                    return store[spec.key], label
            return spec.default, "默认值"

        values: dict[str, object] = {}
        for spec in FIELD_SPECS:
            raw, source = pick(spec)
            sources[spec.env] = source
            if spec.kind == "bool":
                values[spec.arg] = _as_bool(raw, spec, source, issues)
            elif spec.kind == "int":
                values[spec.arg] = _as_int(raw, spec, source, issues)
            elif spec.kind == "float":
                values[spec.arg] = _as_float(raw, spec, source, issues)
            elif spec.kind == "id_list":
                values[spec.arg] = _as_id_list(raw, spec, source, issues)
            else:  # str / url / ws_url / secret
                values[spec.arg] = str(raw).strip()

        return cls(
            config_path=path,
            load_errors=issues,
            sources=sources,
            data_dir=Path(data_dir).expanduser() if data_dir else (base_dir / "data"),
            **values,
        )
