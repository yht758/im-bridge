"""
聊天引擎 —— 平台无关的核心编排。

负责把「上下文管理 + LLM 调用 + 拟真分片发送 + 指令系统」串起来：

    用户消息 -> 准入判断 -> 指令？ -> 上下文压缩 -> 构建 messages
             -> LLM 流式生成 -> 按标点分片 + 随机间隔发送 -> 记录回复

平台接入层只需实现 ``MessageTransport.send_text`` 并调用 ``handle_message``。
"""
from __future__ import annotations

import re

import asyncio
import base64
import logging
import random
import tempfile
from datetime import datetime
from pathlib import Path

from ..config import Config
from ..log_writer import append_log
from ..personas import Persona
from ..ports import MessageTransport
from .chunker import ChunkStreamer, boundary_of, next_delay, split_chunks
from .commands import CommandResult, CommandRouter
from .context import ContextManager
from .llm import LLMClient, LLMError
from .state import StateManager
from .stickers import StickerService
from .voice import SpeechSynthesizer
from .rvc_voice import RvcVoiceConverter
from .voice_registry import VoiceRegistry

logger = logging.getLogger("im_bridge.engine")

# 用户可见的兜底文案（不暴露异常原文）
LLM_FALLBACK_TEXT = "我刚刚没想好，稍等再试一次哦~"
GENERIC_FALLBACK_TEXT = "刚刚有点卡，稍后再试~"


