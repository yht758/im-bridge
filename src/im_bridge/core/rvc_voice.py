"""
RVC 变声后端 —— 把「基础音色音频」转成注册音色。

实现方式：通过子进程调用独立的 rvcenv 运行 ``aemeath_vc/convert.py``，
与 bot 主进程（可能是 Py3.10 / Py3.14）完全隔离，避免把 torch cu128 等重依赖
塞进 bot 运行时。

可用性检测（is_available）是本功能的关键：当本机没开（D:/AemeathVoice 路径不存在 /
rvcenv 不存在 / 默认音色模型不存在）时返回 False，引擎据此回退到云 TTS 或静默，
恰好对应「电脑没开就用云端的，开了就用本地训练的」。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import urllib.request
from pathlib import Path

from .voice_registry import VoiceRegistry
from .external_process import run_external

logger = logging.getLogger("im_bridge.rvc_voice")


class RvcVoiceConverter:
    """本地 RVC 变声转换器（子进程隔离）。"""

    def __init__(
        self,
        registry: VoiceRegistry,
        convert_script: str = "",
        python: str = "",
        default_voice: str = "aemeath",
        index_rate: float = 0.75,
        f0_method: str = "rmvpe",
        pitch: int = 0,
        device: str = "auto",
        endpoint: str = "",
        api_key: str = "",
    ) -> None:
        self.registry = registry
        self.convert_script = Path(convert_script)
        self.python = Path(python)
        self.default_voice = default_voice
        self.index_rate = index_rate
        self.f0_method = f0_method
        self.pitch = pitch
        self.device = (device or "auto").strip().lower()
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key

    def _request(self, route: str, payload=None, timeout=3):
        request = urllib.request.Request(
            self.endpoint + route,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        # SSH reverse forwards are loopback-only; never send audio to a system proxy.
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout)

    # ---------- 可用性 ----------
    def is_available(self) -> bool:
        """本机 RVC 引擎是否就绪（用于决定走本地变声还是回退）。"""
        if self.endpoint:
            try:
                with self._request("/health") as response:
                    return json.loads(response.read(4096)).get("ready") is True
            except Exception:
                return False
        if not self.convert_script.is_file():
            logger.debug("RVC 不可用：转换脚本缺失 %s", self.convert_script)
            return False
        if not self.python.is_file():
            logger.debug("RVC 不可用：解释器缺失 %s", self.python)
            return False
        default = self.registry.get(self.default_voice)
        if default is None:
            logger.debug("RVC 不可用：默认音色 %s 未注册", self.default_voice)
            return False
        if not default.enabled or not default.model_path.is_file() or not default.index_path.is_file():
            logger.debug("RVC 不可用：默认音色模型缺失 %s", default.model_path)
            return False
        return True

    # ---------- 转换 ----------
    def convert(self, base_audio: Path, voice_id: str | None = None) -> Path | None:
        """把 base_audio 转成目标音色，返回输出 wav 路径；失败返回 None。"""
        if self.endpoint:
            try:
                audio = base_audio.read_bytes()
                if len(audio) > 5 * 1024 * 1024:
                    return None
                with self._request("/convert", {
                    "audio": base64.b64encode(audio).decode("ascii"),
                    "voice": voice_id or self.registry.default_id(),
                }, timeout=310) as response:
                    data = response.read(5 * 1024 * 1024 + 1)
                if not data.startswith(b"RIFF") or len(data) > 5 * 1024 * 1024:
                    return None
                out = base_audio.with_suffix(".rvc.wav")
                out.write_bytes(data)
                return out
            except Exception as exc:
                logger.warning("RVC 桥接不可用：%s", type(exc).__name__)
                return None
        voice = self.registry.get(voice_id or self.default_voice)
        if voice is None:
            logger.warning("RVC 转换跳过：音色不存在 %s", voice_id)
            return None
        if not voice.enabled:
            logger.warning("RVC 转换跳过：音色已禁用 %s", voice.id)
            return None
        if not voice.model_path.is_file():
            logger.warning("RVC 转换失败：模型缺失 %s", voice.model_path)
            return None
        if not voice.index_path.is_file():
            logger.warning("RVC 转换失败：索引缺失 %s", voice.index_path)
            return None

        out = base_audio.parent / (base_audio.stem + ".rvc.wav")
        cmd = [
            str(self.python),
            str(self.convert_script),
            "-i", str(base_audio),
            "-o", str(out),
            "--model", str(voice.model_path),
            "--index", str(voice.index_path),
            "--index-rate", str(self.index_rate),
            "--f0-method", self.f0_method,
            "--pitch", str(self.pitch),
        ]
        if self.device == "cpu":
            cmd.append("--cpu")
        try:
            logger.info("[RVC] 转换中：%s -> %s（音色 %s）", base_audio.name, out.name, voice.id)
            proc = run_external(
                cmd,
                cwd=str(self.convert_script.resolve().parent.parent),  # rvc 根目录
                timeout=300,
            )
            if proc.returncode != 0:
                logger.warning(
                    "RVC 转换失败（退出码 %s）：%s", proc.returncode,
                    (proc.stderr or proc.stdout or "").strip()[-500:],
                )
                return None
            if not out.is_file():
                logger.warning("RVC 转换失败：未生成输出 %s", out)
                return None
            return out
        except subprocess.TimeoutExpired:
            logger.warning("RVC 转换超时（>300s）")
            return None
        except Exception as exc:  # noqa: BLE001 - 变声失败不应拖垮主流程
            logger.warning("RVC 转换异常：%s", type(exc).__name__)
            return None

    async def convert_async(self, base_audio: Path, voice_id: str | None = None) -> Path | None:
        """异步包装，避免阻塞事件循环。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.convert, base_audio, voice_id)
