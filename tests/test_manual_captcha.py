# -*- coding: utf-8 -*-
"""
验证码人工输入兜底的接口与语义测试。

不碰任何真实 UI（Qt 不在 CI 里实例化对话框），锁的是三件事：

  · core 的 `ask_captcha` 钩子语义：非空覆盖 / 空值换图 / 异常兜底 / Cancelled 中止
  · 默认路径**完全不变**：不传钩子时，行为与改动前逐字节一致
  · 配置字段 `captcha_manual` 的持久化往返

线程桥（AuthWorker 的 Event 往返）在 TestWorkerBridge 里用真实 QThread 测，
不弹任何界面 —— 对话框本身交给冒烟测试（tools/smoke_gui.py）人工看。
"""
from __future__ import annotations

import threading

import pytest

from campuslink.config import AppConfig
from campuslink.core import authenticator as auth_mod
from campuslink.core.authenticator import Authenticator, Cancelled
from campuslink.core.models import Credentials, LoginResult, NetworkState, PortalProfile
from campuslink.core.ocr_backend import OcrResult


# ============================================================ 测试替身
class _FakePortal:
    """替身 PortalClient：不联网，验证码固定，提交结果可编排。"""

    def __init__(self, submit_results: list[tuple[bool, str, str]]):
        self._results = list(submit_results)
        self.submitted: list[tuple[str, str]] = []   # (captcha_text, ...) 记录提交了什么

    def open_login_page(self):
        return object()          # 页面就是个不透明对象，替身不解析它

    def fetch_captcha(self, page) -> bytes:
        return b"fake-png-bytes"

    def build_payload(self, page, cred, captcha_text: str) -> dict:
        self.submitted.append((captcha_text,))
        return {"captcha": captcha_text}

    def submit(self, page, payload) -> object:
        return ("resp",)

    def evaluate(self, resp) -> tuple[bool, str, str]:
        if self._results:
            return self._results.pop(0)
        return True, "ok", "ok"


class _FakeBackend:
    """替身 OCR：返回固定识别结果（可编排）。

    结果项可以是 `"text"`（conf 按二值 1.0/0.0，模拟启发式引擎）
    或 `("text", conf)`（真实连续置信度，模拟 CRNN 模型引擎）。
    """

    def __init__(self, results: list):
        self._results = list(results)
        self.calls = 0

    def recognize(self, image, *, length, strategies):
        self.calls += 1
        item = self._results.pop(0) if self._results else ""
        if isinstance(item, tuple):
            text, conf = item
        else:
            text, conf = item, (1.0 if item else 0.0)
        return OcrResult(text=text, confidence=conf,
                         detail={}, source="fake")


def _patch_client(monkeypatch, portal: _FakePortal) -> None:
    monkeypatch.setattr(auth_mod, "PortalClient", lambda profile, *a, **kw: portal)


def _profile(**kw) -> PortalProfile:
    d = dict(portal_url="http://fake.portal", max_retry=3,
             check_online_first=False)
    d.update(kw)
    return PortalProfile.from_dict({**PortalProfile().to_dict(), **d})


def _cred() -> Credentials:
    return Credentials(student_id="s", account="a", password="p")


def _run_login(auth: Authenticator, monkeypatch, portal: _FakePortal,
               **kwargs) -> LoginResult:
    _patch_client(monkeypatch, portal)
    return auth.login(_cred(), **kwargs)


# ============================================================ 默认路径不变
class TestDefaultPathUntouched:
    def test_no_ask_hook_behaves_as_before(self, monkeypatch):
        """不传 ask_captcha：识别成功就提交，行为与改动前完全一致。"""
        portal = _FakePortal([(True, "ok", "ok")])
        auth = Authenticator(_profile())
        auth._ocr_backend = _FakeBackend(["1234"])
        res = _run_login(auth, monkeypatch, portal)
        assert res.ok
        assert portal.submitted == [("1234",)]

    def test_recognize_empty_still_retries_without_hook(self, monkeypatch):
        """不传钩子 + 识别为空 → 换图重试（原有逻辑）。"""
        portal = _FakePortal([(True, "ok", "ok")])
        auth = Authenticator(_profile())
        auth._ocr_backend = _FakeBackend(["", "4567"])
        res = _run_login(auth, monkeypatch, portal)
        assert res.ok
        assert portal.submitted == [("4567",)]


