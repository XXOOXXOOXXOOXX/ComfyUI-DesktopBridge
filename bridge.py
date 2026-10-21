import asyncio
import contextvars
import hashlib
import hmac
import json
import queue
import secrets
import threading
import time
from pathlib import Path

OWNER = contextvars.ContextVar("desktop_bridge_owner", default=None)
_lock = threading.RLock()
_devices, _pairs, _jobs = {}, {}, {}
_key = secrets.token_bytes(32)
_server = None
MAX_JSON = 180 * 1024 * 1024


def online(device_id, device):
    return time.time() - device.get("seen", 0) < 45 or any(j["device"] == device_id and j["deadline"] > time.time() and not j["event"].is_set() for j in _jobs.values())


def owner(request):
    return _server.user_manager.get_request_user_id(request)


def proof(user):
    return user + ":" + hmac.new(_key, user.encode(), hashlib.sha256).hexdigest()


def verify(value):
    user, _, signature = str(value).rpartition(":")
    if not user or not hmac.compare_digest(proof(user), str(value)):
        raise ValueError("请从当前用户的 ComfyUI 页面重新加入队列。")
    return user


def current_owner(auth=""):
    return OWNER.get() or verify(auth)


def call(device_id, operation, payload=None, auth="", timeout=120):
    user = current_owner(auth)
    with _lock:
        device = _devices.get(device_id)
        if not device or device["owner"] != user:
            raise ValueError("这台电脑未与当前 ComfyUI 用户配对。")
        if not online(device_id, device):
            raise ValueError("电脑桥接程序已离线，请在素材/屏幕所在电脑启动它。")
        capability = "eagle" if operation.startswith("eagle.") else "capture"
        if capability not in device["capabilities"]:
            raise ValueError("这台电脑尚未启用对应权限，请重新启动桥接程序并勾选。")
        if sum(j["device"] == device_id for j in _jobs.values()) >= 8:
            raise ValueError("这台电脑的等待请求过多，请稍后重试。")
        job_id = secrets.token_hex(16)
        job = {"id": job_id, "op": operation, "payload": payload or {}, "device": device_id,
               "event": threading.Event(), "deadline": time.time() + timeout}
        _jobs[job_id] = job
        device["queue"].put(job_id)
    try:
        deadline = time.monotonic() + timeout
        while not job["event"].wait(.2):
            interrupt()
            if time.monotonic() >= deadline:
                raise ValueError("电脑桥接请求超时，请检查网络和本地程序。")
        result = job["result"]
        if not result.get("ok"):
            raise ValueError(str(result.get("error", "电脑操作失败"))[:400])
        return result.get("data")
    finally:
        with _lock:
            _jobs.pop(job_id, None)


def interrupt():
    try:
        from comfy_execution.utils import get_executing_context
        from comfy.model_management import throw_exception_if_processing_interrupted
    except ImportError:
        return
    if get_executing_context() is not None:
        throw_exception_if_processing_interrupted()


def _store():
    import folder_paths
    return Path(folder_paths.get_user_directory()) / "desktop_bridge" / "devices.json"


