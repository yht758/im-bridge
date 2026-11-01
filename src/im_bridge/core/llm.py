"""
LLM 客户端 —— 对接任意 OpenAI 兼容 API（DeepSeek / OpenAI / 本地推理服务等）。

能力：
- ``complete``：一次性补全（内部带指数退避重试）；
- ``stream``：SSE 流式补全，逐 delta 产出文本；
- ``summarize``：把旧对话压缩成摘要（用于 Summary Buffer）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import httpx

logger = logging.getLogger("im_bridge.llm")

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
NON_RETRYABLE_CLIENT_STATUS = {400, 401, 403, 404, 422}

SUMMARY_SYSTEM_PROMPT = (
    "你是对话记忆压缩器。请把聊天记录压缩成一段不超过 80 字的中文摘要，"
    "保留：用户身份与关系、关键事实、偏好、约定和未完成的话题。"
    "不要编造，不要评价，不要使用 Markdown，只输出摘要正文。"
)


def _as_token_count(value) -> int:
    """把 usage 字段转成非负整数，缺失/脏数据一律按 0。"""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _cached_from_usage(usage: dict, prompt: int) -> int:
    """取缓存命中的 prompt token 数。

    DeepSeek 用 ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``
    （且 ``prompt_tokens == hit + miss``）；命中价与未命中价差一个数量级，
    所以 cached 必须单独记，不能混进 prompt。
    """
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is not None:
        return _as_token_count(hit)
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is not None:
        return max(0, prompt - _as_token_count(miss))
    return _as_token_count(usage.get("cached_tokens"))


def _is_stream_options_rejected(message: str) -> bool:
    """判断错误是不是「服务端不认 stream_options」。"""
    lowered = message.lower()
    if "http 400" not in lowered and "http 422" not in lowered:
        return False
    if "stream_options" in lowered:
        return True
    return "unknown" in lowered or "invalid" in lowered


class LLMError(Exception):
    """LLM 请求失败（网络、协议、空响应等）。"""


class LLMClient:
    """OpenAI 兼容的异步 LLM 客户端。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60,
        max_retries: int = 3,
        temperature: float = 0.9,
        max_tokens: int = 600,
        usage_tracker=None,
        usage_enabled: bool = True,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.temperature = temperature
        self.max_tokens = max_tokens
        # 用量记账是可选旁路：tracker 为 None 或 usage_enabled=False 时完全不做任何事
        self.usage_tracker = usage_tracker
        self.usage_enabled = bool(usage_enabled)

    # ---------- 内部工具 ----------
    @property
    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        messages: list[dict],
        *,
        stream: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        return {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": stream,
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(self.timeout))

    # ---------- 用量记账 ----------
    def _record_usage(
        self,
        usage,
        *,
        source: str = "chat",
        model: str | None = None,
    ) -> None:
        """把一次调用的 usage 交给 tracker。

        usage 缺失/不是对象时按 0 记一条（保证调用次数仍可观测），不抛异常；
        任何记账异常都只 warn，绝不影响主流程。
        """
        if not self.usage_enabled or self.usage_tracker is None:
            return
        name = model or self.model
        try:
            if not isinstance(usage, dict):
                logger.debug("响应中没有 usage，按 0 记账：source=%s", source)
                self.usage_tracker.record(name, 0, 0, 0, source=source)
                return
            prompt = _as_token_count(usage.get("prompt_tokens"))
            completion = _as_token_count(usage.get("completion_tokens"))
            cached = _cached_from_usage(usage, prompt)
            self.usage_tracker.record(name, prompt, completion, cached, source=source)
        except Exception as exc:  # noqa: BLE001
            logger.warning("token 用量记账失败（已忽略）：%s", exc)

    # ---------- 非流式 ----------
    async def complete(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        source: str = "chat",
    ) -> str:
        """发送对话并返回完整回复，失败自动重试。"""
        last_error: Exception | None = None
        payload = self._payload(
            messages, stream=False, temperature=temperature, max_tokens=max_tokens
        )

        for attempt in range(self.max_retries):
            try:
                async with self._client() as client:
                    response = await client.post(
                        self._endpoint, headers=self._headers, json=payload
                    )

                    if response.status_code in RETRYABLE_STATUS:
                        raise LLMError(f"HTTP {response.status_code}")
                    if response.status_code in NON_RETRYABLE_CLIENT_STATUS:
                        raise LLMError(
                            f"HTTP {response.status_code}: {response.text[:200]}"
                        )
                    response.raise_for_status()

                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise LLMError(f"响应不是合法 JSON：{exc}") from exc
                    if not isinstance(data, dict):
                        raise LLMError("响应 JSON 不是对象")
                    choices = data.get("choices") or []
                    if not choices:
                        raise LLMError("响应中没有 choices")
                    content = (choices[0].get("message") or {}).get("content") or ""
                    text = content.strip()
                    if not text:
                        raise LLMError("模型返回了空回复")
                    response_model = data.get("model")
                    self._record_usage(
                        data.get("usage"),
                        source=source,
                        model=response_model
                        if isinstance(response_model, str) and response_model
                        else None,
                    )
                    return text

            except LLMError as exc:
                last_error = exc
                if "HTTP 4" in str(exc) and not str(exc).startswith("HTTP 429"):
                    raise
            except httpx.HTTPStatusError as exc:
                raise LLMError(
                    f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
                ) from exc
            except httpx.RequestError as exc:
                last_error = exc
            except (KeyError, ValueError, IndexError) as exc:
                last_error = exc

            if attempt < self.max_retries - 1:
                delay = 2**attempt
                logger.warning(
                    "LLM 请求失败（第 %d/%d 次），%ss 后重试：%s",
                    attempt + 1,
                    self.max_retries,
                    delay,
                    last_error,
                )
                await asyncio.sleep(delay)

        raise LLMError(
            f"LLM 请求在 {self.max_retries} 次尝试后仍失败：{last_error}"
        ) from last_error

    # ---------- 流式 ----------
    async def stream(
        self, messages: list[dict], *, source: str = "chat"
    ) -> AsyncIterator[str]:
        """SSE 流式补全，逐个 delta 产出文本片段。

        注意：一旦已经产出过内容，后续异常不会自动重试（避免重复发送），
        而是抛出 ``LLMError``，由调用方决定如何处理已收到的部分内容。
        流式只在正常收尾（收到 ``[DONE]`` 或迭代结束）时记账；拿不到 usage
        时按 0 记一条，中断的流不记（拿不到完整用量）。
        """
        last_error: Exception | None = None
        # 有些 OpenAI 兼容服务端不认 stream_options，被拒后自动去掉重试一次
        include_usage = True
        attempt = 0

        while attempt < self.max_retries:
            attempt += 1
            payload = self._payload(messages, stream=True)
            if include_usage:
                payload["stream_options"] = {"include_usage": True}
            yielded = False
            finished = False
            usage_data: dict | None = None

            try:
                async with self._client() as client:
                    async with client.stream(
                        "POST", self._endpoint, headers=self._headers, json=payload
                    ) as response:
                        if response.status_code in RETRYABLE_STATUS:
                            await response.aread()
                            raise LLMError(f"HTTP {response.status_code}")
                        if response.status_code >= 400:
                            body = (await response.aread()).decode("utf-8", "replace")[:200]
                            raise LLMError(f"HTTP {response.status_code}: {body}")

                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line or not line.startswith("data:"):
                                continue
                            data_text = line[5:].strip()
                            if data_text == "[DONE]":
                                finished = True
                                break
                            try:
                                data = json.loads(data_text)
                            except ValueError:
                                continue
                            if not isinstance(data, dict):
                                continue
                            # 先取 usage，再看 choices：DeepSeek 把 usage 挂在
                            # 「最后一个内容块」上（choices 长度为 1、finish_reason 非空），
                            # 若按 OpenAI 的「空 choices + usage」写法会永远拿不到。
                            chunk_usage = data.get("usage")
                            if isinstance(chunk_usage, dict) and chunk_usage:
                                usage_data = chunk_usage  # 覆盖式保存，最后一个有效
                            choices = data.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            content = delta.get("content")
                            if content:
                                yielded = True
                                yield content
                            # 注意：不能因为 finish_reason 非空就 break/return，
                            # 必须让 aiter_lines 自然走完（或读到 [DONE]），
                            # 否则最后那个带 usage 的块会被跳过。

                        # 流正常收尾才记账（中断的流拿不到完整用量）
                        if yielded or finished:
                            self._record_usage(usage_data, source=source)
                            return
                        last_error = LLMError("流式响应为空")

            except LLMError as exc:
                if yielded:
                    raise
                if include_usage and _is_stream_options_rejected(str(exc)):
                    include_usage = False
                    attempt -= 1  # 这次回退不消耗重试预算
                    logger.warning(
                        "服务端不支持 stream_options，已移除该字段重试：%s", exc
                    )
                    continue
                last_error = exc
                if "HTTP 4" in str(exc) and not str(exc).startswith("HTTP 429"):
                    raise
            except httpx.RequestError as exc:
                if yielded:
                    raise LLMError(f"流式传输中断：{exc}") from exc
                last_error = exc
            except (KeyError, ValueError, IndexError) as exc:
                if yielded:
                    raise LLMError(f"流式响应解析失败：{exc}") from exc
                last_error = exc

            if attempt < self.max_retries:
                delay = 2**attempt
                logger.warning(
                    "LLM 流式请求失败（第 %d/%d 次），%ss 后重试：%s",
                    attempt,
                    self.max_retries,
                    delay,
                    last_error,
                )
                await asyncio.sleep(delay)

        raise LLMError(
            f"LLM 流式请求在 {self.max_retries} 次尝试后仍失败：{last_error}"
        ) from last_error

    # ---------- 摘要 ----------
    async def summarize(self, existing_summary: str, transcript: str) -> str:
        """把旧对话（可叠加已有摘要）压缩成一段新摘要。"""
        parts: list[str] = []
        if existing_summary.strip():
            parts.append(f"已有摘要：\n{existing_summary.strip()}")
        if transcript.strip():
            parts.append(f"新增对话：\n{transcript.strip()}")
        if not parts:
            return existing_summary.strip()

        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "\n\n".join(parts)
                + "\n\n请输出合并后的摘要正文（不超过 80 字）。",
            },
        ]
        text = await self.complete(
            messages, temperature=0.2, max_tokens=220, source="summary"
        )
        return text.strip().strip("“”\"'").strip()
