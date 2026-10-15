# -*- coding: utf-8 -*-
"""
视觉任务管理器 —— 任务生命周期状态机（03 文档 §3）。

状态机：

    pending ──领取──> assigned ──提交──> submitted ──校验──> verified
       ▲  ▲              │  │                 │
       │  │ TTL(90s)     │  │ 30s 未提交       │ 长度/字符集不符
       │  └──────────────┘  ▼                 ▼
       │             pending(重派)           rejected
       │ 认证结束(取消)
       ▼
    cancelled <────(任意状态，仅 Router 可发起)

不变式（03 文档）：
  · verified / rejected / expired / cancelled 是终态，不可逆
  · nonce 一经使用即作废（哪怕校验失败）
  · 同一节点同时最多持有 1 个 assigned 任务
  · 全局 pending ≤ max_pending（默认 8）

线程安全：单把 Condition 既做互斥又做长轮询/结果等待的信号。
reap（过期/重派）是惰性的 —— 每次公开操作前顺手做一遍，避免后台线程
引入的不确定性，也让契约测试完全可复现。
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---- 可调参数（03 文档给定值）----
TTL = 90.0                # 任务整体寿命（秒）
ASSIGNED_TIMEOUT = 30.0   # 领取后未提交的宽限（秒）
MAX_PENDING = 8           # 全局 pending 上限（对齐门户 max_retry）


class TaskState(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


#: 终态 —— 不可逆
TERMINAL = frozenset({
    TaskState.VERIFIED, TaskState.REJECTED,
    TaskState.EXPIRED, TaskState.CANCELLED,
})

# 结果提交的返回码（供 apiserver 映射到 HTTP）
RESULT_OK = "ok"
RESULT_NOT_FOUND = "not_found"      # 404
RESULT_FORBIDDEN = "forbidden"      # 403
RESULT_DUPLICATE = "duplicate"      # 409（nonce 已用 / 已被他人领取）
RESULT_EXPIRED = "expired"          # 410
RESULT_INVALID = "invalid"          # 422

_CHARSET_RE = {
    "digits": re.compile(r"^[0-9]+$"),
    "lower": re.compile(r"^[a-z]+$"),
    "upper": re.compile(r"^[A-Z]+$"),
    "alnum": re.compile(r"^[0-9A-Za-z]+$"),
}


@dataclass
class ImageMeta:
    format: str            # "png" | "jpeg"
    width: int
    height: int
    bytes: int


@dataclass
class TaskResult:
    text: str
    source: str            # "ocr" | "manual"
    confidence: float
    detail: dict[str, str]
    elapsed_ms: int
    submitted_at: float


@dataclass
class VisionTask:
    task_id: str
    nonce: str                  # 领取时签发；单次有效
    image: bytes | None         # 仅内存；任务结束即置 None（决策 D5）
    image_meta: ImageMeta
    expect_length: int
    charset_hint: str           # digits|lower|upper|alnum
    strategies_hint: tuple[str, ...]
    allow_manual: bool
    state: TaskState
    created_at: float
    expires_at: float
    assigned_to: str | None = None
    result: TaskResult | None = None
    auth_attempt_id: str = ""   # 内部关联，不外泄
    # 内部记账（不进任何 API 响应）
    nonce_used: bool = False
    assigned_at: float = 0.0

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL


def new_task_id() -> str:
    return "cap_" + secrets.token_hex(4)       # 8 hex


def new_nonce() -> str:
    return secrets.token_hex(8)                # 16 hex


class TaskManager:
    def __init__(self, *, ttl: float = TTL, assigned_timeout: float = ASSIGNED_TIMEOUT,
                 max_pending: int = MAX_PENDING, retention: float = 300.0):
        self._ttl = ttl
        self._assigned_timeout = assigned_timeout
        self._max_pending = max_pending
        self._retention = retention      # 终态任务保留时长（秒），用于统计
        self._cond = threading.Condition()
        self._tasks: dict[str, VisionTask] = {}

    # ------------------------------------------------------------ 生命周期
    @property
    def pending_count(self) -> int:
        with self._cond:
            return sum(1 for t in self._tasks.values()
                       if t.state is TaskState.PENDING)

    def _inflight_locked(self) -> int:
        """在途任务数 = 非终态任务数（pending + assigned + submitted）。"""
        return sum(1 for t in self._tasks.values() if not t.is_terminal)

    def create(self, image: bytes, *, expect_length: int, charset_hint: str = "alnum",
               strategies_hint: tuple[str, ...] = ("raw", "gray"),
               allow_manual: bool = False, auth_attempt_id: str = "",
               image_meta: ImageMeta | None = None,
               validate_image: bool = True) -> VisionTask | None:
        """
        新建一个 pending 任务。全局 pending 已满则返回 None（调用方转 503）。

        图像校验（02 文档）：PNG/JPEG、≤150KB、宽≤400、高≤160。
        不合规抛 TaskError（E_IMAGE_TOO_LARGE / E_UNSUPPORTED_MEDIA）。
        """
        if validate_image:
            from .images import validate as _img_validate
            code = _img_validate(image)
            if code:
                msg = ("图像格式非 PNG/JPEG" if code == "E_UNSUPPORTED_MEDIA"
                       else "图像超过尺寸限制")
                raise TaskError(code, msg)
        with self._cond:
            self._reap_locked()
            # 上限按「在途（非终态）任务数」计 —— 这样 assigned 任务被重派
            # 回 pending 也不会突破全局上限（pending 恒为在途子集）。
            if self._inflight_locked() >= self._max_pending:
                return None
            now = time.time()
            t = VisionTask(
                task_id=new_task_id(),
                nonce="",                    # 领取时才签发
                image=image,
                image_meta=image_meta or _meta_from_bytes(image),
                expect_length=expect_length,
                charset_hint=charset_hint,
                strategies_hint=tuple(strategies_hint),
                allow_manual=allow_manual,
                state=TaskState.PENDING,
                created_at=now,
                expires_at=now + self._ttl,
                auth_attempt_id=auth_attempt_id,
            )
            self._tasks[t.task_id] = t
            self._cond.notify_all()
            return t

    # ------------------------------------------------------------ 领取（长轮询）
    def next_task(self, device_id: str, wait: float = 25.0) -> VisionTask | None:
        """
        长轮询领取一个 pending 任务。无任务时挂起至多 wait 秒。
        返回 None 表示超时（apiserver 回 204）。

        「每节点 1 并发」：该节点已持有 assigned 任务时不再给新的，
        直接返回 None（节点应先完成手头的任务）。
        """
        wait = max(0.0, min(float(wait), 55.0))    # 上限 55s（02 文档）
        deadline = time.monotonic() + wait
        with self._cond:
            while True:
                self._reap_locked()
                if not self._holds_assigned_locked(device_id):
                    t = self._claim_locked(device_id)
                    if t is not None:
                        return t
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def _holds_assigned_locked(self, device_id: str) -> bool:
        return any(t.state is TaskState.ASSIGNED and t.assigned_to == device_id
                   for t in self._tasks.values())

    def _claim_locked(self, device_id: str) -> VisionTask | None:
        now = time.time()
        for t in self._tasks.values():
            if t.state is TaskState.PENDING:
                t.state = TaskState.ASSIGNED
                t.assigned_to = device_id
                t.assigned_at = now
                t.nonce = new_nonce()
                t.nonce_used = False
                self._cond.notify_all()
                return t
        return None

    # ------------------------------------------------------------ 图像
    def get_image(self, task_id: str, device_id: str) -> tuple[bytes, str, ImageMeta]:
        """
        取图像（一次性语义）。返回 (bytes, content_type, meta)。
        出错时抛 _TaskError（apiserver 转错误响应）。
        """
        with self._cond:
            self._reap_locked()
            t = self._tasks.get(task_id)
            if t is None:
                raise TaskError(RESULT_NOT_FOUND, "任务不存在")
            if t.state is TaskState.EXPIRED:
                raise TaskError(RESULT_EXPIRED, "任务已过期")
            if t.state is TaskState.CANCELLED:
                raise TaskError(RESULT_EXPIRED, "任务已取消")
            if t.state is not TaskState.ASSIGNED or t.assigned_to != device_id:
                raise TaskError(RESULT_FORBIDDEN, "任务未分配给你")
            if not t.image:
                raise TaskError(RESULT_EXPIRED, "图像已释放")
            ctype = "image/png" if t.image_meta.format == "png" else "image/jpeg"
            return t.image, ctype, t.image_meta

    # ------------------------------------------------------------ 提交
    def submit(self, task_id: str, device_id: str, *, nonce: str, text: str,
               source: str, confidence: float, detail: dict[str, str],
               elapsed_ms: int) -> tuple[str, VisionTask | None]:
        """
        提交结果。返回 (结果码, 任务)。结果码见模块顶部常量。
        """
        with self._cond:
            self._reap_locked()
            t = self._tasks.get(task_id)
            if t is None:
                return RESULT_NOT_FOUND, None
            if t.state is TaskState.EXPIRED:
                return RESULT_EXPIRED, t
            if t.state is TaskState.CANCELLED:
                return RESULT_EXPIRED, t
            if t.state is TaskState.VERIFIED or t.state is TaskState.REJECTED:
                return RESULT_DUPLICATE, t
            if t.state is not TaskState.ASSIGNED or t.assigned_to != device_id:
                return RESULT_DUPLICATE, t       # 已被其它节点领取 / 未领取

            # nonce 单次：先判定、再作废（哪怕结果校验失败）
            if t.nonce_used or nonce != t.nonce or not nonce:
                t.nonce_used = True
                return RESULT_DUPLICATE, t
            t.nonce_used = True

            t.state = TaskState.SUBMITTED
            if not _valid_result(t, text, source):
                t.state = TaskState.REJECTED
                self._cond.notify_all()
                return RESULT_INVALID, t

            t.state = TaskState.VERIFIED
            t.result = TaskResult(
                text=text, source=source, confidence=float(confidence or 0.0),
                detail=dict(detail or {}), elapsed_ms=int(elapsed_ms or 0),
                submitted_at=time.time(),
            )
            t.image = None                      # 任务结束即释放图像（D5）
            self._cond.notify_all()
            return RESULT_OK, t

    # ------------------------------------------------------------ 取消
    def cancel(self, task_id: str, device_id: str | None = None,
               internal: bool = False) -> str:
        """
        取消任务。节点侧只能取消自己持有的 assigned 任务（回 pending）；
        Router 内部（internal=True）可取消任意状态（转 cancelled 终态）。
        """
        with self._cond:
            t = self._tasks.get(task_id)
            if t is None:
                return RESULT_NOT_FOUND
            if t.is_terminal:
                return RESULT_DUPLICATE
            if internal:
                t.state = TaskState.CANCELLED
                t.image = None
                self._cond.notify_all()
                return RESULT_OK
            if t.state is not TaskState.ASSIGNED or t.assigned_to != device_id:
                return RESULT_FORBIDDEN
            t.state = TaskState.PENDING       # 节点放弃 → 重新入队
            t.assigned_to = None
            t.nonce = ""
            t.nonce_used = True               # 旧 nonce 作废
            self._cond.notify_all()
            return RESULT_OK

    # ------------------------------------------------------------ 查询
    def get(self, task_id: str) -> VisionTask | None:
        with self._cond:
            self._reap_locked()
            return self._tasks.get(task_id)

    def snapshot(self) -> list[VisionTask]:
        with self._cond:
            self._reap_locked()
            return list(self._tasks.values())

    def wait_result(self, task_id: str, timeout: float = 60.0) -> VisionTask | None:
        """阻塞等待某任务进入终态（供 RemoteVisionBackend 同步等待）。"""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                t = self._tasks.get(task_id)
                if t is None or t.is_terminal:
                    return t
                self._reap_locked()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return t
                self._cond.wait(remaining)

    # ------------------------------------------------------------ 到期/重派
    def reap(self) -> int:
        """主动触发一次过期/重派，返回状态发生变更的任务数。"""
        with self._cond:
            return self._reap_locked()

    def _reap_locked(self) -> int:
        now = time.time()
        changed = 0
        for t in self._tasks.values():
            if t.is_terminal:
                continue
            if t.state is TaskState.PENDING and now >= t.expires_at:
                t.state = TaskState.EXPIRED
                t.image = None
                changed += 1
            elif t.state is TaskState.ASSIGNED:
                if now >= t.expires_at:
                    t.state = TaskState.EXPIRED
                    t.image = None
                    changed += 1
                elif now - t.assigned_at >= self._assigned_timeout:
                    t.state = TaskState.PENDING      # 节点失联 → 重派
                    t.assigned_to = None
                    t.nonce = ""
                    t.nonce_used = True
                    changed += 1
            elif t.state is TaskState.SUBMITTED:
                if now >= t.expires_at:
                    t.state = TaskState.EXPIRED
                    t.image = None
                    changed += 1
        # 剪枝：终态任务保留 retention 秒后丢弃（控制常驻内存，配合 48h 长稳）
        for task_id in [tid for tid, t in self._tasks.items()
                        if t.is_terminal and (now - _terminal_at(t)) > self._retention]:
            del self._tasks[task_id]
        if changed:
            self._cond.notify_all()
        return changed


class TaskError(Exception):
    """任务相关错误，携带结果码（apiserver 据此映射 HTTP 状态）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- 工具
