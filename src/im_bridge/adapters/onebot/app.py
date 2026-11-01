"""
OneBot 适配器运行时 —— 事件循环、连接重连、单用户串行化。

只负责“接消息 / 发消息 / 连接管理”，业务编排全部交给 ``ChatEngine``。
本层额外承担三件事：
- 把语音消息转写成文字（Issue #5），转写失败则忽略该条消息；
- 把图片消息转成文字描述后交给引擎，图片自身不进上下文；
- 托管主动会话调度器的启停（Issue #4）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
from pathlib import Path

import httpx
import websockets

from ...config import Config
from ...core.engine import ChatEngine
from ...core.vision import ImageDescriber
from ...core.voice import VoiceTranscriber
from ...log_writer import append_log
from .transport import OneBotTransport

logger = logging.getLogger("im_bridge.onebot.app")

# 一条消息里所有图片的识别总预算（秒），超时剩余图片按失败处理
IMAGE_TOTAL_TIMEOUT = 60.0
# 下载图片的超时（秒）
IMAGE_DOWNLOAD_TIMEOUT = 20.0

CQ_IMAGE_RE = re.compile(r"\[CQ:(?:image|mface),([^\]]*)\]")
CQ_FILE_RE = re.compile(r"\[CQ:file,([^\]]*)\]")
CQ_ANY_RE = re.compile(r"\[CQ:[^\]]+\]")
IMAGE_SEGMENT_TYPES = ("image", "mface")


class OneBotApp:
    """OneBot v11 私聊机器人运行时。"""

    def __init__(
        self,
        config: Config,
        engine: ChatEngine,
        transport: OneBotTransport,
        transcriber: VoiceTranscriber | None = None,
        describer: ImageDescriber | None = None,
        proactive=None,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.transport = transport
        self.transcriber = transcriber
        self.describer = describer
        self.proactive = proactive
        self.logger = logger_ or logger
        self.processing: set[str] = set()  # 同一用户串行处理，避免并发串台
        self.tasks: set[asyncio.Task] = set()
        self.stop_event = asyncio.Event()
        self._retry_delay = 1

    async def _cancel_pending_tasks(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---------- 语音 ----------
    @staticmethod
    def _find_record(message) -> str:
        """从消息段或 CQ 码里找出语音文件的标识。"""
        if isinstance(message, str):
            start = message.find("[CQ:record,")
            if start < 0:
                return ""
            end = message.find("]", start)
            if end < 0:
                return ""
            segment = message[start + len("[CQ:record,") : end]
            for part in segment.split(","):
                key, _, value = part.partition("=")
                if key.strip() == "file":
                    return value.strip()
            return ""

        if isinstance(message, list):
            for segment in message:
                if not isinstance(segment, dict) or segment.get("type") != "record":
                    continue
                data = segment.get("data") or {}
                file = data.get("file") or data.get("url") or ""
                if file:
                    return str(file)
        return ""

    def _map_local_path(self, remote: str) -> Path | None:
        """把容器内路径映射成宿主机可读路径（NapCat 在 Docker 里时必须配）。"""
        mapping = (self.config.voice_path_map or "").strip()
        if mapping:
            for pair in mapping.split(","):
                if "=" not in pair:
                    continue
                prefix, _, local_root = pair.partition("=")
                prefix, local_root = prefix.strip(), local_root.strip()
                if prefix and local_root and remote.startswith(prefix):
                    candidate = Path(local_root + remote[len(prefix) :])
                    return candidate if candidate.exists() else None
        candidate = Path(remote)
        return candidate if candidate.exists() else None

    async def _transcribe(self, file: str) -> str:
        if self.transcriber is None or not self.transcriber.enabled:
            return ""
        try:
            info = await self.transport.call_api(
                "get_record", {"file": file, "out_format": "mp3"}
            )
        except Exception as exc:  # noqa: BLE001 - 取语音失败只影响这一条消息
            self.logger.warning("获取语音文件失败：%s", exc)
            return ""

        remote_path = str((info or {}).get("file") or "").strip()
        if not remote_path:
            self.logger.warning("get_record 未返回文件路径")
            return ""

        local = self._map_local_path(remote_path)
        if local is None:
            self.logger.warning(
                "语音文件无法映射到本机：%s（请检查 AMIS_VOICE_PATH_MAP）", remote_path
            )
            return ""

        text = await self.transcriber.transcribe(local)
        if text:
            self.logger.info("语音已转写：%s", text[:40])
            append_log("questions.log", f"[语音转写] {text[:80]}")
        return text or ""

    # ---------- 图片 ----------
    @staticmethod
    def _parse_cq_params(raw: str) -> dict[str, str]:
        """把 CQ 码里的 ``k=v,k=v`` 解析成字典，并还原常见转义。"""
        params: dict[str, str] = {}
        for part in raw.split(","):
            key, _, value = part.partition("=")
            key = key.strip()
            if not key:
                continue
            params[key] = (
                value.strip()
                .replace("&#44;", ",")
                .replace("&amp;", "&")
                .replace("&#91;", "[")
                .replace("&#93;", "]")
            )
        return params

    @classmethod
    def _find_images(cls, message) -> list[dict]:
        """从消息段数组或 CQ 码字符串里找出所有图片，返回 [{url, file}]。"""
        images: list[dict] = []
        if isinstance(message, str):
            for match in CQ_IMAGE_RE.finditer(message):
                params = cls._parse_cq_params(match.group(1))
                images.append(
                    {"url": params.get("url", ""), "file": params.get("file", "")}
                )
            return images

        if isinstance(message, list):
            for segment in message:
                if not isinstance(segment, dict):
                    continue
                if segment.get("type") not in IMAGE_SEGMENT_TYPES:
                    continue
                data = segment.get("data") or {}
                images.append(
                    {
                        "url": str(data.get("url") or ""),
                        "file": str(data.get("file") or ""),
                    }
                )
        return images

    @staticmethod
    def _find_files(message) -> list[dict]:
        """从消息段数组或 CQ 码字符串里找出所有文件段，返回 [{file_id, name}]。

        OneBot v11 文件段 ``type=="file"``，常见字段 ``file_id`` / ``name`` /
        ``url`` / ``size``（NapCat 私聊发文件即此形态）；部分实现也可能以
        ``[CQ:file,...]`` 字符串形式下发。
        """
        files: list[dict] = []
        if isinstance(message, str):
            for match in CQ_FILE_RE.finditer(message):
                params = OneBotApp._parse_cq_params(match.group(1))
                files.append(
                    {
                        "file_id": params.get("file_id") or params.get("file") or "",
                        "name": params.get("name") or "",
                    }
                )
            return files

        if isinstance(message, list):
            for segment in message:
                if not isinstance(segment, dict) or segment.get("type") != "file":
                    continue
                data = segment.get("data") or {}
                files.append(
                    {
                        "file_id": str(data.get("file_id") or data.get("file") or ""),
                        "name": str(data.get("name") or ""),
                    }
                )
        return files

    async def _download_file(self, file_id: str, name: str) -> Path | None:
        """通过 OneBot ``get_file`` API 把上传文件取到本机临时路径。"""
        if not file_id:
            return None
        try:
            info = await self.transport.call_api("get_file", {"file_id": file_id})
        except Exception as exc:  # noqa: BLE001 - 取文件失败只影响这一条上传
            self.logger.warning("get_file 调用失败：%s", exc)
            return None
        remote_path = str((info or {}).get("file") or "").strip()
        if not remote_path:
            self.logger.warning("get_file 未返回文件路径")
            return None
        local = self._map_local_path(remote_path)
        if local is None:
            self.logger.warning(
                "上传文件无法映射到本机：%s（请检查 AMIS_VOICE_PATH_MAP）", remote_path
            )
            return None
        try:
            import shutil
            import tempfile

            tmp = Path(tempfile.gettempdir()) / f"im-bridge-up-{file_id}-{name}"
            shutil.copy2(local, tmp)
            return tmp
        except OSError as exc:
            self.logger.warning("复制上传文件失败：%s", exc)
            return None

    async def _reply_text(self, user_id: str, text: str) -> None:
        """给上传流程回一条状态文本；发送失败只记日志。"""
        try:
            await self.transport.send_text(int(user_id), text)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("上传状态回复失败：%s", exc)

    async def _handle_voice_upload(self, user_id: str, files: list[dict]) -> None:
        """处理 /voice add 流程中收到的模型/索引文件，逐条下载并交给注册表。"""
        registry = getattr(self.engine, "voice_registry", None)
        if registry is None:
            return
        for f in files:
            name = (f.get("name") or "").strip()
            file_id = (f.get("file_id") or "").strip()
            low = name.lower()
            if low.endswith(".pth"):
                kind = "model"
            elif low.endswith(".index"):
                kind = "index"
            else:
                await self._reply_text(
                    user_id, f"「{name}」不是支持的音色文件（需要 .pth 或 .index）"
                )
                continue

            saved = await self._download_file(file_id, name)
            if saved is None:
                await self._reply_text(user_id, f"「{name}」下载失败，请重试")
                continue

            try:
                status, msg = registry.handle_upload_file(user_id, kind, saved)
            finally:
                # 删除临时副本，登记时注册表已另存到 voices/<id>/
                try:
                    saved.unlink()
                except OSError:
                    pass

            if status == "no_pending":
                await self._reply_text(
                    user_id, "你当前没有正在进行的音色添加，请先发送 /voice add <id> [名称]"
                )
                break
            elif status == "error":
                await self._reply_text(user_id, f"❌ {msg}")
            elif status == "need_more":
                await self._reply_text(user_id, f"✅ {msg}")
            elif status == "done":
                await self._reply_text(user_id, f"🎉 {msg}")

    @staticmethod
    def _clean_text(text: str) -> str:
        """去掉残留 CQ 码（表情、@、图片等），避免把机器码喂给模型。"""
        stripped = CQ_ANY_RE.sub(" ", text or "")
        return re.sub(r"\s+", " ", stripped).strip()

    @staticmethod
    def _guess_mime(name: str) -> str:
        mime, _ = mimetypes.guess_type(name or "")
        if mime and mime.startswith("image/"):
            return mime
        return "image/jpeg"

    async def _download_image(self, url: str) -> tuple[bytes, str] | None:
        """下载图片并校验体积；失败返回 None。"""
        limit = max(1024, int(self.config.vision_max_bytes))
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(IMAGE_DOWNLOAD_TIMEOUT), follow_redirects=True
            ) as client:
                async with client.stream(
                    "GET", url, headers={"User-Agent": "Mozilla/5.0"}
                ) as response:
                    if response.status_code != 200:
                        self.logger.warning(
                            "下载图片失败：HTTP %s", response.status_code
                        )
                        return None
                    mime = (response.headers.get("content-type") or "").split(";")[0].strip()
                    if not mime.startswith("image/"):
                        mime = self._guess_mime(url)
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > limit:
                            self.logger.warning(
                                "图片超过 %.1f MB，跳过识别", limit / 1024 / 1024
                            )
                            return None
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    return (data, mime) if data else None
        except Exception as exc:  # noqa: BLE001 - 下载失败只影响这一张图
            self.logger.warning("下载图片异常：%s", exc)
            return None

    async def _fetch_image(self, segment: dict) -> tuple[bytes, str] | None:
        """按 url → 本地路径 → get_image 的顺序取图片字节。"""
        url = (segment.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            got = await self._download_image(url)
            if got:
                return got

        file = (segment.get("file") or "").strip()
        if file:
            local = self._map_local_path(file)
            if local is not None:
                try:
                    return local.read_bytes(), self._guess_mime(local.name)
                except OSError as exc:
                    self.logger.warning("读取本地图片失败：%s", exc)

        if not file:
            return None
        try:
            info = await self.transport.call_api("get_image", {"file": file})
        except Exception as exc:  # noqa: BLE001 - 取图失败只影响这一条消息
            self.logger.warning("get_image 调用失败：%s", exc)
            return None

        remote_path = str((info or {}).get("file") or "").strip()
        if not remote_path:
            self.logger.warning("get_image 未返回文件路径")
            return None
        local = self._map_local_path(remote_path)
        if local is None:
            self.logger.warning(
                "图片无法映射到本机：%s（请检查 AMIS_VOICE_PATH_MAP）", remote_path
            )
            return None
        try:
            return local.read_bytes(), self._guess_mime(local.name)
        except OSError as exc:
            self.logger.warning("读取图片失败：%s", exc)
            return None

    async def _describe_images(self, segments: list[dict]) -> tuple[list[str], int]:
        """把图片逐张转成文字描述，返回（描述列表, 失败张数）。"""
        if not segments:
            return [], 0
        if self.describer is None or not self.describer.enabled:
            return [], len(segments)

        limit = max(1, int(self.config.image_max_count))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + IMAGE_TOTAL_TIMEOUT
        captions: list[str] = []
        failed = 0
        for index, segment in enumerate(segments[:limit], 1):
            if loop.time() > deadline:
                self.logger.warning("图片识别超时，剩余 %d 张跳过", len(segments) - index + 1)
                failed += len(segments) - index + 1
                break
            got = await self._fetch_image(segment)
            if not got:
                failed += 1
                continue
            caption = await self.describer.describe(got[0], got[1])
            if not caption:
                failed += 1
                continue
            captions.append(caption)
            self.logger.info("图片已识别（第 %d 张）：%s", index, caption[:60])
            append_log("questions.log", f"[图片识别] {caption[:120]}")
        if len(segments) > limit:
            failed += len(segments) - limit
        return captions, failed

    @staticmethod
    def _compose_with_images(text: str, captions: list[str], failed: int) -> str:
        """把图片描述拼进用户文本，让模型当正常聊天内容理解。"""
        lines: list[str] = []
        if captions:
            lines.append("[用户发来的图片内容]")
            for index, caption in enumerate(captions, 1):
                prefix = f"图{index}：" if len(captions) > 1 else ""
                lines.append(prefix + caption)
        if failed:
            lines.append(f"[另有 {failed} 张图片没能读取内容]")
        if not lines:
            return text
        block = "\n".join(lines)
        if text:
            return f"{text}\n\n{block}"
        return f"{block}\n（用户只发了图片，没有文字，请针对上面的内容自然回应。）"

    # ---------- 消息处理 ----------
    async def _handle_private_msg(self, data: dict) -> None:
        user_id = str(data.get("user_id", ""))
        message = data.get("message")
        raw_message = data.get("raw_message", message if isinstance(message, str) else "")
        nickname = str((data.get("sender") or {}).get("nickname", ""))

        if not isinstance(raw_message, str):
            raw_message = str(raw_message)

        # 0) 音色上传捕获：用户正处于 /voice add 流程时，优先接收私发的模型/索引文件
        registry = getattr(self.engine, "voice_registry", None)
        if registry is not None:
            pending = registry.get_pending(user_id)
            files = self._find_files(message)
            if pending is not None and files:
                await self._handle_voice_upload(user_id, files)
                return

        record = self._find_record(message if message is not None else raw_message)
        source = message if message is not None else raw_message
        if record:
            if not self.config.voice_input_enabled:
                self.logger.info("收到语音消息，但未开启 AMIS_VOICE_INPUT_ENABLED，已忽略")
                return
            text = await self._transcribe(record)
            if not text:
                return
        else:
            text = self._clean_text(raw_message)

        # 图片：先转成文字描述，再和文字一起交给引擎（纯图片消息也能触发回复）
        images = self._find_images(source)
        if images:
            if self.config.image_input_enabled:
                captions, failed = await self._describe_images(images)
                text = self._compose_with_images(text, captions, failed)
            else:
                note = f"[用户发来 {len(images)} 张图片，但图片识别未开启，你看不到图片内容]"
                text = f"{text}\n\n{note}" if text else note

        if not user_id or not text:
            return
        if not self.config.respond_to_groups and data.get("message_type") == "group":
            return
        if user_id in self.processing:
            return

        self.processing.add(user_id)
        try:
            should_stop = await self.engine.handle_message(user_id, nickname, text)
            if should_stop:
                self.stop_event.set()
                await self.transport.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单条消息异常不影响连接
            self.logger.exception("处理消息异常：%s", exc)
        finally:
            self.processing.discard(user_id)

    async def _dispatch(self, data: dict) -> None:
        post_type = data.get("post_type", "")
        message_type = data.get("message_type", "")
        if post_type == "message" and message_type == "private":
            task = asyncio.create_task(self._handle_private_msg(data))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def run(self) -> None:
        """主循环：连接 / 收事件 / 断线指数退避重连，直到收到停止请求。"""
        ws_url = self.config.napcat_ws_url
        self.logger.info("正在连接 OneBot WebSocket：%s", ws_url)
        append_log("status.log", f"[运行] 正在连接 OneBot WebSocket：{ws_url}")

        if self.proactive is not None:
            await self.proactive.start()

        try:
            while not self.stop_event.is_set():
                try:
                    await self.transport.connect()
                    self._retry_delay = 1
                    append_log("status.log", "[运行] 已连接 OneBot WebSocket")

                    async for raw in self.transport.ws:
                        try:
                            data = json.loads(raw)
                        except (ValueError, TypeError):
                            continue
                        if isinstance(data, dict):
                            # API 响应优先交给传输层匹配，其余才当事件分发
                            if self.transport.handle_response(data):
                                continue
                            await self._dispatch(data)
                        if self.stop_event.is_set():
                            break

                except (websockets.ConnectionClosed, OSError) as exc:
                    await self.transport.close()
                    await self._cancel_pending_tasks()
                    if self.stop_event.is_set():
                        break
                    self.logger.warning("连接断开：%s，%s 秒后重试", exc, self._retry_delay)
                    append_log(
                        "status.log",
                        f"[运行] 连接断开：{exc}，{self._retry_delay} 秒后重试",
                    )
                    await asyncio.sleep(self._retry_delay)
                    self._retry_delay = min(self._retry_delay * 2, 15)
                except asyncio.CancelledError:
                    await self.transport.close()
                    raise
                except Exception as exc:  # noqa: BLE001
                    await self.transport.close()
                    await self._cancel_pending_tasks()
                    if self.stop_event.is_set():
                        break
                    self.logger.error("运行出错：%s", exc)
                    append_log("status.log", f"[运行] 发生错误：{exc}")
                    await asyncio.sleep(5)
        finally:
            if self.proactive is not None:
                await self.proactive.stop()
            await self.engine.context.close()

        await self.transport.close()
        append_log("status.log", "[运行] 机器人已停止")
        self.logger.info("机器人已停止")