def _save():
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {key: {k: v for k, v in device.items() if k not in {"queue", "seen", "polling"}} for key, device in _devices.items() if not device.get("browser")}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def register():
    global _server
    from aiohttp import web
    from server import PromptServer
    _server = PromptServer.instance
    try:
        for key, device in json.loads(_store().read_text(encoding="utf-8")).items():
            _devices[key] = {**device, "queue": queue.Queue(), "seen": 0}
    except (OSError, ValueError):
        pass

    @web.middleware
    async def identity(request, handler):
        # Authorize at HTTP entry; never accept an owner supplied inside a workflow.
        # Media tags/model texture loaders cannot send Comfy-User headers. Their existing
        # unguessable preview URLs act as read capabilities, as with native /view URLs.
        needs_user = (request.path.startswith("/eagle_assets/") and request.method == "POST") or request.path.startswith(("/screen_capture/", "/desktop_bridge/")) or request.path in {"/prompt", "/api/prompt"}
        agent_route = request.path.startswith("/desktop_bridge/agent/")
        token = None
        try:
            if needs_user and not agent_route:
                token = OWNER.set(owner(request))
            if request.path in {"/prompt", "/api/prompt"} and request.method == "POST":
                data = await request.json()
                for node in data.get("prompt", {}).values():
                    inputs = node.get("inputs", {})
                    if node.get("class_type") == "DesktopScreenCapture":
                        inputs["bridge_auth"] = proof(OWNER.get())
                    value = inputs.get("settings")
                    if isinstance(value, str):
                        try:
                            settings = json.loads(value)
                            if str(settings.get("host", "")).startswith("bridge://"):
                                settings["_bridge_auth"] = proof(OWNER.get())
                                inputs["settings"] = json.dumps(settings, ensure_ascii=False)
                        except (ValueError, AttributeError):
                            pass
                request._read_bytes = json.dumps(data).encode("utf-8")
            return await handler(request)
        except (ValueError, KeyError) as exc:
            return web.json_response({"ok": False, "error": str(exc)[:400]}, status=403)
        finally:
            if token is not None:
                OWNER.reset(token)

    _server.app.middlewares.append(identity)
    routes = _server.routes

    def authenticate(request):
        device_id = request.headers.get("X-Desktop-Device", "")
        device = _devices.get(device_id)
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not device or not hmac.compare_digest(device["secret"], supplied):
            raise web.HTTPForbidden(text="Bridge authentication failed")
        device["seen"] = time.time()
        return device_id, device

    @routes.get("/desktop_bridge/devices")
    async def devices(request):
        user = owner(request)
        with _lock:
            values = [{"id": key, "name": d["name"], "capabilities": d["capabilities"], "online": online(key, d), "browser": bool(d.get("browser"))}
                      for key, d in _devices.items() if d["owner"] == user]
        return web.json_response({"devices": values})

    @routes.post("/desktop_bridge/pair")
    async def pair(request):
        code = secrets.token_hex(6).upper()
        with _lock:
            now = time.time()
            for key in list(_pairs):
                if _pairs[key][1] < now:
                    _pairs.pop(key, None)
            _pairs[code] = (owner(request), now + 300)
        return web.json_response({"code": code, "expires_in": 300})

    @routes.post("/desktop_bridge/agent/claim")
    async def claim(request):
        data = await request.json()
        with _lock:
            paired = _pairs.pop(str(data.get("code", "")).strip().upper(), None)
            if not paired or paired[1] < time.time():
                raise web.HTTPForbidden(text="Pairing code expired")
            device_id, secret = secrets.token_hex(16), secrets.token_hex(32)
            capabilities = [v for v in data.get("capabilities", []) if v in {"eagle", "capture"}]
            _devices[device_id] = {"owner": paired[0], "name": str(data.get("name", "电脑"))[:80], "secret": secret,
                                   "capabilities": capabilities, "queue": queue.Queue(), "seen": time.time()}
            _save()
        return web.json_response({"id": device_id, "secret": secret})

    @routes.post("/desktop_bridge/forget")
    async def forget(request):
        data = await request.json()
        with _lock:
            device = _devices.get(data.get("id"))
            if not device or device["owner"] != owner(request):
                raise web.HTTPForbidden()
            _devices.pop(data["id"])
            for job in _jobs.values():
                if job["device"] == data["id"]:
                    job["result"] = {"ok": False, "error": "设备授权已撤销"}
                    job["event"].set()
            _save()
        return web.json_response({"ok": True})

    @routes.post("/desktop_bridge/browser")
    async def browser(request):
        # Browser capabilities exist only while the authorized screen sharing session is active.
        device_id, secret = secrets.token_hex(16), secrets.token_hex(32)
        with _lock:
            for key in list(_devices):
                if _devices[key].get("browser") and time.time() - _devices[key].get("seen", 0) > 600 and not online(key, _devices[key]):
                    _devices.pop(key, None)
            _devices[device_id] = {"owner": owner(request), "name": "浏览器屏幕共享", "secret": secret, "capabilities": ["capture"], "browser": True, "queue": queue.Queue(), "seen": time.time()}
        return web.json_response({"id": device_id, "secret": secret})

    @routes.post("/desktop_bridge/agent/poll")
    async def poll(request):
        device_id, device = authenticate(request)
        with _lock:
            if device.get("polling"):
                raise web.HTTPConflict(text="Already polling")
            device["polling"] = True
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if _devices.get(device_id) is not device:
                    raise web.HTTPForbidden(text="Device authorization revoked")
                try:
                    job_id = device["queue"].get_nowait()
                except queue.Empty:
                    await asyncio.sleep(.15)
                    continue
                with _lock:
                    job = _jobs.get(job_id)
                if job and not job["event"].is_set() and job["deadline"] > time.time():
                    return web.json_response({k: job[k] for k in ("id", "op", "payload")})
            return web.json_response({"id": None})
        finally:
            device["polling"] = False

    @routes.post("/desktop_bridge/agent/result")
    async def result(request):
        device_id, _ = authenticate(request)
        request._client_max_size = MAX_JSON
        data = await request.json()
        with _lock:
            job = _jobs.get(data.get("id"))
            if not job or job["device"] != device_id or job["event"].is_set():
                raise web.HTTPNotFound()
            job["result"] = data
            job["event"].set()
        return web.json_response({"ok": True})

    @routes.post("/desktop_bridge/agent/active")
    async def active(request):
        device_id, _ = authenticate(request)
        data = await request.json()
        with _lock:
            job = _jobs.get(data.get("id"))
            valid = bool(job and job["device"] == device_id and job["deadline"] > time.time())
        return web.json_response({"active": valid})
