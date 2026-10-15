# -*- coding: utf-8 -*-
"""
认证编排（Router 控制面）—— 把 core 的 Authenticator 接到 RemoteVisionBackend 上。

职责（01 文档 §1）：
  · 认证编排：重试、验证码去重、取消 —— 复用 core.Authenticator
  · OCR 走 RemoteVisionBackend（远程视觉节点）
  · 给 Desktop/Mobile 提供 /auth/status、/auth/login、/auth/tasks、/auth/reconnect

认证在后台线程串行执行（Router 一次只跑一路认证，进行中再触发返回 503 E_BUSY）。

OCR 接缝：`Authenticator.__init__` 已支持 `ocr_backend` 参数（§A1，Agent 2 交付），
本模块直接 `Authenticator(profile, ocr_backend=self._backend)` 注入 RemoteVisionBackend。
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

from .remotecomplete import OcrBackend, RemoteVisionBackend
from .taskmanager import TaskManager


@dataclass
class AuthTask:
    """一次认证尝试（03 文档 §1）。"""
    task_id: str            # "auth_" + hex
    state: str              # checking|submitting|online|failed|cancelled
    attempts: int
    message: str
    raw_message: str
    started_at: float
    finished_at: float | None = None

    def public(self) -> dict:
        return {
            "task_id": self.task_id,
            "state": self.state,
            "attempts": self.attempts,
            "message": self.message,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at) if self.finished_at else None,
        }


class AuthController:
    """
    认证编排器。构造时注入 TaskManager 与可选 profile/credentials。
    credentials（含密码）仅存内存，绝不落盘（03 文档 §5）。
    """

    def __init__(self, taskmanager: TaskManager, *, profile=None, credentials=None,
                 backend: OcrBackend | None = None, online_check=None):
        self._tm = taskmanager
        self._backend = backend or RemoteVisionBackend(taskmanager)
        self.profile = profile
        self.credentials = credentials
        self.auto_reconnect = True
        self.online_check = online_check        # callable() -> bool|None
        self._lock = threading.RLock()
        self._current: AuthTask | None = None
        self._last_result = None                # LoginResult
        self._last_online: bool | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ 状态
    def status(self) -> dict:
        with self._lock:
            busy = self._current is not None and self._current.finished_at is None
            state = self._derive_state(busy)
            last = self._last_result
            cred = self.credentials
            student_id = (cred.student_id if cred else "") or ""
            masked = (student_id[:4] + "****" + student_id[-3:]) \
                if len(student_id) >= 7 else (student_id and (student_id[:2] + "****"))
            return {
                "state": state,
                "portal_url": (self.profile.portal_url if self.profile else ""),
                "last_result": {
                    "ok": last.ok,
                    "message": last.message,
                    "at": _iso(time.time()),
                } if last else None,
                "session": {
                    "established": bool(last and last.ok),
                    "since": _iso(time.time()) if (last and last.ok) else None,
                    "expires_hint": None,
                },
                "account": {
                    "student_id": masked,
                    "operator": (cred.operator if cred else "自动选择"),
                },
                "auto_reconnect": self.auto_reconnect,
            }

    def _derive_state(self, busy: bool) -> str:
        if busy:
            return "checking"
        online = self.online_check() if self.online_check else None
        if online is True:
            return "online"
        last = self._last_result
        if last is not None:
            return "online" if last.ok else "need_login"
        return "unknown"

    # ------------------------------------------------------------ 触发
    def login(self, wait: bool = False) -> dict:
        """
        触发一次认证。已在进行中返回 503（E_BUSY）。wait=True 时阻塞至完成。
        """
        with self._lock:
            if self._current is not None and self._current.finished_at is None:
                raise _Busy("认证正在进行中")
            if self.profile is None or self.credentials is None:
                raise _Unconfigured("尚未配置账号信息")
            t = AuthTask(task_id="auth_" + secrets.token_hex(4), state="checking",
                         attempts=0, message="认证中", raw_message="",
                         started_at=time.time())
            self._current = t

        if wait:
            self._run_auth(t)
            return {"task_id": t.task_id, "state": t.state}

        self._thread = threading.Thread(target=self._run_auth, args=(t,),
                                        name="auth-runner", daemon=True)
        self._thread.start()
        return {"task_id": t.task_id, "state": t.state}

    def _run_auth(self, t: AuthTask) -> None:
        try:
            from campuslink.core.authenticator import Authenticator
            # §A1：直接把 RemoteVisionBackend 注入 authenticator（Agent 2 已落地参数）
            auth = Authenticator(self.profile, ocr_backend=self._backend)
            result = auth.login(
                self.credentials,
                on_progress=lambda m: self._set_progress(t, m),
            )
            with self._lock:
                t.attempts = result.attempts
                t.raw_message = result.raw_message
                t.message = result.message
                t.state = "online" if result.ok else "failed"
                t.finished_at = time.time()
                self._last_result = result
                self._last_online = result.ok
        except Exception as exc:   # 兜底：任何异常都收敛成失败，不炸线程
            with self._lock:
                t.attempts = max(t.attempts, 0)
                t.message = getattr(exc, "user_message", None) or str(exc)
                t.state = "failed"
                t.finished_at = time.time()

    def _set_progress(self, t: AuthTask, msg: str) -> None:
        with self._lock:
            t.message = msg
            if "提交" in msg:
                t.state = "submitting"

    def cancel_current(self) -> None:
        with self._lock:
            if self._current is not None and self._current.finished_at is None:
                self._current.state = "cancelled"
                self._current.finished_at = time.time()

    # ------------------------------------------------------------ 查询/开关
    def auth_task(self, task_id: str) -> AuthTask | None:
        with self._lock:
            if self._current and self._current.task_id == task_id:
                return self._current
        return None

    def set_reconnect(self, enabled: bool) -> None:
        with self._lock:
            self.auto_reconnect = bool(enabled)

    # ------------------------------------------------------------ 凭据下发
    def set_credentials(self, student_id: str, account: str, operator: str,
                        password: str) -> dict:
        """
        方案 A：已配对设备下发的凭据，仅存内存（重启即失，绝不落盘）。

        profile 若尚未加载（_wire_auth 失败），此处仍存凭据但 login 会报
        「尚未配置」——所以调用方应先确保 profile 已就绪（内置档案即满足）。
        返回脱敏后的摘要（绝不回显密码）。
        """
        from campuslink.core.models import Credentials
        from campuslink.logging_setup import register_secret

        if password:
            register_secret(password)          # 日志脱敏登记
        cred = Credentials(
            student_id=(student_id or "").strip(),
            account=(account or "").strip(),
            password=password or "",
            operator=(operator or "自动选择"),
        ).normalized()
        with self._lock:
            self.credentials = cred
        sid = cred.student_id
        masked = (sid[:4] + "****" + sid[-3:]) if len(sid) >= 7 \
            else (sid and (sid[:2] + "****"))
        return {"configured": cred.is_complete(),
                "student_id": masked, "operator": cred.operator}

    def clear_credentials(self) -> None:
        with self._lock:
            self.credentials = None


class _Busy(Exception):
    pass


class _Unconfigured(Exception):
    pass


def _iso(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
