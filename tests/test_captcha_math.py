# -*- coding: utf-8 -*-
"""
算式验证码「解析 + 计算」的纯逻辑测试。

这一层不碰图像，所以可以逐条断言得很难 —— 也正因为如此，
它是整条算式链路里唯一能被完整验证的部分：识别器可能认错字，
但只要文本进来，算得对不对是完全确定的。
"""
from __future__ import annotations

import pytest

from campuslink.core.captcha_math import (MAX_OPERAND, evaluate, normalize_op,
                                         parse_expression, solve)


# ============================================================ 运算符归一化
class TestNormalizeOp:
    @pytest.mark.parametrize("ch,expected", [
        ("加", "+"), ("+", "+"), ("＋", "+"),
        ("减", "-"), ("-", "-"), ("－", "-"), ("—", "-"),
        ("乘", "*"), ("*", "*"), ("×", "*"), ("x", "*"), ("X", "*"),
        ("除", "/"), ("/", "/"), ("÷", "/"),
    ])
    def test_known(self, ch, expected):
        assert normalize_op(ch) == expected

    @pytest.mark.parametrize("ch", ["", "1", "a", "？", "="])
    def test_unknown(self, ch):
        assert normalize_op(ch) is None


# ============================================================ 表达式解析
class TestParseExpression:
    @pytest.mark.parametrize("text,expected", [
        # 该门户的真实形态（汉字运算符 + 尾部 =?）
        ("6乘12=?", (6, "*", 12)),
        ("7加9=?", (7, "+", 9)),
        ("28÷4=?", (28, "/", 4)),
        ("57-0=?", (57, "-", 0)),
        # ddddocr 的原始输出：噪声字符混在两端
        ("73加42", (73, "+", 42)),
        ("21加7-2", (21, "+", 7)),
        ("6乘79", (6, "*", 79)),
        # 符号形式
        ("2x3=?", (2, "*", 3)),
        ("10-4", (10, "-", 4)),
    ])
    def test_parses(self, text, expected):
        assert parse_expression(text) == expected

    @pytest.mark.parametrize("text", [
        "",                       # 空
        "64123",                  # 没有运算符
        "988",                    # 纯数字
        "6和云弈",                # 运算符被认错
        "加3",                    # 左边没有操作数
        "5加",                    # 右边没有操作数
        "加减乘除",               # 只有运算符
        "1234567加8",             # 位数过多（幻觉）
    ])
    def test_rejects(self, text):
        assert parse_expression(text) is None

    def test_operand_cap(self):
        """超过 MAX_OPERAND 位数的判为幻觉。"""
        too_long = "9" * (len(str(MAX_OPERAND)) + 1)
        assert parse_expression(f"{too_long}加1") is None

    def test_whitespace_ignored(self):
        assert parse_expression(" 6 乘 12 = ? ") == (6, "*", 12)

    def test_takes_digits_adjacent_to_operator(self):
        """
        干扰字会粘在两端，只有紧邻运算符的数字才是真操作数。

        '73加42' 里左边是 73，取最后一段；右边取最前一段。
        """
        assert parse_expression("73加42") == (73, "+", 42)


# ============================================================ B 位数截断
class TestMaxBDigits:
    """
    算式验证码的第二个操作数恒为 1 位，而尾部 `=?` 极易被认成数字
    （`8加4=?` → '8加42'）。识别链路必须能把 B 截到 1 位。
    """

    def test_truncates(self):
        assert parse_expression("8加42", max_b_digits=1) == (8, "+", 4)

    def test_truncates_real_case(self):
        assert parse_expression("21加72", max_b_digits=1) == (21, "+", 7)

    def test_no_truncate_by_default(self):
        assert parse_expression("8加42") == (8, "+", 42)

    def test_shorter_than_limit_untouched(self):
        assert parse_expression("8加4", max_b_digits=1) == (8, "+", 4)


# ============================================================ 计算
class TestEvaluate:
    @pytest.mark.parametrize("a,op,b,expected", [
        (6, "*", 12, 72),
        (7, "+", 9, 16),
        (28, "/", 4, 7),
        (57, "-", 0, 57),
        (3, "-", 9, -6),          # 允许负数答案
        (0, "*", 0, 0),
        (0, "+", 0, 0),
    ])
    def test_normal(self, a, op, b, expected):
        assert evaluate(a, op, b) == expected

    def test_division_by_zero(self):
        assert evaluate(7, "/", 0) is None

    def test_inexact_division_rejected(self):
        """
        除不尽说明运算符认错了（八成是减号被认成除号）。

        该门户的除法样本全是整数解，所以宁可换一张图，
        也不要提交一个必然错误的小数答案。
        """
        assert evaluate(7, "/", 2) is None
        assert evaluate(6, "/", 8) is None

    def test_unknown_operator(self):
        assert evaluate(1, "%", 2) is None
        assert evaluate(1, "", 2) is None


# ============================================================ 一步求解
class TestSolve:
    @pytest.mark.parametrize("text,expected", [
        ("8加4=?", "12"),
        ("6乘12=?", "72"),
        ("28÷4=?", "7"),
        ("57-0=?", "57"),
        ("7加9", "16"),
    ])
    def test_solves(self, text, expected):
        assert solve(text) == expected

    def test_returns_none_on_failure(self):
        assert solve("64123") is None
        assert solve("") is None

    def test_detail_filled(self):
        detail: dict[str, str] = {}
        assert solve("8加4", detail=detail) == "12"
        assert detail["_expr"] == "8加4"
        assert detail["_answer"] == "12"

    def test_detail_untouched_on_failure(self):
        detail: dict[str, str] = {}
        assert solve("64123", detail=detail) is None
        assert detail == {}

    def test_max_b_digits_passthrough(self):
        assert solve("8加42", max_b_digits=1) == "12"


# ============================================================ 安全性
class TestSafety:
    """
    识别结果是外部输入（来自 OCR，本质不可信）。
    绝不能让它变成代码执行入口。
    """

    @pytest.mark.parametrize("evil", [
        "__import__('os').system('echo pwned')",
        "1+1; import os",
        "eval('2+2')",
        "9**999999",
        "1/0",
        "0x10+1",
        "1e999*2",
    ])
    def test_no_execution(self, evil):
        """要么算出个位数结果，要么直接拒绝，绝不执行任意代码。"""
        got = solve(evil)
        assert got is None or (got.lstrip("-").isdigit() and abs(int(got)) <= 10 ** 6)

    def test_only_two_operands(self):
        """'1+2+3' 只按第一个运算符算，不会链式求值。"""
        assert parse_expression("1+2+3") == (1, "+", 2)
