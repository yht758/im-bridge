"""
图片识别离线测试 —— 假 HTTP，不访问真实网络。

运行：python tests/test_vision.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "libs"))

import httpx  # noqa: E402

from im_bridge.adapters.onebot.app import OneBotApp  # noqa: E402
from im_bridge.config import Config  # noqa: E402
from im_bridge.core.vision import ImageDescriber  # noqa: E402


def make_response(status_code: int = 200, *, json_data: dict | None = None) -> httpx.Response:
    request = httpx.Request("POST", "http://fake.local/v1/chat/completions")
    return httpx.Response(status_code, json=json_data or {}, request=request)


class FakeAsyncClient:
    """假的 httpx.AsyncClient，只实现 post 与异步上下文协议。"""

    def __init__(self, response: httpx.Response | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def patch_client(fake: FakeAsyncClient):
    return patch.object(ImageDescriber, "_client", lambda self: fake)


class ImageDescriberTest(unittest.IsolatedAsyncioTestCase):
    def make(self, **kwargs) -> ImageDescriber:
        params = dict(
            endpoint="http://fake.local/v1",
            api_key="sk-test",
            model="deepseek-chat",
        )
        params.update(kwargs)
        return ImageDescriber(**params)

    def test_disabled_when_missing_parts(self):
        self.assertFalse(self.make(endpoint="").enabled)
        self.assertFalse(self.make(api_key="").enabled)
        self.assertFalse(self.make(model="").enabled)

    async def test_describe_returns_text(self):
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "一只橘猫趴在键盘上，屏幕显示代码。"}}
            ]
        }
        fake = FakeAsyncClient(make_response(200, json_data=body))
        describer = self.make()
        with patch_client(fake):
            caption = await describer.describe(b"\x89PNG", "image/png")
        self.assertIn("橘猫", caption)
        self.assertEqual(describer.last_reason, "ok")
        payload = fake.calls[0]["json"]
        content = payload["messages"][-1]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    async def test_describe_handles_list_content(self):
        body = {"choices": [{"message": {"content": [{"type": "text", "text": "一张截图"}]}}]}
        fake = FakeAsyncClient(make_response(200, json_data=body))
        describer = self.make()
        with patch_client(fake):
            self.assertEqual(await describer.describe(b"x"), "一张截图")

    async def test_describe_skips_oversized_image(self):
        fake = FakeAsyncClient(make_response(200, json_data={}))
        describer = self.make(max_bytes=4)
        with patch_client(fake):
            self.assertEqual(await describer.describe(b"12345"), "")
        self.assertEqual(describer.last_reason, "too_large")
        self.assertEqual(fake.calls, [])

    async def test_describe_survives_http_error(self):
        fake = FakeAsyncClient(make_response(500, json_data={"error": "boom"}))
        describer = self.make()
        with patch_client(fake):
            self.assertEqual(await describer.describe(b"x"), "")
        self.assertEqual(describer.last_reason, "http_500")

    async def test_describe_survives_exception(self):
        fake = FakeAsyncClient(error=httpx.ConnectError("no route"))
        describer = self.make()
        with patch_client(fake):
            self.assertEqual(await describer.describe(b"x"), "")
        self.assertEqual(describer.last_reason, "request_error")


class ImageParsingTest(unittest.TestCase):
    def test_find_images_in_segments(self):
        message = [
            {"type": "image", "data": {"file": "a.image", "url": "https://x/1.jpg"}},
            {"type": "text", "data": {"text": "看看"}},
            {"type": "mface", "data": {"file": "b.gif", "url": "https://x/2.gif"}},
        ]
        images = OneBotApp._find_images(message)
        self.assertEqual([item["url"] for item in images], ["https://x/1.jpg", "https://x/2.gif"])

    def test_find_images_in_cq_string(self):
        message = "[CQ:image,file=a.image,url=https://x/1.jpg,sub_type=0]这是啥"
        images = OneBotApp._find_images(message)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["url"], "https://x/1.jpg")
        self.assertEqual(images[0]["file"], "a.image")

    def test_find_images_empty(self):
        self.assertEqual(OneBotApp._find_images("[CQ:face,id=14]你好"), [])
        self.assertEqual(OneBotApp._find_images(None), [])

    def test_clean_text_strips_cq_codes(self):
        self.assertEqual(OneBotApp._clean_text("[CQ:face,id=14] 你好 [CQ:image,file=a.image]"), "你好")

    def test_compose_with_caption(self):
        text = OneBotApp._compose_with_images("这是什么", ["一只橘猫"], 0)
        self.assertIn("这是什么", text)
        self.assertIn("一只橘猫", text)
        self.assertIn("图片内容", text)

    def test_compose_image_only(self):
        text = OneBotApp._compose_with_images("", ["一张聊天截图"], 1)
        self.assertIn("一张聊天截图", text)
        self.assertIn("没能读取", text)
        self.assertIn("只发了图片", text)


class FakeEngine:
    """记录引擎收到的文本，不产生真实网络行为。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def handle_message(self, user_id: str, nickname: str, text: str) -> bool:
        self.calls.append((user_id, nickname, text))
        return False