def _meta_from_bytes(image: bytes) -> ImageMeta:
    from .images import image_size, sniff_format
    fmt = sniff_format(image)
    size = image_size(image) or (0, 0)
    return ImageMeta(format=fmt or "png", width=size[0], height=size[1],
                     bytes=len(image))


def _terminal_at(t: VisionTask) -> float:
    """终态任务的「终结时刻」，用于剪枝。"""
    if t.result is not None:
        return t.result.submitted_at
    return t.expires_at


def _valid_result(t: VisionTask, text: str, source: str) -> bool:
    if source not in ("ocr", "manual"):
        return False
    text = (text or "").strip()
    if not text:
        return False
    if t.expect_length and len(text) != t.expect_length:
        return False
    rx = _CHARSET_RE.get(t.charset_hint)
    if rx is not None and not rx.match(text):
        return False
    return True


# ---------------------------------------------------------------- 序列化
def task_to_claim(task: VisionTask, base_path: str = "") -> dict[str, Any]:
    """GET /vision/tasks/next 的 200 响应体。不含 auth_attempt_id 等内部字段。"""
    return {
        "task_id": task.task_id,
        "nonce": task.nonce,
        "created_at": _iso(task.created_at),
        "expires_at": _iso(task.expires_at),
        "image_url": f"/api/v1/vision/tasks/{task.task_id}/image",
        "image_meta": {
            "format": task.image_meta.format,
            "width": task.image_meta.width,
            "height": task.image_meta.height,
            "bytes": task.image_meta.bytes,
        },
        "length": task.expect_length,
        "charset_hint": task.charset_hint,
        "allow_manual": task.allow_manual,
        "strategies_hint": list(task.strategies_hint),
    }


def task_to_status(task: VisionTask) -> dict[str, Any]:
    """GET /vision/tasks/{id} 的响应体（调试用）。"""
    d = {
        "task_id": task.task_id,
        "state": task.state.value,
        "created_at": _iso(task.created_at),
        "expires_at": _iso(task.expires_at),
        "assigned_to": task.assigned_to,
        "allow_manual": task.allow_manual,
    }
    if task.result is not None:
        d["result"] = {
            "text": task.result.text,
            "source": task.result.source,
            "confidence": task.result.confidence,
            "elapsed_ms": task.result.elapsed_ms,
        }
    return d


def _iso(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