# ============================================================ 钩子语义
class TestAskCaptchaHook:
    """
    2.0 语义：ask_captcha 只在「这张图没认出来 / 被置信度门限拦下」时调用。
    识别有把握时不去打扰用户 —— 人工输入从「总是人工」升级为「低置信兜底」。
    """

    def test_low_confidence_answer_overrides_gate(self, monkeypatch):
        """模型没把握（conf 0.7 < 0.9）→ 弹人工 → 用户答案覆盖提交。"""
        portal = _FakePortal([(True, "ok", "ok")])
        auth = Authenticator(_profile())
        auth._ocr_backend = _FakeBackend([("99", 0.7)])   # 自动结果没把握
        res = _run_login(auth, monkeypatch, portal,
                         ask_captcha=lambda img, auto: "16")
        assert res.ok
        assert portal.submitted == [("16",)]

    def test_high_confidence_never_bothers_user(self, monkeypatch):
        """识别有把握（conf 0.95 ≥ 0.9）→ 直接提交，ask 一次都不调。"""
        asked = []

        def _ask(img, auto):
            asked.append(auto)
            return None

        portal = _FakePortal([(True, "ok", "ok")])
        auth = Authenticator(_profile())
        auth._ocr_backend = _FakeBackend([("1234", 0.95)])
        res = _run_login(auth, monkeypatch, portal, ask_captcha=_ask)
        assert res.ok
        assert asked == []
        assert portal.submitted == [("1234",)]

    def test_hook_none_with_empty_auto_retries(self, monkeypatch):
        """自动没认出来 + 用户跳过 → 换图，不提交空答案。"""
        portal = _FakePortal([(True, "ok", "ok")])
        auth = Authenticator(_profile())
        auth._ocr_backend = _FakeBackend(["", "88"])
        res = _run_login(auth, monkeypatch, portal,
                         ask_captcha=lambda img, auto: None)
        assert res.ok
        assert portal.submitted == [("88",)]

    def test_hook_none_with_gated_result_retries(self, monkeypatch):
        """低置信被门限拦下 + 用户跳过 → 换图。"""
        portal = _FakePortal([(True, "ok", "ok")])
        auth = Authenticator(_profile())
        auth._ocr_backend = _FakeBackend([("99", 0.7), ("1234", 0.95)])
        res = _run_login(auth, monkeypatch, portal,
                         ask_captcha=lambda img, auto: None)
        assert res.ok
        assert portal.submitted == [("1234",)]

    def test_hook_whitespace_answer_counts_as_skip(self, monkeypatch):
        portal = _FakePortal([(True, "ok", "ok")])
        auth = Authenticator(_profile())
        auth._ocr_backend = _FakeBackend(["", "7"])
        _run_login(auth, monkeypatch, portal,
                   ask_captcha=lambda img, auto: "   ")
        assert portal.submitted == [("7",)]

    def test_hook_exception_is_swallowed(self, monkeypatch):
        """钩子抛普通异常不能拖垮登录，按「跳过」处理。"""
        portal = _FakePortal([(True, "ok", "ok")])
        auth = Authenticator(_profile())
        auth._ocr_backend = _FakeBackend(["", "5"])

        def _boom(img, auto):
            raise RuntimeError("UI炸了")

        res = _run_login(auth, monkeypatch, portal, ask_captcha=_boom)
        assert res.ok
        assert portal.submitted == [("5",)]

    def test_hook_cancelled_aborts_login(self, monkeypatch):
        """低置信弹框时用户取消（钩子抛 Cancelled）→ 整个登录中止。"""
        portal = _FakePortal([(True, "ok", "ok")])

        def _cancel(img, auto):
            raise Cancelled("用户取消")

        auth = Authenticator(_profile())
        auth._ocr_backend = _FakeBackend([("99", 0.7)])
        res = _run_login(auth, monkeypatch, portal, ask_captcha=_cancel)
        assert res.ok is False
        assert "取消" in res.message
        assert portal.submitted == []          # 一次都没提交

    def test_cancel_flag_checked_before_asking(self, monkeypatch):
        """用户已点取消时，不应再弹框问验证码。"""
        asked = []
        portal = _FakePortal([(True, "ok", "ok")])
        auth = Authenticator(_profile())
        auth._ocr_backend = _FakeBackend([""])
        res = auth.login(
            _cred(),
            cancel=lambda: True,
            ask_captcha=lambda img, auto: asked.append(auto),
        )
        _patch_client(monkeypatch, portal)
        # cancel 恒 True，第一次检查点就退出，登录页都没打开
        assert res.ok is False
        assert asked == []