class ChatEngine:
    """把消息变成回复的编排器。"""

    def __init__(
        self,
        config: Config,
        llm: LLMClient,
        context_manager: ContextManager,
        state_manager: StateManager,
        transport: MessageTransport,
        persona: Persona,
        synthesizer: SpeechSynthesizer | None = None,
        stickers: StickerService | None = None,
        rvc_converter: RvcVoiceConverter | None = None,
        voice_registry: VoiceRegistry | None = None,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.context = context_manager
        self.state = state_manager
        self.transport = transport
        self.persona = persona
        self.synthesizer = synthesizer
        self.stickers = stickers
        self.rvc_converter = rvc_converter
        self.voice_registry = voice_registry
        self.logger = logger_ or logger
        self.started_at = datetime.now()
        self.commands = CommandRouter(
            config, state_manager, context_manager, self.started_at,
            voice_registry=self.voice_registry,
        )
        self._rng = random.Random()

    # ---------- 入口 ----------
    async def handle_message(self, user_id: str, nickname: str, text: str) -> bool:
        """处理一条私聊消息；返回 True 表示请求停止。"""
        user_id = str(user_id)
        text = (text or "").strip()
        if not text:
            return False
        if not self.config.is_allowed(user_id):
            self.logger.info("忽略非白名单用户 %s", user_id)
            return False

        try:
            uid = int(user_id)
        except ValueError:
            self.logger.warning("非法 user_id：%r", user_id)
            return False

        append_log("questions.log", f"[{nickname}({user_id})] {text}")
        self.state.record_interaction(user_id, nickname)

        # 1) 指令：不分片，一次发完
        if self.commands.is_command(text):
            result: CommandResult = await self.commands.handle(user_id, text)
            if result.reply:
                append_log("answers.log", f"[{nickname}({user_id})] {result.reply}")
                self.logger.info("[回复->%s]: %s", nickname or user_id, result.reply[:80])
                await self._safe_send(uid, result.reply)
            return result.stop

        # 2) 普通聊天：先压缩旧历史，再追加当前用户消息
        try:
            await self.context.maybe_compress(user_id, self.llm)
            self.context.append_user(user_id, text)
            messages = self.context.build_messages(user_id, self._system_prompt())

            self.logger.info("[%s(%s)]: %s", nickname or "用户", user_id, text)
            reply = await self._reply(uid, messages)

            self.context.append_assistant(user_id, reply)
            append_log("answers.log", f"[{nickname}({user_id})] {reply}")
            self.logger.info("[回复->%s]: %s", nickname or user_id, reply[:80])
            await self._maybe_send_voice(uid, messages, reply)
            return False

        except LLMError as exc:
            self.logger.error("LLM 调用失败：%s", exc)
            append_log("status.log", f"[运行] LLM 请求失败：{exc}")
            await self._safe_send(uid, LLM_FALLBACK_TEXT)
            return False
        except Exception as exc:  # noqa: BLE001 - 单条消息失败不能拖垮整个进程
            self.logger.exception("处理消息出错：%s", exc)
            append_log("status.log", f"[运行] 处理消息出错：{exc}")
            await self._safe_send(uid, GENERIC_FALLBACK_TEXT)
            return False

    async def _safe_send(self, user_id: int, text: str) -> None:
        """发送兜底消息；发送通道本身异常时只记录日志，避免二次崩溃。"""
        try:
            await self.transport.send_text(user_id, text)
        except Exception as exc:  # noqa: BLE001
            self.logger.error("发送消息失败：%s", exc)

    def _system_prompt(self) -> str:
        """人设提示词 + 可选的表情包规则（没有素材时规则为空）。"""
        prompt = self.persona.system_prompt
        if self.stickers is not None and self.stickers.enabled:
            rules = self.stickers.rules_prompt
            if rules:
                prompt = prompt + rules
        return prompt

    @staticmethod
    def _wants_sticker(messages: list[dict]) -> bool:
        """用户这句话是不是在直接索要表情包（要了就必发）。"""
        pattern = re.compile(r"表情包|表情|斗图|梗图|贴图|发.{0,3}图|来.{0,2}图")
        for message in reversed(messages or []):
            if str(message.get("role") or "") != "user":
                continue
            return bool(pattern.search(str(message.get("content") or "")))
        return False

    async def _maybe_send_sticker(self, user_id: int, tags: list[str], force: bool = False) -> None:
        """按概率/频控发一张表情；失败只记日志，绝不影响文字回复。"""
        if self.stickers is None or not self.stickers.enabled or not tags:
            return
        if not hasattr(self.transport, "send_image"):
            self.logger.warning("当前传输层不支持发送图片，跳过表情")
            return
        try:
            self.logger.info("[表情] 回复携带标签 %s（force=%s）", tags, force)
            encoded = self.stickers.choose(str(user_id), tags, force=force)
            if not encoded:
                self.logger.info("[表情] 未发送，原因：%s", self.stickers.last_reason or "unknown")
                return
            await self.transport.send_image(user_id, encoded)
            self.logger.info("[表情->%s] 已发送（%.1f KB）", user_id, len(encoded) / 1024)
        except Exception as exc:  # noqa: BLE001 - 表情是锦上添花
            self.logger.warning("发送表情失败：%s", exc)

    async def _maybe_send_voice(
        self, user_id: int, messages: list[dict], reply: str
    ) -> None:
        """按概率补发一条语音：内容另生成，不重复文字回复。

        - 概率闸门在生成之前，避免白花一次 LLM 调用；
        - 语音文案结合上文单独生成，读出来的是「补一句」，不是把回复再念一遍；
        - 任何失败只记 warning，绝不影响已经发出去的文字。
        """
        if not self.config.voice_reply_enabled:
            return
        if self.synthesizer is None or not self.synthesizer.enabled:
            return
        if not hasattr(self.transport, "send_record"):
            self.logger.debug("当前传输层不支持发送语音，跳过语音回复")
            return
        if not (reply or "").strip():
            return

        probability = float(getattr(self.config, "voice_reply_probability", 0.0) or 0.0)
        if probability <= 0:
            return
        if probability < 1.0 and random.random() > probability:
            self.logger.info("[语音] 本次未命中概率（p=%.2f）", probability)
            return

        voice_mode = getattr(self.config, "voice_mode", "rvc")
        rvc_ready = False
        if self.rvc_converter is not None and getattr(self.config, "rvc_enabled", False):
            rvc_ready = await asyncio.to_thread(self.rvc_converter.is_available)
        fallback = (getattr(self.config, "rvc_fallback", "off") or "off").lower()
        if voice_mode != "cloud_tts" and not rvc_ready and fallback != "cloud":
            self.logger.info("[语音] 本地 RVC 离线或已关闭，只发文字")
            return

        line = await self._generate_voice_line(messages, reply)
        if not line:
            return

        try:
            with tempfile.TemporaryDirectory(prefix="im-bridge-voice-") as tmp:
                out = Path(tmp) / "reply.mp3"
                path = await self.synthesizer.synthesize(line, out)
                if path is None:
                    return
                base_data = path.read_bytes()
                if len(base_data) > 5 * 1024 * 1024:
                    self.logger.warning("合成语音超过 5MB，放弃发送")
                    return

                # 本地 RVC 变声：本机引擎可用时，把基础音色转成注册音色
                final_path = path
                used_rvc = False
                if voice_mode == "rvc" and self.rvc_converter is not None and getattr(self.config, "rvc_enabled", False):
                    if rvc_ready:
                        converted = await self.rvc_converter.convert_async(path, None)
                        if converted is not None:
                            final_path = converted
                            used_rvc = True
                        else:
                            self.logger.warning("[语音] RVC 转换失败，按回退策略处理")
                    else:
                        self.logger.info(
                            "[语音] 本机 RVC 不可用（电脑可能未开），回退：%s",
                            getattr(self.config, "rvc_fallback", "off"),
                        )

                # 回退策略：未使用 RVC 且回退为 off 时，本次不发语音
                fallback = (getattr(self.config, "rvc_fallback", "off") or "off").lower()
                if voice_mode == "rvc" and not used_rvc and fallback != "cloud":
                    self.logger.info("[语音] 回退策略为 off，本次不发语音")
                    return

                data = final_path.read_bytes()
                encoded = "base64://" + base64.b64encode(data).decode("ascii")
                await self.transport.send_record(user_id, encoded)
                self.logger.info(
                    "[语音->%s] %s（%.1f KB，RVC=%s）",
                    user_id, line[:20], len(data) / 1024, used_rvc,
                )
        except Exception as exc:  # noqa: BLE001 - 语音是锦上添花，失败不影响文字
            self.logger.warning("发送语音失败：%s", exc)

    async def _generate_voice_line(self, messages: list[dict], reply: str) -> str:
        """结合上下文另写一句语音文案，明确要求不复述刚才的文字回复。"""
        system_prompt = (
            f"{self._system_prompt()}\n\n---\n"
            "你现在要补发一条【语音消息】。这句话会被念出来，所以：\n"
            "1. 不要重复、也不要改写你刚发的那段文字，换一个角度补一句；\n"
            "2. 顺着当前聊天内容说，具体、自然，像真人随口补的一句；\n"
            "3. 不超过 30 个字，不带表情符号、不带 Markdown、不用引号；\n"
            "4. 只输出这句话本身。"
        )
        history = list(messages or [])
        if history and str(history[0].get("role")) == "system":
            history = history[1:]
        convo = list(history)
        if reply.strip():
            convo.append({"role": "assistant", "content": reply.strip()})
        convo.append({"role": "user", "content": "（补一条语音，别重复上面的话。）"})
        try:
            raw = await self.llm.complete([{"role": "system", "content": system_prompt}, *convo])
        except LLMError as exc:
            self.logger.warning("生成语音文案失败：%s", exc)
            return ""
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("生成语音文案异常：%s", exc)
            return ""

        cleaned = (raw or "").strip().splitlines()[0].strip().strip("“”\"'「」『』 ")
        if len(cleaned) > 30:
            cleaned = cleaned[:30].rstrip("，,、；;：:。.!！?？ ")
        return cleaned

    # ---------- 回复生成 ----------
    async def _reply(self, user_id: int, messages: list[dict]) -> str:
        """生成并发送回复，返回完整回复文本（已剥离表情标记，用于写入上下文）。"""
        sent = 0
        tag_filter = (
            self.stickers.new_filter()
            if self.stickers is not None and self.stickers.enabled
            else None
        )

        if self.config.streaming_enabled:
            full: list[str] = []
            streamer = ChunkStreamer(max_segments=self.config.chunk_max_segments)
            stream_failed = False
            try:
                async for delta in self.llm.stream(messages):
                    if tag_filter is not None:
                        delta = tag_filter.feed(delta)
                    if not delta:
                        continue
                    full.append(delta)
                    if self.config.chunking_enabled:
                        for chunk in streamer.feed(delta):
                            sent += await self._send_piece(user_id, chunk, sent)
            except LLMError as exc:
                stream_failed = True
                self.logger.warning("流式生成中断：%s", exc)

            if tag_filter is not None:
                tail = tag_filter.flush()
                if tail:
                    full.append(tail)
                    if self.config.chunking_enabled:
                        for chunk in streamer.feed(tail):
                            sent += await self._send_piece(user_id, chunk, sent)

            reply = "".join(full).strip()
            if reply:
                if self.config.chunking_enabled:
                    for chunk in streamer.flush():
                        sent += await self._send_piece(user_id, chunk, sent)
                else:
                    # 关闭分片：流式只用于降低首字延迟，内容按平台上限拆条发送
                    sent += await self._send_piece(user_id, reply, sent)
                await self._maybe_send_sticker(
                    user_id,
                    tag_filter.tags if tag_filter is not None else [],
                    force=self._wants_sticker(messages),
                )
                return reply

            # 没有任何内容：可能是端点不支持 SSE，回退为一次性补全
            self.logger.warning("流式未产出内容，回退一次性补全（stream_failed=%s）", stream_failed)

        raw = await self.llm.complete(messages)
        tags: list[str] = []
        reply = raw
        if self.stickers is not None and self.stickers.enabled:
            reply, tags = self.stickers.parse(raw)

        if self.config.chunking_enabled:
            for chunk in split_chunks(
                reply, max_segments=self.config.chunk_max_segments
            ):
                sent += await self._send_piece(user_id, chunk, sent)
        else:
            sent += await self._send_piece(user_id, reply, sent)
        await self._maybe_send_sticker(user_id, tags, force=self._wants_sticker(messages))
        return reply

    async def _send_piece(self, user_id: int, text: str, sent_count: int) -> int:
        """发送一个分片（必要时按平台长度上限再拆），返回实际发出的条数。"""
        text = (text or "").strip()
        if not text:
            return 0

        count = 0
        for piece in self._limit_pieces(text):
            piece = piece.strip()
            if not piece:
                continue
            if sent_count + count > 0:
                # 句末停顿最久、逗号最短，硬切几乎不停 —— 更像真人喘气
                delay = next_delay(
                    self.config.chunk_min_delay,
                    self.config.chunk_max_delay,
                    self._rng,
                    boundary=boundary_of(piece),
                )
                if delay > 0:
                    await asyncio.sleep(delay)
            await self.transport.send_text(user_id, piece)
            count += 1
        return count

    def _limit_pieces(self, text: str) -> list[str]:
        """按 AMIS_MESSAGE_MAX_CHARS 拆条；已在上限内则原样返回。"""
        limit = int(self.config.message_max_chars or 0)
        if limit <= 0 or len(text) <= limit:
            return [text]
        pieces = split_chunks(text, min_len=1, max_len=limit, max_segments=0)
        result: list[str] = []
        for piece in pieces:
            if len(piece) <= limit:
                result.append(piece)
            else:
                # 保护区超长等极端情况：按硬长度兜底，保证一定能发出去
                result.extend(
                    piece[index : index + limit] for index in range(0, len(piece), limit)
                )
        return result or [text]
