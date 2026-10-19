# -*- coding: utf-8 -*-
"""
算式验证码的解析与安全计算。

本项目的门户（桂林信息科技学院，`10.6.6.6`）在 2026-10 之后把验证码
从「4 位字符」换成了「算术表达式」，表单标签写明 **输入计算结果**：

    6乘12=?      7加9=?      28÷4=?      57-0=?

也就是说 OCR 只认字已经不够了 —— 必须把表达式认出来、**算出来**，
再把结果提交上去。本模块只负责后半段（解析 + 计算），
完全不碰图像，因此可以脱离 cv2/numpy 单独测试。

为什么不 `eval()`：
    识别结果是外部输入（来自 OCR，本质不可信）。直接 eval 等于把
    任意代码执行权交给一张验证码图片。这里只认「两个整数 + 一个运算符」，
    其余一律判为非法。

运算符既有汉字也有符号，且同一含义可能有多种写法（`乘`/`×`/`x`，
`除`/`÷`），所以先做归一化再看。
"""
from __future__ import annotations

import re

__all__ = [
    "MAX_OPERAND",
    "normalize_op",
    "parse_expression",
    "evaluate",
    "solve",
]

#: 操作数上限。超过这个位数基本可以判定是识别幻觉（把干扰字连成一片），
#: 真实验证码最大只见过两位数。
MAX_OPERAND = 999

#: 运算符别名 → 规范符号。
#: 汉字优先，因为该门户的 `加`/`乘` 是画在验证码里的原始字形；
#: 符号形式（`×`/`÷`）则来自 ddddocr 或后续改版。
OP_ALIASES: dict[str, str] = {
    "加": "+",
    "+": "+",
    "＋": "+",
    "减": "-",
    "-": "-",
    "－": "-",
    "−": "-",
    "—": "-",          # 破折号，OCR 常把减号认成它
    "乘": "*",
    "*": "*",
    "＊": "*",
    "×": "*",
    "x": "*",
    "X": "*",
    "除": "/",
    "/": "/",
    "／": "/",
    "÷": "/",
}

#: 反查（detail 里回显用）
_SYMBOL_TO_NAME = {"+": "加", "-": "减", "*": "乘", "/": "除"}

_DIGITS = re.compile(r"\d+")


def normalize_op(ch: str) -> str | None:
    """把单个字符归一成 `+ - * /`；不是运算符则返回 None。"""
    if not ch:
        return None
    return OP_ALIASES.get(ch)


def _first_op_index(text: str) -> tuple[int, str] | None:
    """
    在文本里找第一个运算符。

    返回 (下标, 规范符号)。找不到返回 None。

    注意 `x`/`X` 只在中间位置才算乘号 —— 出现在首尾更像是干扰字母，
    不过真实场景下算式里出现字母的概率极低，这里不做过度防御。
    """
    for i, ch in enumerate(text):
        sym = OP_ALIASES.get(ch)
        if sym is not None:
            return i, sym
    return None


def parse_expression(text: str, *,
                     max_b_digits: int | None = None) -> tuple[int, str, int] | None:
    """
    从任意文本里抠出 `(a, 运算符, b)`。

    输入通常是 ddddocr 的原始输出，噪声很多，例如：

        '6乘12=?'   → (6, '*', 12)
        '73加42'    → (73, '+', 42)
        '28÷4=?'    → (28, '/', 4)
        '57-0=?'    → (57, '-', 0)
        '64123'     → None   （没有运算符）
        '6和云弈'   → None   （运算符认错了）

    只取运算符**左边最后一段**数字和**右边最前一段**数字 ——
    因为干扰字会粘在两端，而紧挨运算符的那两个数才是真操作数。

    `max_b_digits`：限制第二个操作数的位数，用来挡尾部 `=?` 被 OCR 认成数字
    （`8加4=?` → `'8加42'`）。

    ⚠ **这个参数只对"加/乘/除"安全**：实测 3187 条标注，加/乘/除的第二操作数
    100% 是 1 位；但**减法的第二操作数有 64.4% 是 2 位**（`55减23`）。
    所以在减式上用 `max_b_digits=1` 会把 `55减23` 截成 `55减2`，答案从 32 变 53 ——
    是错答而不是换图。详见 `docs/design/08-验证码改版应对.md` §5.1。

    字符型验证码不要传这个参数。
    """
    if not text:
        return None
    # 去掉空白与常见噪声符号，但保留运算符本身
    cleaned = "".join(ch for ch in text if not ch.isspace())

    found = _first_op_index(cleaned)
    if found is None:
        return None
    idx, sym = found

    left = cleaned[:idx]
    right = cleaned[idx + 1:]

    left_digits = _DIGITS.findall(left)
    right_digits = _DIGITS.findall(right)
    if not left_digits or not right_digits:
        return None

    # 紧邻运算符的那一段才是操作数
    a_txt = left_digits[-1]
    b_txt = right_digits[0]

    if max_b_digits and len(b_txt) > max_b_digits:
        b_txt = b_txt[:max_b_digits]

    if len(a_txt) > 3 or len(b_txt) > 3:
        return None            # 位数过多，判为幻觉
    a, b = int(a_txt), int(b_txt)

    if a > MAX_OPERAND or b > MAX_OPERAND:
        return None
    return a, sym, b


def evaluate(a: int, op: str, b: int) -> int | None:
    """
    计算结果。返回 None 表示这条表达式不该被采用。

    规则：
      · 加/减/乘一律可算（减法允许负数 —— 实测 6.5% 的减法题结果为负，如 `1减8`）
      · 除法**必须整除**，除不尽返回 None。

    关于除法这条要说清楚：早期 30 张样本里除法全是整数解，所以当时的结论是
    "除不尽说明运算符认错了（八成是减号被认成除号）"。但 3187 条标注实测有
    **6.7% 的除法题本来就除不尽**（如 `2除7`）。

    返回 None 的后果是"换一张图"，不是"提交错答案"，所以这个保守策略仍然成立：
    在弄清门户对不整除题的期望之前，宁可换图。若哪天确认了门户的校验方式，
    改这里即可（见 `docs/design/08-验证码改版应对.md` §6）。
      · 除零非法。
    """
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        if b == 0:
            return None
        if a % b != 0:
            return None
        return a // b
    return None


def solve(text: str, *, max_b_digits: int | None = None,
          detail: dict[str, str] | None = None) -> str | None:
    """
    一步到位：文本 → 答案字符串（无法可靠求解时返回 None）。

    `max_b_digits` 见 `parse_expression`。
    `detail` 传入字典时会写入 `_expr` / `_answer` 两个键，
    供日志与 GUI 回显（不含敏感信息）。
    """
    parsed = parse_expression(text, max_b_digits=max_b_digits)
    if parsed is None:
        return None
    a, op, b = parsed
    ans = evaluate(a, op, b)
    if ans is None:
        return None
    if detail is not None:
        name = _SYMBOL_TO_NAME.get(op, op)
        detail["_expr"] = f"{a}{name}{b}"
        detail["_answer"] = str(ans)
    return str(ans)
