"""
配置界面一致性测试 —— 防止「界面上有开关、保存时被丢弃」。

背景：表情包四个字段曾只存在于 FIELD_UI，不在 config_store.SCHEMA 里，
`collect()` 按 schema 过滤后直接丢掉，于是「开启 → 保存 → 又变回关闭」。

运行：python tests/test_config_ui_consistency.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "launcher"))

import config_store  # noqa: E402
import qt_theme  # noqa: E402

SCHEMA = {spec["key"]: spec for spec in config_store.SCHEMA}
FIELD_UI = qt_theme.FIELD_UI


class FieldCoverageTest(unittest.TestCase):
    def test_every_ui_field_is_saveable(self):
        """界面能编辑的字段，必须在保存 schema 里，否则改完会被静默丢弃。"""
        missing = [key for key in FIELD_UI if key not in SCHEMA]
        self.assertEqual(missing, [], f"这些字段界面可见但保存会被丢弃：{missing}")

    def test_every_bool_field_renders_as_switch(self):
        """schema 里是 bool 的字段，必须登记在 BOOL_KEYS，否则会渲染成文本框。"""
        from ui.pages.ai_config import BOOL_KEYS

        for key, spec in SCHEMA.items():
            if spec.get("type") != "bool" or key not in FIELD_UI:
                continue
            self.assertIn(key, BOOL_KEYS, f"{key} 是 bool，但没进 BOOL_KEYS")

    def test_bool_keys_are_declared_bool(self):
        from ui.pages.ai_config import BOOL_KEYS

        for key in BOOL_KEYS:
            spec = SCHEMA.get(key)
            self.assertIsNotNone(spec, f"{key} 在 BOOL_KEYS 但不在 schema")
            self.assertEqual(spec.get("type"), "bool", f"{key} 不是 bool 类型")


class FlattenIdsTest(unittest.TestCase):
    """历史数据修复：字符串化列表要能摊平回正常 ID。"""

    def setUp(self) -> None:
        from ui.pages.ai_config import _flatten_ids

        self.flatten = _flatten_ids

    def test_plain_list(self):
        self.assertEqual(self.flatten(["10001", "10002"]), ["10001", "10002"])

    def test_comma_string(self):
        self.assertEqual(self.flatten("10001, 10002"), ["10001", "10002"])
        self.assertEqual(self.flatten("10001，10002"), ["10001", "10002"])

    def test_stringified_list(self):
        self.assertEqual(self.flatten("['10001', '10002']"), ["10001", "10002"])
        self.assertEqual(self.flatten('["10001"]'), ["10001"])

    def test_double_nested_garbage(self):
        # 服务器上真实出现过的脏数据
        self.assertEqual(self.flatten(["['[]']"]), [])
        self.assertEqual(self.flatten(['["[\'10001\']"]']), ["10001"])

    def test_empty_values(self):
        self.assertEqual(self.flatten(None), [])
        self.assertEqual(self.flatten(""), [])
        self.assertEqual(self.flatten([]), [])


if __name__ == "__main__":
    unittest.main()
