# -*- coding: utf-8 -*-
"""
视觉节点 API 客户端 —— 02 文档客户端侧实现。

只依赖 `requests`（项目已有，不新增依赖）。mDNS 发现（zeroconf）按任务书
约定不引入依赖，Router 地址由用户手动输入 IP:端口。

方法都是同步阻塞的；HTTP/网络错误统一抛 `NodeError`（带人话），由调用方
（node 主循环 / watcher / GUI）决定重试还是降级。
"""
from __future__ import annotations

import requests


class NodeError(Exception):
    """视觉节点与 Router 通信失败。message 可直接展示给用户。"""


class NodeClient:
    def __init__(self, base_url: str, token: str | None = None,
                 timeout: float = 30.0):
        # 容忍用户多输斜杠、以及只填 "IP:端口" 不带 http:// ——
        # requests 缺 scheme 会抛 InvalidSchema，这里自动补上。
        base_url = (base_url or "").strip().rstrip("/")
        if base_url and "://" not in base_url:
            base_url = "http://" + base_url
        self.base_url = base_url
        self.token = token or ""
        self.timeout = timeout

    # ------------------------------------------------------------ 基础
    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _check(r: requests.Response) -> requests.Response:
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except Exception:
                msg = r.text or f"HTTP {r.status_code}"
            raise NodeError(msg)
        return r

    def _get(self, path: str, *, timeout: float | None = None, **kw) -> requests.Response:
        try:
            return self._check(requests.get(self._url(path),
                                            headers=self.headers,
                                            timeout=timeout or self.timeout, **kw))
        except requests.RequestException as exc:
            raise NodeError(f"无法连接 Router：{type(exc).__name__}") from exc

    def _post(self, path: str, json=None, *, timeout: float | None = None) -> requests.Response:
        try:
            return self._check(requests.post(self._url(path), json=json,
                                             headers=self.headers,
                                             timeout=timeout or self.timeout))
        except requests.RequestException as exc:
            raise NodeError(f"无法连接 Router：{type(exc).__name__}") from exc

    def _delete(self, path: str) -> requests.Response:
        try:
            return self._check(requests.delete(self._url(path),
                                               headers=self.headers,
                                               timeout=self.timeout))
        except requests.RequestException as exc:
            raise NodeError(f"无法连接 Router：{type(exc).__name__}") from exc

    # ------------------------------------------------------------ 探活
    def health(self) -> bool:
        """轻量探活，返回 Router 是否可达。不抛异常。"""
        try:
            r = requests.get(self._url("/api/v1/health"),
                             headers=self.headers, timeout=min(self.timeout, 5.0))
            return r.status_code == 200
        except requests.RequestException:
            return False

    # ------------------------------------------------------------ 配对
    def pair_start(self) -> dict:
        return self._post("/api/v1/pair/start", json={}).json()

    def pair_confirm(self, pin: str, *, device_name: str, device_kind: str,
                     capabilities: list[str]) -> dict:
        """用 PIN 换 device_token。成功返回含 device_token 的 dict。"""
        return self._post("/api/v1/pair/confirm", json={
            "pin": pin, "device_name": device_name, "device_kind": device_kind,
            "capabilities": capabilities,
        }).json()

    def set_token(self, token: str) -> None:
        self.token = token or ""

    # ------------------------------------------------------------ 任务
    def next_task(self, wait: float = 25.0) -> dict | None:
        """长轮询领取任务。无任务（204）返回 None。"""
        wait = max(0.0, min(float(wait), 55.0))
        r = self._get(f"/api/v1/vision/tasks/next?wait={wait}",
                      timeout=wait + 5.0)
        if r.status_code == 204:
            return None
        return r.json()

    def get_image(self, image_url: str) -> bytes:
        r = self._get(image_url, timeout=self.timeout)
        return r.content

    def submit_result(self, task_id: str, nonce: str, text: str, source: str,
                      confidence: float, detail: dict, elapsed_ms: int) -> bool:
        """提交结果。返回是否被接受。"""
        r = self._post(f"/api/v1/vision/tasks/{task_id}/result", json={
            "nonce": nonce, "text": text, "source": source,
            "confidence": confidence, "detail": detail or {}, "elapsed_ms": elapsed_ms,
        })
        return bool(r.json().get("accepted"))

    def cancel_task(self, task_id: str) -> bool:
        """节点侧主动放弃任务（回 pending）。返回是否成功。"""
        r = self._delete(f"/api/v1/vision/tasks/{task_id}")
        return r.status_code == 204

    # ------------------------------------------------------------ 只读状态
    def auth_status(self) -> dict:
        return self._get("/api/v1/auth/status").json()

    def system_status(self) -> dict:
        return self._get("/api/v1/system/status").json()
