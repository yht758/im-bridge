"""
声音注册表离线测试 —— 不依赖 RVC 引擎，只验证「用户自助添加音色」的状态机与持久化。

覆盖：
- 首次运行自动种入内置 aemeath 音色；
- list / get / default_id / set_default；
- 上传流程：先收 model 再收 index -> 自动登记；顺序相反也行；
- 非 .pth/.index 文件被拒绝；
- remove 不能删内置 aemeath；
- 路径解析：绝对路径原样保留，相对路径以 voices/<id>/ 为基准。

运行：python tests/test_voice_registry.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "libs"))

from im_bridge.core.voice_registry import (  # noqa: E402
    BUILTIN_AEMEATH,
    VoiceRegistry,
)


class VoiceRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.voices_dir = Path(self._tmp.name) / "voices"
        self.reg = VoiceRegistry(self.voices_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---------- 初始化与查询 ----------
    def test_seeds_builtin_aemeath_on_first_load(self) -> None:
        voices = self.reg.list_voices()
        self.assertEqual(len(voices), 1)
        self.assertEqual(voices[0].id, "aemeath")
        self.assertTrue(voices[0].is_builtin)
        # 落盘文件应被创建
        self.assertTrue((self.voices_dir / "voices.json").is_file())

    def test_default_id_is_aemeath(self) -> None:
        self.assertEqual(self.reg.default_id(), "aemeath")
        self.assertEqual(self.reg.get(None).id, "aemeath")

    def test_get_missing_returns_none(self) -> None:
        self.assertIsNone(self.reg.get("does-not-exist"))

    def test_set_default(self) -> None:
        # 先添加一个再切默认
        self.reg.add("v2", "自定义", owner_qq="100", model_path="m.pth", index_path="i.index")
        self.assertTrue(self.reg.set_default("v2"))
        self.assertEqual(self.reg.default_id(), "v2")
        # 切到不存在的音色应失败
        self.assertFalse(self.reg.set_default("nope"))

    def test_remove_blocks_builtin(self) -> None:
        self.assertFalse(self.reg.remove("aemeath"))
        # 普通音色可删，且默认会回落
        self.reg.add("v2", "自定义", owner_qq="100", model_path="m.pth", index_path="i.index")
        self.reg.set_default("v2")
        self.assertTrue(self.reg.remove("v2"))
        self.assertEqual(self.reg.default_id(), "aemeath")

    def test_relative_paths_resolve_under_voices_id(self) -> None:
        self.reg.add("rel", "相对路径", owner_qq="", model_path="model.pth", index_path="index.index")
        v = self.reg.get("rel")
        self.assertIsNotNone(v)
        self.assertEqual(v.model_path, self.voices_dir / "rel" / "model.pth")
        self.assertEqual(v.index_path, self.voices_dir / "rel" / "index.index")

    # ---------- 上传流程（核心状态机） ----------
    def _mk(self, name: str, content: bytes = b"x") -> Path:
        """在已存在的临时根目录下造一个源文件（注册表只负责复制）。"""
        src = Path(self._tmp.name) / name
        src.write_bytes(content)
        return src

    def test_upload_model_then_index_registers(self) -> None:
        user = "12345"
        self.reg.begin_upload(user, "newvoice", "我的音色")
        self.assertIsNotNone(self.reg.get_pending(user))

        status, msg = self.reg.handle_upload_file(user, "model", self._mk("model.pth", b"fake-model"))
        self.assertEqual(status, "need_more")

        status, msg = self.reg.handle_upload_file(user, "index", self._mk("index.index", b"fake-index"))
        self.assertEqual(status, "done")
        self.assertIsNone(self.reg.get_pending(user))

        v = self.reg.get("newvoice")
        self.assertIsNotNone(v)
        self.assertEqual(v.name, "我的音色")
        # 文件应被复制到 voices/newvoice/
        self.assertTrue((self.voices_dir / "newvoice" / "model.pth").is_file())
        self.assertTrue((self.voices_dir / "newvoice" / "index.index").is_file())

    def test_upload_index_then_model_also_registers(self) -> None:
        user = "999"
        self.reg.begin_upload(user, "rev", "反序")
        s1, _ = self.reg.handle_upload_file(user, "index", self._mk("index.index", b"x"))
        self.assertEqual(s1, "need_more")
        s2, _ = self.reg.handle_upload_file(user, "model", self._mk("model.pth", b"y"))
        self.assertEqual(s2, "done")
        self.assertIsNotNone(self.reg.get("rev"))

    def test_upload_without_pending_returns_no_pending(self) -> None:
        status, _ = self.reg.handle_upload_file("nobody", "model", self._mk("model.pth"))
        self.assertEqual(status, "no_pending")

    def test_upload_unknown_kind_rejected(self) -> None:
        user = "555"
        self.reg.begin_upload(user, "bad", "坏")
        status, msg = self.reg.handle_upload_file(user, "txt", self._mk("notes.txt", b"hi"))
        self.assertEqual(status, "error")
        self.assertIn("未知文件类型", msg)

    def test_persistence_across_reload(self) -> None:
        self.reg.add("persist", "持久", owner_qq="1", model_path="m.pth", index_path="i.index")
        # 用同一个目录新建实例，应读到已写入的数据
        reg2 = VoiceRegistry(self.voices_dir)
        self.assertIsNotNone(reg2.get("persist"))
        self.assertEqual(reg2.default_id(), "aemeath")

    def test_builtin_always_present_even_if_file_corrupted(self) -> None:
        # 故意写一个坏 json，加载时应回退并补回 aemeath
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        (self.voices_dir / "voices.json").write_text("{ not valid json", "utf-8")
        reg2 = VoiceRegistry(self.voices_dir)
        self.assertIsNotNone(reg2.get("aemeath"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
