"""Real HTTP relay, isolated Eagle fixture and synthetic capture; no user library/screen is read."""
import asyncio
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from PIL import Image
import numpy as np

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "ComfyUI-EagleAssets/tests"))
sys.path.insert(0, str(ROOT / "DesktopBridge-Client"))
from fixture import Fixture
import client
import capture


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path / "__init__.py", submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.fixture = Fixture(cls.temp.name, media=True, native_web=True)
        from aiohttp import web
        import server
        cls.app = web.Application(client_max_size=180 * 1024 * 1024)
        cls.instance = server.PromptServer.instance
        cls.instance.app = cls.app
        def user(request):
            value = request.headers.get("comfy-user")
            if value not in {"alice", "bob"}:
                raise KeyError("Unknown user")
            return value
        cls.instance.user_manager = types.SimpleNamespace(get_request_user_id=user)
        cls.bridge = load("desktop_bridge_fixture", ROOT / "ComfyUI-DesktopBridge").bridge
        cls.screen = load("screen_capture_fixture", ROOT / "ComfyUI-ScreenCapture")
        @cls.instance.routes.post("/prompt")
        async def prompt(request):
            return web.json_response(await request.json())
        cls.app.add_routes(cls.instance.routes)
        cls.loop = asyncio.new_event_loop()
        ready = threading.Event()
        async def start():
            cls.runner = web.AppRunner(cls.app)
            await cls.runner.setup()
            cls.site = web.TCPSite(cls.runner, "127.0.0.1", 0)
            await cls.site.start()
            cls.host = "http://127.0.0.1:" + str(cls.site._server.sockets[0].getsockname()[1])
            ready.set()
        def serve():
            asyncio.set_event_loop(cls.loop)
            cls.loop.run_until_complete(start()); cls.loop.run_forever()
        cls.thread = threading.Thread(target=serve, daemon=True); cls.thread.start(); ready.wait(10)
        code = cls.http("/desktop_bridge/pair", {})["code"]
        cls.identity = cls.http("/desktop_bridge/agent/claim", {"code": code, "name": "同事的测试电脑", "capabilities": ["eagle", "capture"]})
        cls.stop = threading.Event()
        cls.serial = 0
        def synthetic(config):
            cls.serial += 1
            image = Image.new("RGB", (80, 60), (cls.serial % 256, 30, 220))
            return capture.crop(image, config)
        cls.capture_patch = patch.object(capture, "grab", synthetic); cls.capture_patch.start()
        cls.agent = threading.Thread(target=client.run, args=({"server": cls.host, "identity": cls.identity, "capabilities": ["eagle", "capture"]}, cls.stop, lambda value: None, cls.fixture.httpd.server_address[1]), daemon=True)
        cls.agent.start()

    @classmethod
    def tearDownClass(cls):
        cls.stop.set()
        cls.capture_patch.stop()
        # Revocation and closing the runner cancel long poll requests cleanly.
        cls.http("/desktop_bridge/forget", {"id": cls.identity["id"]})
        asyncio.run_coroutine_threadsafe(cls.runner.cleanup(), cls.loop).result(30)
        cls.loop.call_soon_threadsafe(cls.loop.stop); cls.thread.join(3)
        cls.fixture.close(); cls.temp.cleanup()

    @classmethod
    def http(cls, route, body=None, user="alice", headers=None):
        request = Request(cls.host + route, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json", "comfy-user": user, **(headers or {})})
        with urlopen(request, timeout=200) as response:
            return json.load(response)

    def settings(self):
        return {**self.fixture.settings(), "host": "bridge://" + self.identity["id"], "folder_id": "assets", "limit": 3}

    def test_01_remote_eagle_alpha_original_and_metadata(self):
        path = self.fixture.library / "images/asset0.info/1.png"
        image = Image.new("RGBA", (64, 64), (255, 0, 255, 0))
        image.putpixel((32, 32), (250, 20, 30, 255)); image.save(path)
        settings = json.dumps(self.settings())
        connected = self.http("/eagle_assets/connect", {"settings": settings})
        self.assertEqual(connected["library_name"], "Fixture")
        catalog = self.http("/eagle_assets/catalog", {"settings": settings})
        self.assertTrue(catalog["ok"])
        preview = next(item for item in catalog["items"] if item["id"] == "asset0")["preview"]
        media = next(item for item in catalog["items"] if item["id"] == "asset0")["media_url"]
        with urlopen(self.host + media) as response:
            self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertTrue(preview["filename"].endswith(".png"))
        with Image.open(Path(self.temp.name) / "temp" / preview["filename"]) as result:
            self.assertEqual(result.mode, "RGBA"); self.assertEqual(result.getpixel((0, 0))[3], 0)
        cache = Path(self.temp.name) / "user/desktop_bridge/cache" / self.identity["id"]
        self.assertTrue(list(cache.rglob("1.png")))
        self.assertFalse(any(self.fixture.library == path for path in cache.parents))
        result = self.http("/eagle_assets/item_info", {"settings": settings, "item_id": "asset0"})
        self.assertEqual(result["item"]["id"], "asset0")

    def test_02_multi_user_isolation_and_prompt_identity(self):
        self.assertEqual(self.http("/desktop_bridge/devices", user="bob")["devices"], [])
        with self.assertRaises(HTTPError) as rejected:
            self.http("/eagle_assets/connect", {"settings": json.dumps(self.settings())}, user="bob")
        self.assertEqual(rejected.exception.code, 400)
        result = self.http("/prompt", {"prompt": {"1": {"class_type": "EagleAssetImport", "inputs": {"settings": json.dumps({**self.settings(), "_bridge_auth": self.bridge.proof("bob")})}}, "2": {"class_type": "DesktopScreenCapture", "inputs": {"bridge_auth": self.bridge.proof("bob")}}}})
        self.assertEqual(self.bridge.verify(json.loads(result["prompt"]["1"]["inputs"]["settings"])["_bridge_auth"]), "alice")
        self.assertEqual(self.bridge.verify(result["prompt"]["2"]["inputs"]["bridge_auth"]), "alice")
        with self.assertRaises(ValueError):
            self.bridge.call(self.identity["id"], "capture.frames", {}, self.bridge.proof("bob"))
        with self.assertRaises(HTTPError):
            self.http("/desktop_bridge/forget", {"id": self.identity["id"]}, user="bob")
        with self.assertRaises(HTTPError):
            self.http("/desktop_bridge/agent/poll", {}, headers={"X-Desktop-Device": self.identity["id"], "Authorization": "Bearer wrong"})

    def test_03_remote_export_transfers_bytes_and_annotations(self):
        import eagle_assets_fixture.runtime as runtime
        client = runtime.client_for({**self.settings(), "_bridge_auth": self.bridge.proof("alice")})
        path = Path(self.temp.name) / "output/server-only.png"
        Image.new("RGB", (13, 7), (4, 190, 65)).save(path)
        client.export_paths([path], ["跨电脑结果"], "results", tags=["测试"], annotation="第一段\n\n第二文本框内容\n第二段")
        payload = self.fixture.exports[-1]
        self.assertNotEqual(Path(payload["items"][0]["path"]), path)
        self.assertEqual(payload["items"][0]["annotation"], "第一段\n\n第二文本框内容\n第二段")
        self.assertEqual(payload["items"][0]["tags"], ["测试"])
        original = next(item for item in self.fixture.items if item["name"] == "跨电脑结果")
        with Image.open(self.fixture.library / "images" / (original["id"] + ".info") / "跨电脑结果.png") as image:
            self.assertEqual(image.getpixel((0, 0)), (4, 190, 65))

    def test_04_capture_batch_freshness_rgb_resize_mask_and_fault(self):
        config = {"enabled": True, "source": "computer", "device": self.identity["id"], "target": "monitor", "monitor": 1, "x": 10, "y": 5, "width": 20, "height": 10, "frames": 3, "interval": 0, "fps": 60, "resize_width": 40, "resize_height": 20, "on_error": "error"}
        node = self.screen.DesktopScreenCapture()
        result = node.capture(bridge_auth=self.bridge.proof("alice"), **config)["result"]
        images, masks, status = result
        self.assertEqual(tuple(images.shape), (3, 20, 40, 3))
        self.assertEqual(tuple(masks.shape), (3, 20, 40))
        self.assertEqual(float(masks.sum()), 0)
        self.assertAlmostEqual(float(images[0, 0, 0, 2]), 220 / 255, places=5)
        self.assertNotEqual(float(images[0, 0, 0, 0]), float(images[-1, 0, 0, 0]))
        later = node.capture(bridge_auth=self.bridge.proof("alice"), **{**config, "frames": 1})["result"][0]
        self.assertNotEqual(float(later[0, 0, 0, 0]), float(images[-1, 0, 0, 0]))
        blank = node.capture(bridge_auth="bad", **{**config, "on_error": "blank"})["result"]
        self.assertIn("空图", blank[2]); self.assertEqual(float(blank[0].sum()), 0); self.assertEqual(float(blank[1].min()), 1)
        disabled = node.capture(**{**config, "enabled": False})["result"]
        self.assertEqual(disabled[2], "捕获已关闭")

    def test_05_archives_reject_traversal_and_browser_crop_alpha(self):
        from importlib import import_module
        files = import_module(self.bridge.__package__ + ".files")
        import zipfile
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("../escape.txt", "bad")
        with self.assertRaises(ValueError):
            files.unpack(base64.b64encode(output.getvalue()).decode(), Path(self.temp.name) / "bad")
        image = Image.new("RGBA", (30, 20), (200, 30, 80, 128))
        data = {"frames": [capture.encode(image)]}
        images, masks = self.screen.decode_frames(data, {"source": "browser", "x": 5, "y": 3, "width": 6, "height": 7, "resize_width": 0, "resize_height": 0})
        self.assertEqual(tuple(images.shape), (1, 7, 6, 3))
        self.assertAlmostEqual(float(masks[0, 0, 0]), 127 / 255, places=5)

    def test_06_preview_transport_preserves_source_coordinates(self):
        result = self.http("/screen_capture/preview", {"source": "computer", "device": self.identity["id"], "target": "monitor", "monitor": 1, "frames": 1, "fps": 10})
        self.assertEqual((result["width"], result["height"]), (80, 60))
        with patch.object(capture, "grab", return_value=Image.new("RGB", (4000, 2000), (50, 60, 70))):
            preview = capture.capture({"frames": 1, "preview": True})
        self.assertEqual((preview["width"], preview["height"]), (4000, 2000))
        with Image.open(io.BytesIO(base64.b64decode(preview["frames"][0]))) as image:
            self.assertEqual(image.size, (1280, 640))
        with self.assertRaises(ValueError):
            capture.capture({"frames": 1}, cancel=lambda: (_ for _ in ()).throw(ValueError("cancel")))

    def test_07_revocation_ends_inflight_poll(self):
        code = self.http("/desktop_bridge/pair", {}, user="bob")["code"]
        identity = self.http("/desktop_bridge/agent/claim", {"code": code, "name": "撤销测试", "capabilities": ["capture"]})
        result = []
        def poll():
            try:
                self.http("/desktop_bridge/agent/poll", {}, headers={"X-Desktop-Device": identity["id"], "Authorization": "Bearer " + identity["secret"]})
                result.append(200)
            except HTTPError as exc:
                result.append(exc.code)
        worker = threading.Thread(target=poll, daemon=True); worker.start(); time.sleep(.3)
        self.http("/desktop_bridge/forget", {"id": identity["id"]}, user="bob")
        worker.join(3)
        self.assertEqual(result, [403])


if __name__ == "__main__":
    unittest.main()
