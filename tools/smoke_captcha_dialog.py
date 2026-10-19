# -*- coding: utf-8 -*-
"""
验证码人工输入对话框的冒烟测试（离屏，不弹真窗口）。

必须独立进程跑：Qt 一个进程里 QApplication 与 QCoreApplication 不能共存，
pytest 主进程里已有 QCoreApplication，所以这里用 subprocess 隔离。

用法：
    python tools/smoke_captcha_dialog.py
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    img = Image.new("RGB", (200, 60), (255, 255, 255))
    for x in range(60, 80):                      # 画个粗竖线当"1"
        for y in range(10, 50):
            img.putpixel((x, y), (30, 30, 30))
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    from PySide6.QtWidgets import QApplication, QDialog, QLabel

    from campuslink.gui.captcha_dialog import CaptchaDialog

    app = QApplication.instance() or QApplication(sys.argv)
    img = _png_bytes()
    failures: list[str] = []

    # --- 1) 构造 + 图片加载 ---
    dlg = CaptchaDialog(img, auto_text="99", math_mode=True,
                        timeout_s=15, parent=None)
    if dlg.pic.pixmap() is None or dlg.pic.pixmap().isNull():
        failures.append("验证码图片没有渲染出来")
    print(f"[1] 构造 ok，图 {dlg.pic.pixmap().width()}x{dlg.pic.pixmap().height()}")

    # --- 2) 正常提交 ---
    dlg.ed_answer.setText("16")
    dlg._submit()
    if dlg.result() != QDialog.Accepted:
        failures.append("提交后对话框没有 accept")
    if dlg._answer != "16":
        failures.append(f"答案不对: {dlg._answer!r}")
    print(f"[2] 提交 ok，answer={dlg._answer!r}")

    # --- 3) 空输入按跳过 ---
    dlg2 = CaptchaDialog(img, "", math_mode=False, timeout_s=15)
    dlg2.ed_answer.setText("   ")
    dlg2._submit()
    if dlg2._answer is not None:
        failures.append("空输入应视为跳过（answer=None）")
    if dlg2.result() != QDialog.Rejected:
        failures.append("跳过应 reject")
    print("[3] 空输入=跳过 ok")

    # --- 4) 提交按钮文本与提示 ---
    dlg3 = CaptchaDialog(img, "", math_mode=True, timeout_s=15)
    texts = [w.text() for w in dlg3.findChildren(QLabel)]
    if not any("计算结果" in t for t in texts):
        failures.append(f"math 模式应有「计算结果」提示，实际标签: {texts}")
    if dlg3.btn_ok.text() != "提交" or dlg3.btn_skip.text() != "换一张":
        failures.append("按钮文案不对")
    print(f"[4] 提示文案 ok: {[t for t in texts if t][:2]}")

    # --- 5) 坏图片不崩 ---
    dlg4 = CaptchaDialog(b"not-a-png", "", math_mode=False, timeout_s=15)
    if "加载失败" not in dlg4.pic.text():
        failures.append("坏图片应显示占位提示")
    print("[5] 坏图片兜底 ok")

    if failures:
        print("\nFAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
