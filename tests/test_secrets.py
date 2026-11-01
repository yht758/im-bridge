"""
密钥读写测试 —— 在临时目录里模拟服务端 .env，不碰真实文件。

运行：python tests/test_secrets.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "launcher"))

import instance_config as ic  # noqa: E402


class SecretStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="secret_test_")
        self.env_path = Path(self.tmpdir.name) / ".env"
        self._original = ic.ENV_PATH
        ic.ENV_PATH = self.env_path

    def tearDown(self) -> None:
        ic.ENV_PATH = self._original
        self.tmpdir.cleanup()

    def write_env(self, text: str) -> None:
        self.env_path.write_text(text, encoding="utf-8")

    def test_read_masks_key_and_keeps_secret_out(self):
        self.write_env("# 注释\nAMIS_API_KEY=sk-1234567890abcdef\nAMIS_MODEL=deepseek-chat\n")
        secrets = ic.read_secrets()

        self.assertTrue(secrets["api_key_set"])
        self.assertEqual(secrets["api_key"], "sk-1…cdef")
        self.assertNotIn("1234567890", str(secrets))
        self.assertFalse(secrets["asr_api_key_set"])

    def test_write_replaces_existing_line_and_keeps_others(self):
        self.write_env("# 我的配置\nexport AMIS_API_KEY=old-key\nAMIS_MODEL=deepseek-chat\n")
        ic.write_secret("api_key", "sk-new-value-9999")

        text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("AMIS_API_KEY=sk-new-value-9999", text)
        self.assertNotIn("old-key", text)
        self.assertIn("AMIS_MODEL=deepseek-chat", text)
        self.assertIn("# 我的配置", text)

    def test_write_appends_when_missing(self):
        self.write_env("AMIS_MODEL=deepseek-chat\n")
        ic.write_secret("asr_api_key", "asr-secret")

        lines = self.env_path.read_text(encoding="utf-8").splitlines()
        self.assertIn("AMIS_ASR_API_KEY=asr-secret", lines)

    def test_rejects_bad_input(self):
        with self.assertRaises(ic.InstanceError):
            ic.write_secret("api_key", "")
        with self.assertRaises(ic.InstanceError):
            ic.write_secret("api_key", "line1\nline2")
        with self.assertRaises(ic.InstanceError):
            ic.write_secret("unknown_key", "x")

    def test_write_does_not_leak_into_other_keys(self):
        self.write_env("AMIS_API_KEY=sk-old\nAMIS_ASR_API_KEY=asr-old\n")
        ic.write_secret("api_key", "sk-new")

        values = ic._parse_env(self.env_path)
        self.assertEqual(values["AMIS_API_KEY"], "sk-new")
        self.assertEqual(values["AMIS_ASR_API_KEY"], "asr-old")


if __name__ == "__main__":
    unittest.main()
