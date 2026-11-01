"""
图片理解 —— 把入站图片转成一段中文描述。

OneBot 的图片消息段只有 ``url`` / ``file`` 标识，模型看不到像素；这里先取到
图片字节，再调用 OpenAI 兼容的视觉接口（``/chat/completions``，content 为
``text`` + ``image_url`` 两段），把结果作为客观描述交回调用方。

这样后续链路（上下文、摘要、人设、表情）全部按纯文本处理，历史里也能留下
「图片讲了什么」，不会因为重发 base64 把 token 打爆。

设计原则与 ``voice.py`` 一致：未启用、图片过大、下载失败、模型报错一律只记
``warning`` 并返回空字符串，绝不把正常对话带崩。日志不输出 API Key。
"""
from __future__ import annotations

import base64
import logging

import httpx

logger = logging.getLogger("im_bridge.vision")

CHAT_PATH = "/chat/completions"

VISION_SYSTEM_PROMPT = (
    "你是图片解说器：把图片内容转写成客观描述，供一位看不到图片的聊天助手理解。"
    "必须覆盖三点：①画面主体、场景与人物动作；②图中所有可见文字（原样摘录，"
    "看不清就说明看不清）；③如果是截图、聊天记录、表情包或梗图，说明类型和关键信息。"
    "不要评价，不要客套，不要 Markdown，只输出描述正文。"
)

VISION_USER_PROMPT = "请按系统要求描述这张图片，200 字以内。"


def _resolve_url(endpoint: str, suffix: str) -> str:
    """把配置的接口地址规范成完整地址（支持只写 base 或写全路径两种写法）。"""
    base = (endpoint or "").strip().rstrip("/")
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


class ImageDescriber:
    """OpenAI 兼容的图片描述客户端。"""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        timeout: float = 45,
        max_bytes: int = 8_000_000,
        max_chars: int = 400,
    ) -> None:
        self.endpoint = (endpoint or "").strip()
        self.api_key = api_key
        self.model = (model or "").strip()
        self.timeout = timeout
        self.max_bytes = max(1, int(max_bytes))
        self.max_chars = max_chars
        # 最近一次失败原因，便于在日志/排查里一眼看出断点
        self.last_reason = ""

    @property
    def enabled(self) -> bool:
        """接口地址、密钥、模型三者齐全才算启用。"""
        return bool(self.endpoint and self.api_key and self.model)

    @property
    def _url(self) -> str:
        return _resolve_url(self.endpoint, CHAT_PATH)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(self.timeout))

    def _too_large(self, data: bytes) -> bool:
        return len(data) > self.max_bytes

    async def describe(self, data: bytes, mime: str = "image/jpeg") -> str:
        """把一张图片转成中文描述；失败返回空字符串，不抛异常。"""
        self.last_reason = ""
        if not self.enabled:
            self.last_reason = "disabled"
            return ""
        if not data:
            self.last_reason = "empty"
            return ""
        if self._too_large(data):
            self.last_reason = "too_large"
            logger.warning(
                "图片过大（%.1f MB > %.1f MB），跳过识别",
                len(data) / 1024 / 1024,
                self.max_bytes / 1024 / 1024,
            )
            return ""

        mime = (mime or "image/jpeg").split(";")[0].strip() or "image/jpeg"
        encoded = base64.b64encode(data).decode("ascii")
        payload = {
            "model": self.model,
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 600,
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_USER_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{encoded}"},
                        },
                    ],
                },
            ],
        }

        try:
            async with self._client() as client:
                response = await client.post(
                    self._url, headers=self._headers, json=payload
                )
                if response.status_code != 200:
                    self.last_reason = f"http_{response.status_code}"
                    logger.warning(
                        "图片识别失败：HTTP %s %s",
                        response.status_code,
                        (response.text or "")[:160],
                    )
                    return ""
                body = response.json()
        except Exception as exc:  # noqa: BLE001 - 视觉链路只是增强，不能影响主流程
            self.last_reason = "request_error"
            logger.warning("图片识别请求异常：%s", exc)
            return ""

        text = self._extract_text(body)
        if not text:
            self.last_reason = "empty_answer"
            logger.warning("图片识别返回空内容：%s", str(body)[:160])
            return ""
        self.last_reason = "ok"
        return text

    def _extract_text(self, body: dict) -> str:
        """从响应里取出描述正文，兼容字符串与分段两种 content 形态。"""
        try:
            choices = body.get("choices") or []
            message = (choices[0] or {}).get("message") or {}
            content = message.get("content")
        except (AttributeError, IndexError, TypeError):
            return ""
        if isinstance(content, list):
            parts = [
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") in ("text", "output_text")
            ]
            content = "".join(parts)
        text = str(content or "").strip()
        if not text:
            return ""
        # 去掉模型偶尔加的引号/前后缀，控制长度避免污染上下文
        text = text.strip().strip('"').strip("“”").strip()
        if len(text) > self.max_chars:
            text = text[: self.max_chars].rstrip() + "…"
        return text
