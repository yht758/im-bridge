import asyncio
import json
import logging
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "launcher"))
from rvc_bridge import make_server
from im_bridge.core.rvc_voice import RvcVoiceConverter
from im_bridge.core.engine import ChatEngine


class FakeConverter:
    default_voice = "aemeath"
    ready = True

    def is_available(self):
        return self.ready

    def convert(self, source, voice):
        if voice != self.default_voice:
            return None
        out = source.with_suffix(".wav")
        out.write_bytes(b"RIFF\x00\x00\x00\x00WAVEtest")
        return out


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.converter = FakeConverter()
        self.server = make_server(self.converter, "test-token", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.client = RvcVoiceConverter(Mock(default_id=lambda: "aemeath"),
                                         endpoint=self.url, api_key="test-token")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_authentication_and_ready(self):
        self.assertTrue(self.client.is_available())
        self.client.api_key = "wrong"
        self.assertFalse(self.client.is_available())

    def test_missing_model_is_offline(self):
        self.converter.ready = False
        self.assertFalse(self.client.is_available())

    def test_audio_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "base.mp3"
            source.write_bytes(b"fake mp3")
            result = self.client.convert(source)
            self.assertIsNotNone(result)
            self.assertTrue(result.read_bytes().startswith(b"RIFF"))

    def test_unknown_voice_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "base.mp3"
            source.write_bytes(b"fake mp3")
            self.assertIsNone(self.client.convert(source, "missing"))

    def test_invalid_payload_rejected(self):
        request = urllib.request.Request(self.url + "/convert", data=b"[]",
            headers={"Authorization": "Bearer test-token"})
        with self.assertRaises(urllib.error.HTTPError) as result:
            urllib.request.urlopen(request)
        self.assertEqual(result.exception.code, 400)


class OfflinePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_skips_tts_and_record(self):
        engine = ChatEngine.__new__(ChatEngine)
        engine.config = SimpleNamespace(voice_reply_enabled=True, voice_reply_probability=1,
                                       rvc_enabled=True, rvc_fallback="off")
        engine.synthesizer = SimpleNamespace(enabled=True, synthesize=AsyncMock())
        engine.transport = SimpleNamespace(send_record=AsyncMock())
        engine.rvc_converter = SimpleNamespace(is_available=lambda: False)
        engine.logger = logging.getLogger("test")
        engine._generate_voice_line = AsyncMock()
        await engine._maybe_send_voice(1, [], "文字已经发出")
        engine._generate_voice_line.assert_not_called()
        engine.synthesizer.synthesize.assert_not_called()
        engine.transport.send_record.assert_not_called()

    async def test_failed_conversion_does_not_send_base_voice(self):
        engine = ChatEngine.__new__(ChatEngine)
        engine.config = SimpleNamespace(voice_reply_enabled=True, voice_reply_probability=1,
                                       rvc_enabled=True, rvc_fallback="off")
        engine.transport = SimpleNamespace(send_record=AsyncMock())
        engine.rvc_converter = SimpleNamespace(is_available=lambda: True, convert_async=AsyncMock(return_value=None))
        engine.logger = logging.getLogger("test")
        engine._generate_voice_line = AsyncMock(return_value="你好")
        async def synthesize(text, out):
            out.write_bytes(b"base voice")
            return out
        engine.synthesizer = SimpleNamespace(enabled=True, synthesize=synthesize)
        await engine._maybe_send_voice(1, [], "文字已经发出")
        engine.transport.send_record.assert_not_called()


class CliResponseTests(unittest.TestCase):
    def test_stderr_after_json_does_not_hide_response(self):
        import remote
        self.assertEqual(remote.parse_json_output('{"ok": false, "error": "offline"}\nwarning: offline'),
                         {"ok": False, "error": "offline"})

    def test_missing_json_is_a_clear_error(self):
        import remote
        with self.assertRaises(remote.RemoteError):
            remote.parse_json_output("connection closed")


class DirectLocalVoiceTests(unittest.TestCase):
    def test_bridge_synthesizes_without_cloud_or_rvc(self):
        class Local:
            def is_available(self): return True
            def synthesize(self, text):
                self.text = text
                return b"RIFF\0\0\0\0WAVEtest"
        local=Local()
        server=make_server(local,"token",0)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            request=urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/audio/speech",
                data=json.dumps({"input":"你好"}).encode(),headers={"Authorization":"Bearer token"})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.headers["Content-Type"],"audio/wav")
                self.assertTrue(response.read().startswith(b"RIFF"))
            self.assertEqual(local.text,"你好")
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_local_mode_uses_bridge_key_and_endpoint(self):
        from im_bridge.core.voice import configured_synthesizer
        config=SimpleNamespace(voice_mode="local_tts",rvc_endpoint="http://127.0.0.1:9882",
                               rvc_api_key="bridge-key",rvc_default_voice="aemeath")
        synth=configured_synthesizer(config)
        self.assertEqual(synth.endpoint,"http://127.0.0.1:9882/v1")
        self.assertEqual(synth.api_key,"bridge-key")
        self.assertEqual(synth.model,"local-gpt-sovits")


if __name__ == "__main__":
    unittest.main()
