#!/usr/bin/env python3
"""实例操作 CLI —— 供本地管理台通过 SSH 调用。

本地 GUI 不直接改远端文件，而是执行这里的命令：校验、写配置、更新注册表
全部复用服务端的 ``instance_config``，避免两端逻辑漂移。

用法：
    instance_cli.py list
    instance_cli.py get      <id>
    instance_cli.py create   <json>
    instance_cli.py update   <id> <json>
    instance_cli.py delete   <id> [--keep-backup]
    instance_cli.py config   <id>            # 读该实例 config.json
    instance_cli.py save     <id> <json>     # 写该实例 config.json（合并）

输出统一为单行 JSON：{"ok": true, ...} 或 {"ok": false, "error": "..."}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import instance_config as ic  # noqa: E402


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _probe_tts(endpoint: str, model: str, voice: str, key: str) -> dict:
    """真的合成一次，用来区分「配置错了」「隧道断了」「服务拒了」。

    只花一个「好」字的成本，但能一次性确认链路通不通。
    """
    import time
    import urllib.error
    import urllib.request

    base = endpoint.rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    result: dict[str, object] = {"endpoint": base}

    # 本地适配层会暴露 /health，顺便带上上游模型状态；云端没有这个端点，忽略即可
    try:
        with urllib.request.urlopen(f"{root}/health", timeout=5) as resp:
            result["health"] = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - 非本地服务没有 /health 很正常
        result["health"] = None

    payload = {"model": model, "input": "好", "voice": voice, "response_format": "mp3"}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        base + "/audio/speech", data=json.dumps(payload).encode(), headers=headers
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=45) as resp:
            audio = resp.read()
            result.update(
                ok=True,
                status=resp.status,
                bytes=len(audio),
                content_type=resp.headers.get("content-type", ""),
                ms=int((time.time() - started) * 1000),
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        result.update(ok=False, status=exc.code, error=detail, ms=int((time.time() - started) * 1000))
    except Exception as exc:  # noqa: BLE001
        result.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:200], ms=int((time.time() - started) * 1000))
    return result


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        _emit({"ok": False, "error": "缺少子命令"})
        return 2

    command = argv[1]

    try:
        if command == "list":
            _emit({"ok": True, "instances": ic.list_instances()})
            return 0

        if command == "get":
            instance = ic.require_instance(argv[2])
            _emit({"ok": True, "instance": instance})
            return 0

        if command == "create":
            payload = json.loads(argv[2])
            instance = ic.create_instance(
                instance_id=str(payload.get("id") or ""),
                name=str(payload.get("name") or ""),
                bot_qq=str(payload.get("bot_qq") or ""),
                persona=str(payload.get("persona") or ""),
                onebot_ws_url=str(payload.get("onebot_ws_url") or ""),
            )
            _emit({"ok": True, "instance": instance})
            return 0

        if command == "update":
            payload = json.loads(argv[3])
            instance = ic.update_instance(argv[2], payload)
            _emit({"ok": True, "instance": instance})
            return 0

        if command == "delete":
            keep = "--keep-backup" in argv
            result = ic.delete_instance(argv[2], keep_backup=keep)
            _emit({"ok": True, **result})
            return 0

        if command == "config":
            instance_id = argv[2]
            instance = ic.require_instance(instance_id)
            path = ic.config_path_of(instance)
            data = {}
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
            _emit({"ok": True, "config": data, "path": str(path)})
            return 0

        if command == "save":
            instance_id = argv[2]
            payload = json.loads(argv[3])
            ic.write_config(instance_id, payload)
            _emit({"ok": True})
            return 0

        if command == "secrets":
            _emit({"ok": True, "secrets": ic.read_secrets()})
            return 0

        if command == "rvc-preview":
            import asyncio
            import base64
            import tempfile
            sys.path.insert(0, str(ic.ROOT / "src"))
            from im_bridge.config import Config
            from im_bridge.core.voice import configured_synthesizer
            from im_bridge.core.rvc_voice import RvcVoiceConverter
            from im_bridge.core.voice_registry import VoiceRegistry
            instance = ic.require_instance(argv[2])
            config = Config.from_env_or_file(config_path=ic.config_path_of(instance), env_dir=ic.ROOT)
            request = json.loads(sys.stdin.read(8192))
            text = str(request.get("text") or "").strip()
            if not 0 < len(text) <= 100:
                raise ValueError("试听文字应为 1~100 字")
            converter = RvcVoiceConverter(
                VoiceRegistry(config.data_dir / "voices"), endpoint=config.rvc_endpoint,
                api_key=config.rvc_api_key, default_voice=config.rvc_default_voice,
            )
            if not config.rvc_enabled or not config.rvc_endpoint or not converter.is_available():
                raise ValueError("本地语音未连接；请先启用并连接")
            synth = configured_synthesizer(config)
            with tempfile.TemporaryDirectory(prefix="rvc-preview-") as tmp:
                source = asyncio.run(synth.synthesize(text, Path(tmp) / "base.mp3"))
                if source is None:
                    raise ValueError("语音合成失败，请检查所选语音来源与服务日志")
                output = source if config.voice_mode == "local_tts" else converter.convert(source, config.rvc_default_voice)
                if output is None:
                    raise ValueError("RVC 转换失败，请检查本地模型或稍后重试")
                _emit({"ok": True, "audio": base64.b64encode(output.read_bytes()).decode("ascii")})
            return 0

        if command == "tts-probe":
            instance_id = argv[2] if len(argv) > 2 else "default"
            instance = ic.require_instance(instance_id)
            path = ic.config_path_of(instance)
            config: dict = {}
            if path.is_file():
                config = json.loads(path.read_text(encoding="utf-8"))
            env = ic._parse_env(ic.ENV_PATH)
            endpoint = str(config.get("tts_endpoint") or env.get("AMIS_TTS_ENDPOINT") or "").strip()
            model = str(config.get("tts_model") or env.get("AMIS_TTS_MODEL") or "tts-1").strip()
            voice = str(config.get("tts_voice") or env.get("AMIS_TTS_VOICE") or "alloy").strip()
            key = (env.get("AMIS_TTS_API_KEY") or env.get("AMIS_API_KEY") or "").strip()
            if not endpoint:
                _emit({"ok": False, "error": "未配置 TTS 接口地址"})
                return 0
            probe = _probe_tts(endpoint, model, voice, key)
            _emit(
                {
                    "ok": bool(probe.get("ok")),
                    "probe": probe,
                    "model": model,
                    "voice": voice,
                    "local": "127.0.0.1" in endpoint or "localhost" in endpoint,
                }
            )
            return 0

        if command == "secret":
            ic.write_secret(argv[2], argv[3])
            _emit({"ok": True, "secrets": ic.read_secrets()})
            return 0

        if command == "secret-stdin":
            # 密钥走 stdin，避免出现在 ssh 命令行/服务器进程列表里
            ic.write_secret(argv[2], sys.stdin.read().strip())
            _emit({"ok": True, "secrets": ic.read_secrets()})
            return 0

        _emit({"ok": False, "error": f"未知子命令：{command}"})
        return 2

    except ic.InstanceError as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1
    except (IndexError, ValueError) as exc:
        _emit({"ok": False, "error": f"参数错误：{exc}"})
        return 2
    except Exception as exc:  # noqa: BLE001
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
