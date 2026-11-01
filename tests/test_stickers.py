"""表情包核心离线测试：标签解析 / 流式过滤 / 频控 / 素材库 / 编码。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from im_bridge.core.stickers import (  # noqa: E402
    RateLimiter,
    StickerService,
    StickerStore,
    TagFilter,
    parse_tags,
    strip_tags,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeConfig:
    sticker_enabled = True
    sticker_probability = 1.0
    sticker_cooldown = 0
    sticker_max_per_day = 100


class TagTests(unittest.TestCase):
    def test_parse_and_strip(self):
        text = "你好呀 [[表情:开心]] 今天也要开心 [[表情：害羞]]"
        self.assertEqual(parse_tags(text), ["开心", "害羞"])
        self.assertEqual(strip_tags(text), "你好呀  今天也要开心")

    def test_plain_text_untouched(self):
        self.assertEqual(parse_tags("普通文本"), [])
        self.assertEqual(strip_tags("普通文本"), "普通文本")


class TagFilterTests(unittest.TestCase):
    def test_tag_split_across_deltas(self):
        flt = TagFilter()
        parts = [flt.feed(chunk) for chunk in ["你好", "[[表", "情:开", "心]]", "世界"]]
        self.assertEqual("".join(parts), "你好世界")
        self.assertEqual(flt.tags, ["开心"])
        self.assertEqual(flt.flush(), "")

    def test_plain_brackets_preserved(self):
        flt = TagFilter()
        out = flt.feed("数组 [[1,2]] 结束")
        tail = flt.flush()
        combined = out + tail
        self.assertIn("数组", combined)
        self.assertIn("结束", combined)

    def test_empty(self):
        flt = TagFilter()
        self.assertEqual(flt.feed(""), "")
        self.assertEqual(flt.tags, [])
        self.assertEqual(flt.flush(), "")


class RateLimiterTests(unittest.TestCase):
    def test_cooldown(self):
        limiter = RateLimiter(cooldown=60, max_per_day=10)
        self.assertTrue(limiter.allow("u1", now=1000))
        limiter.mark("u1", now=1000)
        self.assertFalse(limiter.allow("u1", now=1030))
        self.assertTrue(limiter.allow("u1", now=1061))

    def test_daily_limit(self):
        limiter = RateLimiter(cooldown=0, max_per_day=2)
        for index in range(2):
            self.assertTrue(limiter.allow("u1", now=1000 + index))
            limiter.mark("u1", now=1000 + index)
        self.assertFalse(limiter.allow("u1", now=1002))
        # 第二天重置
        self.assertTrue(limiter.allow("u1", now=90000))


class StoreTests(unittest.TestCase):
    def test_directory_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "stickers"
            happy = root / "开心"
            happy.mkdir(parents=True)
            (happy / "a.png").write_bytes(PNG_BYTES)
            (happy / "b.gif").write_bytes(PNG_BYTES)
            store = StickerStore(root)
            self.assertEqual(store.tags(), ["开心"])
            self.assertEqual(store.count(), 2)
            self.assertIsNotNone(store.pick("开心"))
            self.assertIsNone(store.pick("不存在"))

    def test_index_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "stickers"
            root.mkdir()
            (root / "x.png").write_bytes(PNG_BYTES)
            (root / "index.json").write_text(
                '{"开心": ["x.png"], "缺失": ["nope.png"]}',
                encoding="utf-8",
            )
            store = StickerStore(root)
            self.assertEqual(store.tags(), ["开心"])
            self.assertIsNotNone(store.pick("开心"))
            self.assertIsNone(store.pick("缺失"))


class ServiceTests(unittest.TestCase):
    def _service(self, root: Path, config=None) -> StickerService:
        config = config or FakeConfig()
        config.data_dir = str(root)
        return StickerService(config)

    def test_choose_returns_base64(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "stickers"
            tag_dir = root / "开心"
            tag_dir.mkdir(parents=True)
            (tag_dir / "a.png").write_bytes(PNG_BYTES)
            service = self._service(Path(tmp))
            encoded = service.choose("u1", ["开心"])
            self.assertIsNotNone(encoded)
            assert encoded is not None
            self.assertTrue(encoded.startswith("base64://"))
            raw = base64.b64decode(encoded[len("base64://") :])
            self.assertEqual(raw, PNG_BYTES)
            # 冷却 0 / 上限 100，第二次仍可
            self.assertIsNotNone(service.choose("u1", ["开心"]))

    def test_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = FakeConfig()
            config.sticker_enabled = False
            service = self._service(Path(tmp), config)
            self.assertIsNone(service.choose("u1", ["开心"]))
            self.assertEqual(service.rules_prompt, "")

    def test_unknown_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            self.assertIsNone(service.choose("u1", ["不存在"]))



class ToleranceTests(unittest.TestCase):
    """模型写出 [文件名.jpg] 这类标记时：剥干净；能对上标签就发图。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "stickers"
        (root / "开心").mkdir(parents=True)
        (root / "开心" / "happy_1.png").write_bytes(PNG_BYTES)
        (root / "index.json").write_text('{"开心": ["开心/happy_1.png"]}', encoding="utf-8")
        config = FakeConfig()
        config.data_dir = self.tmp.name
        self.service = StickerService(config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_filename_marker_stripped(self):
        cleaned, tags = self.service.parse("先给你这张 [雪绒豹豹歪头.jpg]")
        self.assertEqual(cleaned, "先给你这张")
        self.assertEqual(tags, [])

    def test_filename_maps_to_tag(self):
        cleaned, tags = self.service.parse("看这个 [happy_1.png]")
        self.assertEqual(cleaned, "看这个")
        self.assertEqual(tags, ["开心"])

    def test_stream_filter_strips_filename(self):
        stream = self.service.new_filter()
        out = "".join(stream.feed(part) for part in ["先给你", " [雪绒", "豹豹.jpg]", " 收好"])
        self.assertNotIn("雪绒豹豹", out)
        self.assertEqual(stream.tags, [])

    def test_force_overrides_probability(self):
        self.service.probability = 0.0
        self.assertIsNone(self.service.choose("u", ["开心"], force=False))
        self.assertIsNotNone(self.service.choose("u", ["开心"], force=True))

    def test_force_without_tags_picks_random(self):
        self.assertIsNotNone(self.service.choose("u", [], force=True))

    def test_rules_prompt_mentions_example(self):
        prompt = self.service.rules_prompt
        self.assertIn("[[表情:开心]]", prompt)
        self.assertIn("不要写文件名", prompt)


if __name__ == "__main__":
    unittest.main()
