# -*- coding: utf-8 -*-
"""
`/api/v1` 服务端 —— 02 文档全部端点。

实现约束（04 文档 §5）：
  · 只用标准库（http.server + ThreadingHTTPServer），不引第三方 Web 框架
  · 长轮询 /vision/tasks/next 由 ThreadingHTTPServer 的每连接一线程承载
  · Bearer 校验、/auth/* 每设备限速 10 req/min
  · 统一错误格式 {"error": {"code","message","retryable"}}

RouterContext 把 TaskManager / PairingManager / AuthController 组装成
一个控制面，apiserver 只做 HTTP 编排，不含业务逻辑。
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .auth import AuthController, _Busy, _Unconfigured
from .pairing import PairingError, PairingManager
from .taskmanager import (RESULT_DUPLICATE, RESULT_EXPIRED, RESULT_FORBIDDEN,
                          RESULT_INVALID, RESULT_NOT_FOUND, RESULT_OK, TaskError,
                          TaskManager, task_to_claim, task_to_status)

# 错误码 → HTTP 状态
_HTTP_BY_CODE = {
    "E_BAD_REQUEST": 400,
    "E_UNAUTHORIZED": 401,
    "E_FORBIDDEN": 403,
    "E_NOT_FOUND": 404,
    "E_DUPLICATE": 409,
    "E_TASK_EXPIRED": 410,
    "E_IMAGE_TOO_LARGE": 413,
    "E_UNSUPPORTED_MEDIA": 415,
    "E_INVALID_RESULT": 422,
    "E_RATE_LIMITED": 429,
    "E_INTERNAL": 500,
    "E_BUSY": 503,
}

# TaskManager 结果码 → (HTTP, error_code)
_RESULT_TO_HTTP = {
    RESULT_NOT_FOUND: (404, "E_NOT_FOUND"),
    RESULT_FORBIDDEN: (403, "E_FORBIDDEN"),
    RESULT_DUPLICATE: (409, "E_DUPLICATE"),
    RESULT_EXPIRED: (410, "E_TASK_EXPIRED"),
    RESULT_INVALID: (422, "E_INVALID_RESULT"),
}


class RateLimiter:
    """固定窗口限速（/auth/* 每设备 10 req/min）。"""

    def __init__(self, limit: int = 10, window: float = 60.0):
        self._limit = limit
        self._window = window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self._window:
                q.popleft()
            if len(q) >= self._limit:
                return False
            q.append(now)
            return True


class RouterContext:
    """
    控制面：任务管理 + 配对 + 认证编排 + 只读元数据。
    """

    def __init__(self, *, taskmanager: TaskManager | None = None,
                 pairing: PairingManager | None = None,
                 auth: AuthController | None = None,
                 router_version: str = "0.1.0", portal_name: str = "",
                 model_hash: str = ""):
        self.taskmanager = taskmanager or TaskManager()
        self.pairing = pairing or PairingManager()
        self.auth = auth or AuthController(self.taskmanager)
        self.router_version = router_version
        self.portal_name = portal_name
        self.model_hash = model_hash
        self.started_at = time.time()
        self.accepting_nodes = True
        self._stats_lock = threading.Lock()
        self._stats = {"verified": 0, "failed": 0, "manual": 0}

    # ---- 统计（24h 计数用单调计数器近似，进程内）----
    def bump(self, key: str) -> None:
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + 1

    def stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)


class ApiServer:
    """HTTP 服务封装。serve_forever() 阻塞；start() 起守护线程。"""

    def __init__(self, context: RouterContext, host: str = "127.0.0.1",
                 port: int = 7700):
        self.context = context
        self.host = host
        self.port = port
        self._limiter = RateLimiter()
        ctx = context

        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "CampusLinkRouter/" + ctx.router_version

            # ---- 静音默认日志（走 campuslink.logging_setup）----
            def log_message(self, fmt, *args):
                return

            # ---- HTTP 方法分发 ----
            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

            def do_PUT(self):
                self._dispatch("PUT")

            def do_DELETE(self):
                self._dispatch("DELETE")

            # ================================================== 基础设施
            def _read_body(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n > 0 else b""

            def _json(self, data: dict, code: int = 200):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _error(self, code: str, message: str, retryable: bool = False,
                       http: int | None = None):
                self._json({"error": {"code": code, "message": message,
                                      "retryable": retryable}},
                           http or _HTTP_BY_CODE.get(code, 500))

            def _no_content(self):
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _ok_empty(self):
                """200 空体（探活 /health 用，02 文档约定仅状态码 200、不返回信息）。"""
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _binary(self, data: bytes, ctype: str):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body_json(self) -> dict:
                try:
                    raw = self._read_body()
                    if not raw:
                        return {}
                    return json.loads(raw.decode("utf-8"))
                except Exception:
                    return None

            # ---- Bearer 认证 ----
            def _device(self, *, required: bool = True):
                """返回 (device, token) 或 None（未认证时已回 401）。"""
                authz = self.headers.get("Authorization", "")
                token = ""
                if authz.startswith("Bearer "):
                    token = authz[7:].strip()
                dev = ctx.pairing.authenticate(token)
                if dev is None:
                    if required:
                        self._error("E_UNAUTHORIZED", "未授权：凭据缺失或无效")
                    return None
                return dev

            # ---- 路由 ----
            def _dispatch(self, method: str):
                parts = urlsplit(self.path)
                path = parts.path
                qs = parse_qs(parts.query)

                try:
                    self._route(method, path, qs)
                except TaskError as exc:
                    http, code = _RESULT_TO_HTTP.get(exc.code, (500, "E_INTERNAL"))
                    self._error(code, exc.message, http=http)
                except PairingError as exc:
                    self._error(exc.code, exc.message, retryable=exc.retryable)
                except _Busy:
                    self._error("E_BUSY", "认证正在进行中", retryable=True, http=503)
                except _Unconfigured:
                    self._error("E_BAD_REQUEST", "尚未配置账号信息")
                except BrokenPipeError:
                    pass
                except Exception:
                    self._error("E_INTERNAL", "服务端异常", retryable=True, http=500)

            def _route(self, method: str, path: str, qs: dict):
                seg = [s for s in path.split("/") if s]
                # 期望前缀 ["api","v1", ...]
                if len(seg) < 2 or seg[0] != "api" or seg[1] != "v1":
                    self._error("E_NOT_FOUND", "未知路径", http=404)
                    return
                seg = seg[2:]

                # ---- 公开 ----
                if seg == ["health"] and method == "GET":
                    self._ok_empty()
                    return
                if seg == ["capabilities"] and method == "GET":
                    self._capabilities()
                    return
                if seg == ["pair", "start"] and method == "POST":
                    self._pair_start()
                    return
                if seg == ["pair", "confirm"] and method == "POST":
                    self._pair_confirm()
                    return

                # ---- 需认证：先判路径是否已知（未知 → 404 优先于 401）----
                if not self._known_protected(method, seg):
                    self._error("E_NOT_FOUND", "未知路径", http=404)
                    return
                dev = self._device()
                if dev is None:
                    return
                device_id = dev.device_id
                ctx.pairing.touch(device_id)

                # 限速：/auth/*
                if seg and seg[0] == "auth":
                    if not outer._limiter.allow(device_id):
                        self._error("E_RATE_LIMITED", "请求过于频繁", retryable=True,
                                    http=429)
                        return

                if seg == ["auth", "status"] and method == "GET":
                    self._json(ctx.auth.status())
                    return
                if seg == ["auth", "login"] and method == "POST":
                    self._auth_login()
                    return
                if seg == ["auth", "reconnect"] and method == "POST":
                    self._auth_reconnect()
                    return
                if seg == ["auth", "credentials"] and method == "PUT":
                    self._auth_credentials()
                    return
                if seg == ["auth", "credentials"] and method == "DELETE":
                    self._auth_clear_credentials()
                    return
                if len(seg) == 3 and seg[0] == "auth" and seg[1] == "tasks" \
                        and method == "GET":
                    self._auth_task(seg[2])
                    return

                if seg == ["pair", "devices"] and method == "GET":
                    self._json({"devices": ctx.pairing.list_devices()})
                    return
                if len(seg) == 3 and seg[0] == "pair" and seg[1] == "devices" \
                        and method == "DELETE":
                    self._revoke(seg[2])
                    return

                if seg == ["system", "status"] and method == "GET":
                    self._system_status()
                    return

                if seg == ["vision", "tasks", "next"] and method == "GET":
                    self._next_task(device_id, qs)
                    return
                if len(seg) == 4 and seg[0] == "vision" and seg[1] == "tasks" \
                        and seg[3] == "image" and method == "GET":
                    self._task_image(device_id, seg[2])
                    return
                if len(seg) == 4 and seg[0] == "vision" and seg[1] == "tasks" \
                        and seg[3] == "result" and method == "POST":
                    self._task_result(device_id, seg[2])
                    return
                if len(seg) == 3 and seg[0] == "vision" and seg[1] == "tasks" \
                        and method == "DELETE":
                    self._task_cancel(device_id, seg[2])
                    return
                if len(seg) == 3 and seg[0] == "vision" and seg[1] == "tasks" \
                        and method == "GET":
                    self._task_get(seg[2])
                    return

                self._error("E_NOT_FOUND", "未知路径", http=404)

            def _known_protected(self, method: str, seg: list) -> bool:
                """判断 (method, seg) 是否命中某个需认证的已知端点。"""
                if method == "GET":
                    return (seg == ["auth", "status"]
                            or (len(seg) == 3 and seg[:2] == ["auth", "tasks"])
                            or seg == ["pair", "devices"]
                            or seg == ["system", "status"]
                            or seg == ["vision", "tasks", "next"]
                            or (len(seg) == 4 and seg[:2] == ["vision", "tasks"]
                                and seg[3] == "image")
                            or (len(seg) == 3 and seg[:2] == ["vision", "tasks"]))
                if method == "POST":
                    return (seg == ["auth", "login"]
                            or seg == ["auth", "reconnect"]
                            or (len(seg) == 4 and seg[:2] == ["vision", "tasks"]
                                and seg[3] == "result"))
                if method == "PUT":
                    return seg == ["auth", "credentials"]
                if method == "DELETE":
                    return ((len(seg) == 3 and seg[:2] == ["pair", "devices"])
                            or (len(seg) == 3 and seg[:2] == ["vision", "tasks"])
                            or seg == ["auth", "credentials"])
                return False

            # ================================================== 端点
            def _capabilities(self):
                auth = ctx.auth
                st = auth.status()
                self._json({
                    "protocol": "campuslink",
                    "api_version": "1.0",
                    "router_version": ctx.router_version,
                    "auth": {"mode": "router", "portal": ctx.portal_name,
                             "state": st["state"]},
                    "vision": {
                        "accepting_nodes": ctx.accepting_nodes,
                        "active_nodes": ctx.pairing.count(),
                        "pending_tasks": ctx.taskmanager.pending_count,
                        "model_hash": ctx.model_hash or None,
                    },
                    "features": ["vision_tasks", "manual_input", "pairing"],
                    "pairing_required": ctx.pairing.count() == 0,
                })

            def _pair_start(self):
                self._json(ctx.pairing.start_pairing())

            def _pair_confirm(self):
                body = self._body_json()
                if body is None:
                    self._error("E_BAD_REQUEST", "JSON 解析失败")
                    return
                self._json(ctx.pairing.confirm(
                    body.get("pin", ""),
                    device_name=body.get("device_name", ""),
                    device_kind=body.get("device_kind", "desktop"),
                    capabilities=body.get("capabilities", []) or [],
                ))

            def _revoke(self, device_id: str):
                if ctx.pairing.revoke(device_id):
                    self._no_content()
                else:
                    self._error("E_NOT_FOUND", "设备不存在", http=404)

            def _auth_login(self):
                body = self._body_json()
                if body is None:
                    body = {}
                wait = bool(body.get("wait", False))
                try:
                    self._json(ctx.auth.login(wait=wait), code=202)
                except _Busy:
                    self._error("E_BUSY", "认证正在进行中", retryable=True, http=503)

            def _auth_reconnect(self):
                body = self._body_json()
                if body is None:
                    body = {}
                ctx.auth.set_reconnect(bool(body.get("enabled", True)))
                self._json({"enabled": ctx.auth.auto_reconnect})

            def _auth_credentials(self):
                body = self._body_json()
                if body is None:
                    self._error("E_BAD_REQUEST", "JSON 解析失败")
                    return
                student_id = str(body.get("student_id", "") or "").strip()
                password = body.get("password", "")
                if not student_id or not password:
                    self._error("E_BAD_REQUEST", "student_id 与 password 均必填")
                    return
                summary = ctx.auth.set_credentials(
                    student_id=student_id,
                    account=str(body.get("account", "") or ""),
                    operator=str(body.get("operator", "") or "自动选择"),
                    password=password,
                )
                self._json({"configured": summary["configured"],
                            "student_id": summary["student_id"],
                            "operator": summary["operator"]})

            def _auth_clear_credentials(self):
                ctx.auth.clear_credentials()
                self._no_content()

            def _auth_task(self, task_id: str):
                t = ctx.auth.auth_task(task_id)
                if t is None:
                    self._error("E_NOT_FOUND", "认证任务不存在", http=404)
                    return
                self._json(t.public())

            def _next_task(self, device_id: str, qs: dict):
                wait = 25.0
                try:
                    wait = float((qs.get("wait") or ["25"])[0])
                except (ValueError, IndexError):
                    wait = 25.0
                t = ctx.taskmanager.next_task(device_id, wait=wait)
                if t is None:
                    self._no_content()
                    return
                self._json(task_to_claim(t))

            def _task_image(self, device_id: str, task_id: str):
                data, ctype, _ = ctx.taskmanager.get_image(task_id, device_id)
                self._binary(data, ctype)

            def _task_result(self, device_id: str, task_id: str):
                body = self._body_json()
                if body is None:
                    self._error("E_BAD_REQUEST", "JSON 解析失败")
                    return
                code, t = ctx.taskmanager.submit(
                    task_id, device_id,
                    nonce=body.get("nonce", ""),
                    text=body.get("text", ""),
                    source=body.get("source", "ocr"),
                    confidence=body.get("confidence", 0.0),
                    detail=body.get("detail", {}) or {},
                    elapsed_ms=body.get("elapsed_ms", 0),
                )
                if code == RESULT_OK:
                    src = (t.result.source if t and t.result else "ocr")
                    ctx.bump("manual" if src == "manual" else "verified")
                    self._json({"accepted": True})
                    return
                http, errcode = _RESULT_TO_HTTP.get(code, (500, "E_INTERNAL"))
                msg = {
                    RESULT_DUPLICATE: "该任务已有结果或已被领取",
                    RESULT_EXPIRED: "任务已过期或已取消",
                    RESULT_INVALID: f"期望 {t.expect_length if t else '?'} 位、"
                                    f"字符集 {t.charset_hint if t else 'alnum'}",
                    RESULT_NOT_FOUND: "任务不存在",
                    RESULT_FORBIDDEN: "无权操作该任务",
                }.get(code, "提交失败")
                self._error(errcode, msg, http=http)

            def _task_cancel(self, device_id: str, task_id: str):
                code = ctx.taskmanager.cancel(task_id, device_id)
                if code == RESULT_OK:
                    self._no_content()
                    return
                http, errcode = _RESULT_TO_HTTP.get(code, (500, "E_INTERNAL"))
                self._error(errcode, "无法取消该任务", http=http)

            def _task_get(self, task_id: str):
                t = ctx.taskmanager.get(task_id)
                if t is None:
                    self._error("E_NOT_FOUND", "任务不存在", http=404)
                    return
                self._json(task_to_status(t))

            def _system_status(self):
                st = ctx.auth.status()
                stats = ctx.stats()
                self._json({
                    "uptime_s": int(time.time() - ctx.started_at),
                    "network": {"state": st["state"], "local_ip": "",
                                "portal": st["portal_url"]},
                    "vision_nodes": ctx.pairing.list_devices(),
                    "tasks": {
                        "pending": ctx.taskmanager.pending_count,
                        "verified_24h": stats.get("verified", 0),
                        "failed_24h": stats.get("failed", 0),
                        "manual_24h": stats.get("manual", 0),
                    },
                    "storage": {"overlay_free_mb": None},
                })

        self._handler = Handler

    # ---- 生命周期 ----
    def make_server(self) -> ThreadingHTTPServer:
        return ThreadingHTTPServer((self.host, self.port), self._handler)

    def serve_forever(self):
        self._server = self.make_server()
        self._server.daemon_threads = True
        try:
            self._server.serve_forever()
        finally:
            self._server.server_close()

    def start(self) -> ThreadingHTTPServer:
        """起守护线程，返回 server（供测试/模拟器用）。"""
        srv = self.make_server()
        srv.daemon_threads = True
        self._server = srv
        t = threading.Thread(target=srv.serve_forever, daemon=True,
                             name="api-server")
        t.start()
        return srv

    def shutdown(self):
        srv = getattr(self, "_server", None)
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
