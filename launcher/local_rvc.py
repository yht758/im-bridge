"""Own the local bridge and a reconnecting SSH reverse forward."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import config_store
import remote
from rvc_bridge import make_server

sys.path.insert(0, str(config_store.ROOT / "src"))
from im_bridge.core.rvc_voice import RvcVoiceConverter
from im_bridge.core.voice_registry import VoiceRegistry
from im_bridge.core.external_process import start_external, clean_environment

PORT = 9882
ENDPOINT = f"http://127.0.0.1:{PORT}"


class LocalTts:
    """Direct local GPT-SoVITS; never uses a cloud base voice."""
    def is_available(self):
        try:
            with self._opener().open("http://127.0.0.1:9880/health", timeout=2) as response:
                return json.loads(response.read()).get("status") == "ok"
        except Exception:
            return False

    def _opener(self):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def synthesize(self, text):
        request = urllib.request.Request("http://127.0.0.1:9880/tts", headers={"Content-Type": "application/json"},
            data=json.dumps({"text": text, "text_language": "zh", "top_k": 20, "top_p": .6, "temperature": .6}).encode())
        with self._opener().open(request, timeout=180) as response:
            return response.read(5 * 1024 * 1024 + 1)


def cloud_health(endpoint: str, token: str, cfg=None) -> dict:
    # Token goes over stdin, never in the SSH process command line.
    code = '''import sys,json,urllib.request
p=json.load(sys.stdin)
try:
 r=urllib.request.Request(p["endpoint"]+"/health",headers={"Authorization":"Bearer "+p["token"]})
 with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(r,timeout=3) as f: print(f.read().decode())
except Exception as e: print(json.dumps({"ready":False,"error":type(e).__name__}))
'''
    import shlex
    rc, output = remote.run("python3 -c " + shlex.quote(code), timeout=15,
                            stdin_text=json.dumps({"endpoint": endpoint, "token": token}), cfg=cfg)
    if rc:
        return {"ready": False, "error": "SSH 检查失败"}
    try:
        return json.loads(output.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ready": False, "error": "云端返回异常"}


class LocalRvcSession:
    def __init__(self):
        self.server = None
        self.process = None
        self.worker = None
        self.stop_event = threading.Event()
        self.token = ""
        self.cfg = None
        self.status = "尚未连接；离线时只发文字"
        self.model_process = None
        self.model_log = None
        self.voice_mode = "local_tts"

    def start(self, values: dict, cfg: dict) -> str:
        if self.worker and self.worker.is_alive():
            raise RuntimeError("本地语音已连接；更改模型或服务器前请先断开")
        if not 0 <= float(values.get("rvc_index_rate", .75)) <= 1:
            raise ValueError("索引强度应在 0~1 之间")
        if values.get("rvc_f0_method", "rmvpe") not in ("rmvpe", "pm"):
            raise ValueError("音高算法应为 rmvpe 或 pm")
        self.stop_event.clear()
        self.voice_mode = values.get("voice_mode", "local_tts")
        if values.get("voice_mode", "local_tts") == "local_tts":
            converter = LocalTts()
            if not converter.is_available():
                python = Path(values["local_tts_python"])
                script = Path(values["local_tts_script"])
                if not python.is_file() or not script.is_file():
                    raise RuntimeError("请在高级设置中填写本地 GPT-SoVITS 解释器和 API 脚本路径")
                self.status = "正在加载本地 GPT-SoVITS（首次约需 30~60 秒）…"
                log_path = config_store.ROOT / "data" / "local-voice.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                self.model_log = log_path.open("a", encoding="utf-8")
                self.model_process = start_external(
                    [str(python), str(script), "--host", "127.0.0.1", "--port", "9880"],
                    cwd=str(script.parent), stdout=self.model_log, stderr=self.model_log,
                    env={**clean_environment(), "is_half": "False"},
                )
                for _ in range(150):
                    if converter.is_available():
                        break
                    if self.stop_event.wait(1) or self.model_process.poll() is not None:
                        self.stop()
                        raise RuntimeError("本地语音模型启动失败，请查看 data/local-voice.log")
                else:
                    self.stop()
                    raise RuntimeError("本地模型加载超时，请查看 data/local-voice.log")
        else:
            if values.get("voice_mode") == "cloud_tts":
                raise RuntimeError("云端语音不需要连接本机，请直接保存语音设置")
            converter = self._rvc_converter(values)
        try:
            return self._connect(converter, cfg)
        except Exception:
            self.stop()
            raise

    def _rvc_converter(self, values):
        return RvcVoiceConverter(
            VoiceRegistry(config_store.ROOT / "data" / "voices"),
            convert_script=values["rvc_convert_script"], python=values["rvc_python"],
            default_voice=values.get("rvc_default_voice") or "aemeath",
            index_rate=float(values.get("rvc_index_rate", .75)),
            f0_method=values.get("rvc_f0_method", "rmvpe"),
            pitch=int(values.get("rvc_pitch", 0)), device=values.get("rvc_device", "auto"),
        )
    def _connect(self, converter, cfg):
        if not converter.is_available():
            raise RuntimeError("本地 RVC 文件未就绪，请检查解释器、转换脚本、模型和索引")
        self.cfg = dict(cfg)
        base = remote._base_cmd(self.cfg)
        token_path = config_store.ROOT / "data" / "rvc_bridge.token"
        token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token = token_path.read_text("utf-8").strip() if token_path.is_file() else ""
        if len(self.token) < 32:
            self.token = secrets.token_urlsafe(32)
            token_path.write_text(self.token, encoding="utf-8")
        self.server = make_server(converter, self.token, PORT)
        self.stop_event.clear()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.command = base[:-1] + ["-N", "-T", "-o", "ExitOnForwardFailure=yes",
                                    "-R", f"127.0.0.1:{PORT}:127.0.0.1:{PORT}", base[-1]]
        self.status = "本地文件就绪，正在连接云端…"
        self.worker = threading.Thread(target=self._loop, daemon=True)
        self.worker.start()
        # Initial readiness is checked from the server, rather than inferred from Popen.
        for _ in range(15):
            if self.stop_event.wait(1):
                break
            if self.status.startswith("云端已连接"):
                return self.token
        error = self.status
        self.stop()
        raise RuntimeError(error if "失败" in error else "连接未就绪，请检查 SSH 或端口 9882 是否被占用")

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                with open(os.devnull, "w") as sink:
                    self.process = subprocess.Popen(
                        self.command, stdin=subprocess.DEVNULL, stdout=sink, stderr=sink,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    last_probe = 0.0
                    while not self.stop_event.wait(1):
                        if self.process.poll() is not None:
                            self.status = "SSH 隧道断开，正在重连；此时只发文字"
                            break
                        if time.monotonic() - last_probe < 30:
                            continue
                        result = cloud_health(ENDPOINT, self.token, self.cfg)
                        last_probe = time.monotonic()
                        if result.get("ready"):
                            mode = "本机直接合成" if self.voice_mode == "local_tts" else "本机 RVC 变声"
                            self.status = "云端已连接 · " + ("本机正在合成语音…" if result.get("busy") else mode)
                        else:
                            self.status = "云端暂不可达，正在检查；此时只发文字"
            except Exception as exc:
                self.status = f"连接失败（{type(exc).__name__}），正在重连"
            if self.stop_event.wait(5):
                break

    def stop(self):
        self.stop_event.set()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=20)
        if self.model_process is not None and self.model_process.poll() is None:
            self.model_process.terminate()
            try:
                self.model_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.model_process.kill()
        self.model_process = None
        if self.model_log:
            self.model_log.close()
            self.model_log = None
        self.status = "已断开；云端只发文字"

    def snapshot(self):
        return self.status
