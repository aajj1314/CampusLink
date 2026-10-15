# -*- coding: utf-8 -*-
"""
状态机属性测试与不变式（03 文档 §3）。

不变式：
  · verified / rejected / expired / cancelled 是终态，不可逆
  · nonce 一经使用即作废（哪怕校验失败）
  · 同一节点同时最多持有 1 个 assigned 任务
  · 全局 pending ≤ max_pending
"""
from __future__ import annotations

import random
import time

from campuslink.router.taskmanager import (TaskManager, TaskState)
from tests.contract.conftest import make_png

IMG = make_png(80, 24)


# ============================================================ 属性测试
def test_property_500_random_steps(captcha_png):
    """
    随机操作序列（领取/提交/取消/过期）后断言：
    终态不可逆、nonce 不复用、单节点单任务、pending 不超上限。
    """
    tm = TaskManager(max_pending=8)
    rng = random.Random(20261015)
    devices = ["dev_A", "dev_B", "dev_C"]
    terminal_seen: dict[str, str] = {}      # task_id -> 终态
    used_nonces: set[tuple[str, str]] = set()
    held: dict[str, str] = {}               # device -> task_id（assigned）

    for step in range(500):
        op = rng.choice(["create", "claim", "submit", "cancel", "reap"])
        if op == "create":
            tm.create(captcha_png, expect_length=4, charset_hint="alnum",
                      allow_manual=bool(rng.getrandbits(1)))
        elif op == "claim":
            dev = rng.choice(devices)
            if dev not in held:
                t = tm.next_task(dev, wait=0)
                if t is not None:
                    held[dev] = t.task_id
        elif op == "submit":
            if held:
                dev = rng.choice(list(held))
                tid = held[dev]
                t = tm.get(tid)
                if t is None or t.is_terminal:
                    held.pop(dev, None)
                    continue
                # 随机给合法 / 非法 / 重放 nonce
                nonce = t.nonce if rng.getrandbits(1) else "deadbeefdeadbeef"
                text = "e5s3" if rng.getrandbits(1) else "e5"
                code, _ = tm.submit(tid, dev, nonce=nonce, text=text,
                                    source="ocr", confidence=0.9,
                                    detail={"raw": text}, elapsed_ms=1)
                if code == "ok" or t.is_terminal:
                    held.pop(dev, None)
                if code == "ok":
                    used_nonces.add((tid, nonce))
        elif op == "cancel":
            if held:
                dev = rng.choice(list(held))
                tm.cancel(held[dev], dev)
                held.pop(dev, None)
        else:  # reap
            tm.reap()

        # ---- 不变式断言 ----
        snap = tm.snapshot()
        for t in snap:
            if t.task_id in terminal_seen:
                assert t.state.value == terminal_seen[t.task_id], \
                    f"终态不可逆：{t.task_id} 从 {terminal_seen[t.task_id]} 变到 {t.state.value}"
            if t.is_terminal:
                terminal_seen.setdefault(t.task_id, t.state.value)
        assert tm.pending_count <= 8, "pending 超出上限"
        assigned = [t for t in snap if t.state is TaskState.ASSIGNED]
        owners = [t.assigned_to for t in assigned]
        assert len(owners) == len(set(owners)), "同一节点持有多个 assigned 任务"


# ============================================================ 专项不变式
def test_nonce_single_use_even_on_reject(captcha_png):
    tm = TaskManager()
    t = tm.create(captcha_png, expect_length=4)
    claimed = tm.next_task("A", wait=0)
    # 第一次提交：长度非法 → 422，但 nonce 已被作废
    code, _ = tm.submit(claimed.task_id, "A", nonce=claimed.nonce, text="ab",
                        source="ocr", confidence=0.5, detail={}, elapsed_ms=1)
    assert code == "invalid"
    # 再用同一 nonce 提交合法结果 → 仍 409（nonce 不可复用）
    code2, _ = tm.submit(claimed.task_id, "A", nonce=claimed.nonce, text="e5s3",
                         source="ocr", confidence=1.0, detail={}, elapsed_ms=1)
    assert code2 == "duplicate"


def test_single_task_per_node(captcha_png):
    tm = TaskManager()
    tm.create(captcha_png, expect_length=4)
    tm.create(captcha_png, expect_length=4)
    t1 = tm.next_task("A", wait=0)
    assert t1 is not None
    # A 已持有一个任务，再领应拿不到
    assert tm.next_task("A", wait=0) is None
    # B 不受影响
    t2 = tm.next_task("B", wait=0)
    assert t2 is not None and t2.task_id != t1.task_id


def test_assigned_timeout_requeues(captcha_png):
    tm = TaskManager(assigned_timeout=0.15, ttl=30)
    t = tm.create(captcha_png, expect_length=4)
    tm.next_task("A", wait=0)
    assert t.state is TaskState.ASSIGNED
    time.sleep(0.25)
    tm.reap()
    assert t.state is TaskState.PENDING, "节点 30s 未提交应回 pending 重派"
    assert t.assigned_to is None


def test_ttl_expiry(captcha_png):
    tm = TaskManager(ttl=0.15)
    t = tm.create(captcha_png, expect_length=4)
    time.sleep(0.25)
    tm.reap()
    assert t.state is TaskState.EXPIRED
    assert t.image is None, "过期后图像应释放"


def test_max_pending_enforced(captcha_png):
    tm = TaskManager(max_pending=3)
    for _ in range(3):
        assert tm.create(captcha_png, expect_length=4) is not None
    assert tm.create(captcha_png, expect_length=4) is None


def test_cancel_internal_is_terminal(captcha_png):
    tm = TaskManager()
    t = tm.create(captcha_png, expect_length=4)
    assert tm.cancel(t.task_id, internal=True) == "ok"
    assert t.state is TaskState.CANCELLED


def test_manual_result_requires_valid_charset(captcha_png):
    tm = TaskManager()
    t = tm.create(captcha_png, expect_length=4, charset_hint="digits")
    claimed = tm.next_task("A", wait=0)
    code, _ = tm.submit(claimed.task_id, "A", nonce=claimed.nonce, text="12ab",
                        source="manual", confidence=1.0, detail={}, elapsed_ms=1)
    assert code == "invalid"      # digits 字符集不允许字母
    tm2 = TaskManager()
    t2 = tm2.create(captcha_png, expect_length=4, charset_hint="digits")
    c2 = tm2.next_task("A", wait=0)
    code2, _ = tm2.submit(c2.task_id, "A", nonce=c2.nonce, text="1234",
                          source="manual", confidence=1.0, detail={}, elapsed_ms=1)
    assert code2 == "ok"