# ============================================================ 置信度门限
class TestConfidenceGate:
    """`Authenticator._gated`：只有「真实的中间值置信度」才会被拦。"""

    def test_binary_confidence_never_gated(self):
        """启发式引擎给 1.0/0.0 二值 —— 门限对它必须无影响（1.x 兼容）。"""
        p = _profile()
        assert not Authenticator._gated(1.0, p)
        assert not Authenticator._gated(0.0, p)

    def test_low_confidence_gated(self):
        p = _profile(captcha_min_confidence=0.90)
        assert Authenticator._gated(0.7, p)
        assert Authenticator._gated(0.899, p)

    def test_high_confidence_passes(self):
        p = _profile(captcha_min_confidence=0.90)
        assert not Authenticator._gated(0.90, p)      # 等于门限 = 有把握
        assert not Authenticator._gated(0.95, p)

    def test_gate_disabled_at_zero(self):
        """门限设 0 → 全部放行（用户想关掉这个特性）。"""
        p = _profile(captcha_min_confidence=0.0)
        assert not Authenticator._gated(0.5, p)

    def test_gate_disabled_at_one(self):
        """门限设 1.0 → 只有 conf=1.0 的（可能是二值）能过，其余全拦。"""
        p = _profile(captcha_min_confidence=1.0)
        assert not Authenticator._gated(1.0, p)       # 1.0 不在开区间，仍不拦
        assert Authenticator._gated(0.99, p)

    def test_bad_threshold_disables_gate(self):
        """手改档案把门限写坏 → 不拦（宁可多提交也不因配置错误卡死）。"""
        p = _profile()
        object.__setattr__(p, "captcha_min_confidence", "abc")
        assert not Authenticator._gated(0.5, p)

    def test_integration_low_confidence_exhausts_retries(self, monkeypatch):
        """连续低置信 → 换图重试到 max_retry 用尽，一次都不提交。"""
        portal = _FakePortal([(True, "ok", "ok")])
        auth = Authenticator(_profile(max_retry=3))
        auth._ocr_backend = _FakeBackend([("99", 0.7)] * 5)
        res = _run_login(auth, monkeypatch, portal)
        assert res.ok is False
        assert portal.submitted == []

    def test_integration_gate_roundtrip(self):
        from campuslink.core.models import PortalProfile as PP
        p = PP(captcha_min_confidence=0.8)
        assert PP.from_dict(p.to_dict()).captcha_min_confidence == 0.8

    def test_profile_field_default(self):
        from campuslink.core.models import PortalProfile as PP
        assert PP().captcha_min_confidence == 0.90


# ============================================================ 配置字段
class TestConfig:
    def test_default_off(self):
        assert AppConfig().captcha_manual is False

    def test_roundtrip(self, tmp_path):
        c = AppConfig(captcha_manual=True)
        p = tmp_path / "cfg.json"
        c.save(p)
        assert AppConfig.load(p).captcha_manual is True

    def test_bool_coercion(self):
        c = AppConfig.from_dict({"captcha_manual": "true"})
        assert c.captcha_manual is True
        c = AppConfig.from_dict({"captcha_manual": "0"})
        assert c.captcha_manual is False

    def test_unknown_key_ignored(self):
        c = AppConfig.from_dict({"captcha_manual": True, "nonsense": 1})
        assert c.captcha_manual is True


# ============================================================ 线程桥
@pytest.fixture(scope="module")
def qcoreapp():
    """QThread 没有 QCoreApplication 就不跑 run()。给测试一个最小事件环境。"""
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app


