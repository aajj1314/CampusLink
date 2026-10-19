# -*- coding: utf-8 -*-
"""
链路自检 —— 不训练、不读数据也能跑，用来确认代码搬过去是对的。

在训练机上装完依赖后先跑这个：
    python ml/scripts/selftest.py

它检查的是"最容易因为环境不同而出错"的地方：
  · 字符集/编解码是否往返一致（中文编码错位是这类项目最常见的坑）
  · 标注文件读写是否丢字符（BOM、CRLF、编码）
  · 预处理输出形状与值域
  · CTC 贪心解码是否按预期去重、去 blank
  · 算出的答案是否和项目权威实现一致
"""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

_fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}   {extra}")
        _fails.append(name)


def main() -> int:
    print(f"Python {sys.version.split()[0]}")
    print(f"字符集 {C.CHARS}  （{C.NUM_CLASSES} 类含 blank）\n")

    print("[1] 字符集与编解码")
    check("字符集无重复", len(set(C.CHARS)) == len(C.CHARS))
    check("字符集不含 ASCII 字母",
          not any(c.isascii() and c.isalpha() for c in C.CHARS))
    check("索引从 1 开始（0 留给 blank）", min(C.CHAR_TO_IDX.values()) == 1)
    for text in ("7加9", "28除4", "6乘12", "57减0"):
        idx = C.encode(text)
        back = "".join(C.IDX_TO_CHAR[i] for i in idx)
        check(f"往返 {text}", back == text, f"得到 {back}")

    print("\n[2] 写法归一")
    cases = {
        "7+9": "7加9", "7加9": "7加9", "7＋9": "7加9",
        "28/4": "28除4", "28÷4": "28除4", "28除4": "28除4",
        "6*12": "6乘12", "6×12": "6乘12", "6x12": "6乘12", "6乘12": "6乘12",
        "57-0": "57减0", "57－0": "57减0",
        "8加4=?": "8加4",        # 尾部 =? 必须丢掉
        " 7 加 9 ": "7加9",      # 空白丢掉
        "abc7加9qwe": "7加9",    # 字母丢掉，因为不在字符集
        "": "",
    }
    for raw, want in cases.items():
        got = C.normalize_text(raw)
        check(f"{raw!r} → {want!r}", got == want, f"得到 {got!r}")

    print("\n[3] 标注文件读写")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "labels.csv"
        data = {"cap_0001.png": "7加9", "cap_0002.png": "28除4", "cap_0003.png": "6乘12"}
        C.write_labels(p, data)
        back = C.read_labels(p)
        check("往返一致", back == data, f"{back}")
        # 手工编辑成符号形式也要能读
        p.write_text("filename,label\ncap_0001.png,7+9\ncap_0002.png,28/4\n",
                     encoding="utf-8-sig")
        back2 = C.read_labels(p)
        check("符号形式被归一", back2.get("cap_0001.png") == "7加9" and
              back2.get("cap_0002.png") == "28除4", f"{back2}")
        # 无效标签要跳过
        p.write_text("filename,label\ncap_x.png,乱写\ncap_y.png,7加9\n", encoding="utf-8")
        back3 = C.read_labels(p)
        check("无效标签被跳过", list(back3) == ["cap_y.png"], f"{back3}")

    print("\n[4] 预处理")
    img = Image.new("RGB", (200, 60), (255, 255, 255))
    arr = C.preprocess_pil(img)
    check("形状 [1,32,200]", arr.shape == (1, C.IMG_H, C.IMG_W), f"{arr.shape}")
    check("dtype float32", arr.dtype == np.float32, f"{arr.dtype}")
    check("值域 [-1,1]", float(arr.min()) >= -1.0001 and float(arr.max()) <= 1.0001,
          f"[{arr.min():.2f},{arr.max():.2f}]")
    check("白图归一化后接近 +1", float(arr.mean()) > 0.9, f"{arr.mean():.3f}")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    check("字节预处理可用", C.preprocess_bytes(buf.getvalue()) is not None)
    check("坏字节返回 None", C.preprocess_bytes(b"not an image") is None)

    print("\n[5] CTC 贪心解码")
    T, NC = 6, C.NUM_CLASSES
    logits = np.full((T, NC), -9.0, dtype=np.float32)
    # 期望序列：7(8) 加(11) 9(10)，中间插 blank
    seq = [C.CHAR_TO_IDX["7"], C.BLANK_IDX, C.CHAR_TO_IDX["加"],
           C.CHAR_TO_IDX["加"], C.CHAR_TO_IDX["9"], C.BLANK_IDX]
    for t, cls in enumerate(seq):
        logits[t, cls] = 9.0
    out = C.ctc_greedy_decode(logits)[0]
    check("去 blank + 去重复", out == "7加9", f"得到 {out!r}")
    out3 = C.ctc_greedy_decode(logits[None, :, :])[0]
    check("支持 [B,T,C] 输入", out3 == "7加9", f"得到 {out3!r}")

    print("\n[6] 与项目权威实现对齐")
    try:
        from campuslink.core.captcha_math import solve
        # 传统启发式链路：B 截到 1 位（对付 `=?` 被认成数字）
        for label, want in (("7加9", "16"), ("28除4", "7"), ("6乘1", "6"), ("57减0", "57")):
            got = solve(label, max_b_digits=1)
            check(f"{label}（B截1位）→ {want}", got == want, f"得到 {got}")
        # 模型输出链路：不截断，2 位操作数照常算
        check("6乘12 → 72（不截断）", solve("6乘12") == "72", f"得到 {solve('6乘12')}")
        check("6乘12 截断后 → 6（符合预期）",
              solve("6乘12", max_b_digits=1) == "6")
        check("除不尽判为无效", solve("7除2", max_b_digits=1) is None)
    except Exception as exc:                     # pragma: no cover
        check("captcha_math 可导入", False, str(exc))

    print()
    if _fails:
        print(f"失败 {len(_fails)} 项：{_fails}")
        return 1
    print("全部通过。可以开始标注 / 训练了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
