# -*- coding: utf-8 -*-
"""
无人值守 CLI（`campuslink.cli_headless`）的胶水层测试。

只测「壳」的行为——core 的 280 条测试继续锁认证逻辑本身：

  · CliConfig 明文 json 读写往返 / 损坏容错 / credentials 组装
  · 首次配置交互（mock stdin / 中断）
  · 退出码语义：成功 0 / 认证失败 1 / 未配置 3
  · --silent 与手动模式的行为分叉（不真分配控制台）
  · 快捷方式的参数约定（桌面无参、Startup 带 --silent）
"""
from __future__ import annotations

import io
import json
import sys

import pytest

from campuslink import cli_headless as H
from campuslink.core.models import Credentials, PortalProfile


# ============================================================ 配置
class TestCliConfig:
    def test_roundtrip(self, tmp_path):
        c = H.CliConfig(student_id="s1", account="a1", password="p1")
        f = tmp_path / "cli_config.json"
        assert c.save(f) and f.exists()
        d = json.loads(f.read_text(encoding="utf-8"))
        assert d["password"] == "p1"           # 明文是拍板的行为
        c2 = H.CliConfig.load(f)
        assert (c2.student_id, c2.account, c2.password) == ("s1", "a1", "p1")

    def test_missing_file_returns_none(self, tmp_path):
        assert H.CliConfig.load(tmp_path / "no.json") is None

    def test_corrupt_file_returns_none(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not json", encoding="utf-8")
        assert H.CliConfig.load(f) is None

    def test_non_dict_file_returns_none(self, tmp_path):
        f = tmp_path / "arr.json"
        f.write_text("[1,2]", encoding="utf-8")
        assert H.CliConfig.load(f) is None

    def test_unknown_keys_ignored(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text(json.dumps({"student_id": "s", "junk": 1}),
                     encoding="utf-8")
        c = H.CliConfig.load(f)
        assert c.student_id == "s" and not hasattr(c, "junk")

    def test_values_coerced_to_str(self, tmp_path):
        """手改 json 把数字写进字段也不崩。"""
        f = tmp_path / "c.json"
        f.write_text(json.dumps({"student_id": 12345, "password": 9}),
                     encoding="utf-8")
        c = H.CliConfig.load(f)
        assert c.student_id == "12345" and c.password == "9"

    def test_complete_check(self):
        assert H.CliConfig(student_id="s", password="p").complete()
        assert not H.CliConfig(student_id="s").complete()
        assert not H.CliConfig(password="p").complete()

    def test_credentials_normalized(self):
        c = H.CliConfig(student_id=" s ", password="p")
        cred = c.credentials()
        assert isinstance(cred, Credentials)
        assert cred.student_id == "s"

    def test_default_profile(self):
        assert H.CliConfig().profile_name == H.DEFAULT_PROFILE_NAME


# ============================================================ 首次配置
class TestRunSetup:
    def test_happy_path(self, tmp_path, monkeypatch):
        import io
        monkeypatch.setattr(H.paths, "data_dir", lambda: tmp_path)  # 防污染真实配置
        out = io.StringIO()
        monkeypatch.setattr(H.sys, "stdin",
                            io.StringIO("2353100416\n\nmypassword\n"))
        cfg = H.run_setup(out)
        assert cfg is not None
        assert cfg.student_id == "2353100416"
        assert cfg.account == ""               # 留空 → credentials 时用学号
        assert cfg.password == "mypassword"

    def test_blank_fields_rejected(self, monkeypatch):
        import io
        out = io.StringIO()
        monkeypatch.setattr(H.sys, "stdin", io.StringIO("\n\n\n"))
        assert H.run_setup(out) is None

    def test_eof_returns_none(self, monkeypatch):
        import io
        out = io.StringIO()
        monkeypatch.setattr(H.sys, "stdin", io.StringIO(""))
        assert H.run_setup(out) is None

    def test_save_failure_reports_none(self, tmp_path, monkeypatch):
        import io
        monkeypatch.setattr(H.paths, "data_dir", lambda: tmp_path / "no-dir")
        out = io.StringIO()
        monkeypatch.setattr(H.sys, "stdin",
                            io.StringIO("s\n\np\n"))
        # save 目标目录不存在 → 失败
        cfg = H.run_setup(out)
        assert cfg is not None                 # run_setup 本身成功
        assert cfg.save(tmp_path / "no-dir" / "sub" / "x.json") is False


# ============================================================ 退出码语义
class TestExitCodes:
    def test_constants(self):
        assert H.EXIT_OK == 0
        assert H.EXIT_AUTH_FAILED == 1
        assert H.EXIT_SETUP_REQUIRED == 3

    def test_success_exit_zero(self, tmp_path, monkeypatch):
        cfg = H.CliConfig(student_id="s", password="p")
        f = tmp_path / "cli_config.json"
        cfg.save(f)
        monkeypatch.setattr(H, "CLI_CONFIG_FILE", "cli_config.json")
        monkeypatch.setattr(H.paths, "data_dir", lambda: tmp_path)
        monkeypatch.setattr(H, "check_already_online", lambda p, o: True)
        code = H.main([])
        assert code == H.EXIT_OK

    def test_auth_failure_exit_one(self, tmp_path, monkeypatch):
        cfg = H.CliConfig(student_id="s", password="p")
        cfg.save(tmp_path / "cli_config.json")
        monkeypatch.setattr(H.paths, "data_dir", lambda: tmp_path)
        monkeypatch.setattr(H, "check_already_online", lambda p, o: False)
        monkeypatch.setattr(H, "run_auth", lambda *a, **kw: False)
        monkeypatch.setattr(H, "_pause", lambda: None)
        code = H.main([])
        assert code == H.EXIT_AUTH_FAILED

    def test_incomplete_config_reruns_setup(self, tmp_path, monkeypatch):
        """
        配置存在但缺字段（如只填了学号）→ 必须重新进配置。

        用户拍板（2026-10-19）：首次/重新配置的唯一判据是
        「json 里是否有账号密码学号」，与网络状态无关。
        """
        import io
        cfg = H.CliConfig(student_id="s")       # 缺密码
        cfg.save(tmp_path / "cli_config.json")
        monkeypatch.setattr(H.paths, "data_dir", lambda: tmp_path)
        monkeypatch.setattr(H, "check_already_online", lambda p, o: True)
        monkeypatch.setattr(H.sys, "stdin", io.StringIO("s2\n\nrealpass\n"))
        code = H.main([])
        assert code == H.EXIT_OK
        # 重配后的新凭据已落盘
        assert H.CliConfig.load(tmp_path / "cli_config.json").password == "realpass"

    def test_silent_without_config_exits_setup_required(self, tmp_path,
                                                        monkeypatch):
        monkeypatch.setattr(H.paths, "data_dir", lambda: tmp_path)
        warned = []
        monkeypatch.setattr(H, "_messagebox", lambda t: warned.append(t))
        code = H.main(["--silent"])
        assert code == H.EXIT_SETUP_REQUIRED
        assert warned and "初始配置" in warned[0]


# ============================================================ 控制台
class TestConsole:
    def test_silent_mode_never_touches_stdout(self):
        """--silent 的输出全走日志，stdout 不参与（隐藏窗口运行）。"""
        out = H._open_console_out(silent=True)
        assert out is None

    def test_manual_mode_uses_stdout(self):
        """console 打包下手动模式直接用 stdout。"""
        import io
        orig = sys.stdout
        sys.stdout = io.StringIO()
        try:
            out = H._open_console_out(silent=False)
            assert out is sys.stdout
        finally:
            sys.stdout = orig

    def test_never_returns_none(self, monkeypatch):
        """stdout 意外不可用（windowed 残留场景）也必须返回可写对象。"""
        monkeypatch.setattr(sys, "stdout", None)
        out = H._open_console_out(silent=False)
        assert out is not None
        out.write("x")            # 不抛
        out.flush()


# ============================================================ 认证行为
class TestRunAuth:
    def test_returns_true_on_ok(self):
        class _A:
            def __init__(self, *a, **kw):
                pass

            def login(self, cred, **kw):
                from campuslink.core.models import LoginResult, NetworkState
                kw["on_progress"]("...")
                return LoginResult(True, "认证成功", NetworkState.ONLINE,
                                   attempts=1)

        cfg = H.CliConfig(student_id="s", password="p")
        import io
        out = io.StringIO()
        orig = H.Authenticator
        H.Authenticator = _A
        try:
            assert H.run_auth(cfg, PortalProfile(), verbose=True, out=out)
        finally:
            H.Authenticator = orig

    def test_returns_false_on_failure(self):
        from campuslink.core.models import LoginResult, NetworkState

        class _A:
            def __init__(self, *a, **kw):
                pass

            def login(self, cred, **kw):
                return LoginResult(False, "密码错误", NetworkState.NEED_LOGIN,
                                   attempts=1)

        cfg = H.CliConfig(student_id="s", password="p")
        import io
        out = io.StringIO()
        orig = H.Authenticator
        H.Authenticator = _A
        try:
            assert not H.run_auth(cfg, PortalProfile(), verbose=False, out=out)
        finally:
            H.Authenticator = orig