class FakeTransport:
    async def call_api(self, *args, **kwargs):
        raise AssertionError("本用例应走本地文件，不应调用 get_image")

    async def close(self) -> None:
        return None


class InboundImageFlowTest(unittest.IsolatedAsyncioTestCase):
    """纯图片消息应当触发一次带描述的回复。"""

    async def test_image_only_message_reaches_engine(self):
        import tempfile

        from im_bridge.adapters.onebot import app as app_module

        body = {"choices": [{"message": {"content": "一只橘猫趴在键盘上，屏幕是代码编辑器。"}}]}
        fake_client = FakeAsyncClient(make_response(200, json_data=body))
        config = Config(api_key="sk-test", image_input_enabled=True)
        describer = ImageDescriber(
            endpoint="http://fake.local/v1", api_key="sk-test", model="deepseek-chat"
        )
        engine = FakeEngine()
        app = OneBotApp(config=config, engine=engine, transport=FakeTransport(), describer=describer)

        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "cat.jpg"
            image_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
            event = {
                "user_id": "10001",
                "message_type": "private",
                "message": [{"type": "image", "data": {"file": str(image_path), "url": ""}}],
                "raw_message": "[CQ:image,file=cat.jpg]",
                "sender": {"nickname": "测试"},
            }
            original = app_module.append_log
            app_module.append_log = lambda *a, **k: None
            try:
                with patch_client(fake_client):
                    await app._handle_private_msg(event)
            finally:
                app_module.append_log = original

        self.assertEqual(len(engine.calls), 1)
        text = engine.calls[0][2]
        self.assertIn("橘猫", text)
        self.assertIn("只发了图片", text)
        self.assertNotIn("[CQ:", text)

    async def test_text_with_image_keeps_original_words(self):
        import tempfile

        from im_bridge.adapters.onebot import app as app_module

        body = {"choices": [{"message": {"content": "一张微信聊天截图，内容在讨论周末聚餐。"}}]}
        fake_client = FakeAsyncClient(make_response(200, json_data=body))
        config = Config(api_key="sk-test")
        describer = ImageDescriber(
            endpoint="http://fake.local/v1", api_key="sk-test", model="deepseek-chat"
        )
        engine = FakeEngine()
        app = OneBotApp(config=config, engine=engine, transport=FakeTransport(), describer=describer)

        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "shot.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            event = {
                "user_id": "10002",
                "message_type": "private",
                "message": [
                    {"type": "image", "data": {"file": str(image_path), "url": ""}},
                    {"type": "text", "data": {"text": "他们说的这家店在哪"}},
                ],
                "raw_message": "[CQ:image,file=shot.png]他们说的这家店在哪",
                "sender": {"nickname": "测试"},
            }
            original = app_module.append_log
            app_module.append_log = lambda *a, **k: None
            try:
                with patch_client(fake_client):
                    await app._handle_private_msg(event)
            finally:
                app_module.append_log = original

        self.assertEqual(len(engine.calls), 1)
        text = engine.calls[0][2]
        self.assertIn("他们说的这家店在哪", text)
        self.assertIn("周末聚餐", text)

    async def test_disabled_image_input_still_responds(self):
        from im_bridge.adapters.onebot import app as app_module

        config = Config(api_key="sk-test", image_input_enabled=False)
        engine = FakeEngine()
        app = OneBotApp(config=config, engine=engine, transport=FakeTransport())
        event = {
            "user_id": "10003",
            "message_type": "private",
            "message": [{"type": "image", "data": {"file": "x.image", "url": ""}}],
            "raw_message": "[CQ:image,file=x.image]在吗",
            "sender": {"nickname": "测试"},
        }
        original = app_module.append_log
        app_module.append_log = lambda *a, **k: None
        try:
            await app._handle_private_msg(event)
        finally:
            app_module.append_log = original

        self.assertEqual(len(engine.calls), 1)
        text = engine.calls[0][2]
        self.assertIn("在吗", text)
        self.assertIn("未开启", text)


class ConfigVisionFieldsTest(unittest.TestCase):
    def test_defaults(self):
        config = Config()
        self.assertTrue(config.image_input_enabled)
        self.assertEqual(config.vision_endpoint, "")
        self.assertEqual(config.image_max_count, 4)
        self.assertGreaterEqual(config.vision_max_bytes, 1024)

    def test_empty_vision_fields_fall_back_to_llm(self):
        config = Config(api_key="sk-x", base_url="https://api.example.com/v1", model="m")
        describer = ImageDescriber(
            endpoint=config.vision_endpoint or config.llm_base_url,
            api_key=config.vision_api_key or config.api_key,
            model=config.vision_model or config.model,
        )
        self.assertTrue(describer.enabled)
        self.assertEqual(describer._url, "https://api.example.com/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
