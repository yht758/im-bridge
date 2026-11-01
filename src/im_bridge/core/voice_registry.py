"""
声音注册表 —— 管理「用户可自行加入的 RVC 音色」。

数据落在 ``<data_dir>/voices/voices.json``，结构：
    {
      "default": "aemeath",
      "voices": [
        {
          "id": "aemeath",
          "name": "用户配置音色",
          "owner_qq": "",
          "model_path": "model.pth",
          "index_path": "model.index",
          "description": "本地训练默认音色",
          "enabled": true
        }
      ]
    }

设计原则：
- 绝对路径与相对路径都支持；相对路径以 ``<data_dir>/voices/<id>/`` 为基准解析，
  这样用户新增的音色（模型/索引放在该目录下）可以整体带走。
- 首次运行且无文件时，自动种入内置 ``aemeath`` 音色（指向本地训练好的模型）。
- 任何读写异常都不抛出，仅记日志并返回空结果，避免拖垮启动。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("im_bridge.voice_registry")

# 内置默认值：本地训练好的 aemeath 音色（绝对路径指向 RVC 工程）
BUILTIN_AEMEATH = {
    "id": "aemeath",
    "name": "用户配置音色",
    "owner_qq": "",
    "model_path": "model.pth",
    "index_path": "model.index",
    "description": "本地训练默认音色（RVC 40k / v2 / f0）",
    "enabled": True,
}


@dataclass(frozen=True)
class VoiceEntry:
    """一条音色记录（路径已解析为绝对路径）。"""

    id: str
    name: str
    owner_qq: str
    model_path: Path
    index_path: Path
    description: str
    enabled: bool
    is_builtin: bool = False


class VoiceRegistry:
    """voices.json 的读写与查询。"""

    def __init__(self, voices_dir: Path) -> None:
        self.voices_dir = Path(voices_dir)
        self._path = self.voices_dir / "voices.json"
        self._cache: dict | None = None
        # 进行中的上传：user_id -> {id, name, model: Path|None, index: Path|None}
        self.pending: dict[str, dict] = {}

    # ---------- 底层读写 ----------
    def _ensure_dir(self) -> None:
        try:
            self.voices_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("创建 voices 目录失败：%s", exc)

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = {"default": "aemeath", "voices": [dict(BUILTIN_AEMEATH)]}
            self._save(self._cache)
            return self._cache
        try:
            data = json.loads(self._path.read_text("utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            logger.warning("voices.json 读取/解析失败，回退内置 aemeath")
            data = {}
        data.setdefault("voices", [])
        if not any(v.get("id") == "aemeath" for v in data["voices"]):
            data["voices"].insert(0, dict(BUILTIN_AEMEATH))
        if not data.get("default"):
            data["default"] = "aemeath"
        # 写入缓存前先落盘一次（保证默认文件存在）
        self._cache = data
        return data

    def _save(self, data: dict) -> bool:
        self._ensure_dir()
        try:
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8"
            )
            self._cache = data
            return True
        except OSError as exc:
            logger.warning("写入 voices.json 失败：%s", exc)
            return False

    # ---------- 路径解析 ----------
    def _resolve(self, raw: str) -> Path:
        p = Path(raw)
        if p.is_absolute():
            return p
        # 相对路径以 <voices_dir>/<id>/ 为基准（调用方需保证 id 已知）
        return self.voices_dir / raw

    def _resolve_for(self, entry: dict) -> dict:
        vid = entry.get("id", "")
        model = self._resolve(entry.get("model_path", ""))
        index = self._resolve(entry.get("index_path", ""))
        # 相对路径补上 <voices_dir>/<id>/
        if not Path(entry.get("model_path", "")).is_absolute():
            model = self.voices_dir / vid / entry.get("model_path", "")
        if not Path(entry.get("index_path", "")).is_absolute():
            index = self.voices_dir / vid / entry.get("index_path", "")
        return {**entry, "model_path": model, "index_path": index}

    # ---------- 公开 API ----------
    def list_voices(self) -> list[VoiceEntry]:
        data = self._load()
        out: list[VoiceEntry] = []
        for raw in data.get("voices", []):
            resolved = self._resolve_for(raw)
            out.append(
                VoiceEntry(
                    id=resolved.get("id", ""),
                    name=resolved.get("name", resolved.get("id", "")),
                    owner_qq=str(resolved.get("owner_qq", "")),
                    model_path=Path(resolved.get("model_path", "")),
                    index_path=Path(resolved.get("index_path", "")),
                    description=resolved.get("description", ""),
                    enabled=bool(resolved.get("enabled", True)),
                    is_builtin=(resolved.get("id") == "aemeath"),
                )
            )
        return out

    def get(self, voice_id: str | None) -> VoiceEntry | None:
        if not voice_id:
            voice_id = self._load().get("default")
        for v in self.list_voices():
            if v.id == voice_id:
                return v
        return None

    def default_id(self) -> str:
        return self._load().get("default") or "aemeath"

    def set_default(self, voice_id: str) -> bool:
        data = self._load()
        if not any(v.get("id") == voice_id for v in data.get("voices", [])):
            return False
        data["default"] = voice_id
        return self._save(data)

    def add(
        self,
        voice_id: str,
        name: str,
        owner_qq: str,
        model_path: str,
        index_path: str,
        description: str = "",
    ) -> bool:
        """新增/覆盖一条音色。model_path / index_path 可为绝对或相对（相对则放入 voices/<id>/）。"""
        data = self._load()
        voice_id = voice_id.strip()
        if not voice_id:
            return False
        entry = {
            "id": voice_id,
            "name": name or voice_id,
            "owner_qq": str(owner_qq or ""),
            "model_path": model_path,
            "index_path": index_path,
            "description": description or "",
            "enabled": True,
        }
        voices = data.get("voices", [])
        for index, v in enumerate(voices):
            if v.get("id") == voice_id:
                voices[index] = entry
                data["voices"] = voices
                return self._save(data)
        voices.append(entry)
        data["voices"] = voices
        if not data.get("default"):
            data["default"] = voice_id
        return self._save(data)

    def remove(self, voice_id: str) -> bool:
        # 内置 aemeath 是本地训练音色，也是 RVC 可用性探测的锚点，禁止删除
        if voice_id == BUILTIN_AEMEATH["id"]:
            return False
        data = self._load()
        voices = [v for v in data.get("voices", []) if v.get("id") != voice_id]
        if len(voices) == len(data.get("voices", [])):
            return False
        data["voices"] = voices
        if data.get("default") == voice_id:
            data["default"] = (voices[0].get("id") if voices else "")
        return self._save(data)

    # ---------- 上传流程（用户自助添加音色） ----------
    def begin_upload(self, user_id: str, voice_id: str, name: str) -> None:
        """标记某用户正在上传某个音色，等待模型/索引两个文件。"""
        self.pending[str(user_id)] = {
            "id": voice_id,
            "name": name,
            "model": None,
            "index": None,
        }

    def cancel_upload(self, user_id: str) -> None:
        self.pending.pop(str(user_id), None)

    def get_pending(self, user_id: str) -> dict | None:
        return self.pending.get(str(user_id))

    def handle_upload_file(self, user_id: str, kind: str, src: Path) -> tuple[str, str]:
        """接收上传文件（kind='model'|'index'），凑齐后自动登记。

        返回 (status, message)，status ∈ {need_more, done, error, no_pending}。
        """
        user_id = str(user_id)
        pending = self.pending.get(user_id)
        if pending is None:
            return "no_pending", ""
        if kind not in ("model", "index"):
            return "error", "未知文件类型"
        vid = pending["id"]
        target_dir = self.voices_dir / vid
        target_dir.mkdir(parents=True, exist_ok=True)
        dest_name = "model.pth" if kind == "model" else "index.index"
        dest = target_dir / dest_name
        try:
            import shutil

            shutil.copy2(str(src), str(dest))
        except OSError as exc:
            return "error", f"保存文件失败：{exc}"
        pending[kind] = dest
        if pending["model"] is not None and pending["index"] is not None:
            ok = self.add(
                vid, pending["name"], owner_qq=user_id,
                model_path="model.pth", index_path="index.index",
                description="用户上传音色",
            )
            self.pending.pop(user_id, None)
            if ok:
                return "done", f"音色「{pending['name']}」(id={vid}) 已登记，可用 /voice use {vid} 切换"
            return "error", "登记失败，请检查文件是否有效"
        return "need_more", f"已收到{kind}文件，还差一个文件"