class TestWorkerBridge:
    """
    AuthWorker 的人工问答通道：不弹 UI、不依赖 pytest-qt（本仓不加依赖），
    用真实 QThread + 轮询验证「发信号 → 阻塞 → 收答案」闭环。

    说明：测试里的信号槽用普通 lambda，会在发射线程直接执行；
    真实 GUI 里 `manual_captcha.connect(self._on_manual_captcha)` 的接收者
    是主线程的 MainWindow，Qt 的 AutoConnection 会投递成队列调用 ——
    这与既有 `captcha_seen` 是同一种模式，行为由 Qt 保证，不在这里重复验证。
    """

    def _wait_until(self, pred, timeout: float = 5.0) -> bool:
        """
        轮询等待 + 泵事件循环。

        manual_captcha 信号的接收端住主线程（AuthWorker 对象的所属线程），
        工作线程 emit 走的是**队列投递** —— 必须有人转事件循环才会执行。
        真实 GUI 里是 app.exec()，测试里就得手动 processEvents。
        """
        import time

        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if app is not None:
                app.processEvents()
            if pred():
                return True
            time.sleep(0.02)
        if app is not None:
            app.processEvents()
        return pred()

    def _stub(self, holder, wait_in_thread: bool = False):
        from campuslink.gui.worker import AuthWorker as _W  # noqa: F401

        class _StubAuth:
            def login(self, cred, **kw):
                ask = kw.get("ask_captcha")
                assert ask is not None, "启用人工模式后必须传钩子"
                if wait_in_thread:
                    # 模拟「ask 已经阻塞在等待用户输入时，用户点了取消」。
                    # 线程必须晚于 ask 进入等待 —— 若先 cancel 再 ask，
                    # ask 开头的 evt.clear() 会把取消信号清掉（真实 GUI 也有
                    # 同样的固有竞争，靠对话框轮询 cancel_fn 兜底）。
                    import threading as _th
                    import time as _t

                    def _later():
                        _t.sleep(0.3)
                        wref = holder.get("w")
                        if wref is not None:
                            wref.cancel()

                    _th.Thread(target=_later, daemon=True).start()
                ans = ask(b"img-bytes", "")
                print(f"[stub] ask returned {ans!r}", flush=True)
                return LoginResult(ok=(ans == "16"), message=f"ans={ans}",
                                   state=NetworkState.NEED_LOGIN)
        return _StubAuth()

    def _make_worker(self, stub, wait_s, holder: dict):
        from campuslink.gui.worker import AuthWorker
        w = AuthWorker(stub, _cred())
        w.enable_manual_captcha(wait_s=wait_s)
        w.manual_captcha.connect(
            lambda img, auto: holder.setdefault("asked", (img, auto)))
        results: list = []
        w.result.connect(lambda r: results.append(r))
        return w, results

    def test_answer_roundtrip(self, qcoreapp):
        holder: dict = {}
        stub = self._stub(holder)
        w, results = self._make_worker(stub, wait_s=5, holder=holder)
        holder["w"] = w
        w.start()
        assert self._wait_until(lambda: "asked" in holder), "主线程应收到询问"
        assert holder["asked"] == (b"img-bytes", "")
        w.submit_answer("16")
        assert self._wait_until(lambda: bool(results))
        w.wait(2000)
        assert results[0].ok is True

    def test_timeout_returns_none(self, qcoreapp):
        holder: dict = {}
        stub = self._stub(holder)
        w, results = self._make_worker(stub, wait_s=1, holder=holder)  # 1 秒超时
        holder["w"] = w
        w.start()
        assert self._wait_until(lambda: bool(results), timeout=8)
        w.wait(2000)
        assert results[0].message == "ans=None"

    def test_cancel_releases_waiting_ask(self, qcoreapp):
        """阻塞等待期间用户点取消 → 立刻放行返回 None，不再等满 30s。"""
        import time as _t
        holder: dict = {}
        stub = self._stub(holder, wait_in_thread=True)
        w, results = self._make_worker(stub, wait_s=30, holder=holder)
        holder["w"] = w
        started = _t.monotonic()
        w.start()
        assert self._wait_until(lambda: bool(results), timeout=8)
        w.wait(2000)
        assert results[0].message == "ans=None"
        assert _t.monotonic() - started < 20, "取消应立即放行，而不是等满 wait_s"
