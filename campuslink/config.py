# -*- coding: utf-8 -*-
"""
用户配置（JSON）与密码（凭据管理器）的读写。

职责边界很清楚：
    本模块  → 非敏感设置：学工号、账号、运营商、行为开关、门户选择
    secrets → 密码，走系统凭据管理器

**配置文件里不会出现密码**，这不是靠约定，而是靠数据结构上没有这个字段。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths, secrets


@dataclass
class AppConfig:
    """全部用户设置。刻意没有任何密码字段。"""

    # --- 账号信息（非敏感）---
    student_id: str = ""              # 学(工)号
    account: str = ""                 # 上网账号（留空则用学工号）
    operator: str = "自动选择"

    # --- 门户 ---
    profile_name: str = ""            # 选中的门户档案名
    portal_url: str = "auto"          # auto = 每次现场自动发现
    profile_override: dict = field(default_factory=dict)   # 覆盖档案里的个别项

    # --- 行为 ---
    auto_connect: bool = True         # 断线自动重新认证
    check_interval: int = 60          # 自动检测间隔（秒）
    start_minimized: bool = False     # 开机启动时直接缩到托盘
    minimize_to_tray: bool = True     # 点击最小化时进托盘
    close_to_tray: bool = True        # 点关闭按钮时进托盘而不是退出
    autostart: bool = False           # 开机自动启动

    # --- Router 视觉节点（默认关闭，不影响现有独立模式）---
    vision_node_enabled: bool = False  # 作为视觉节点连接 Router
    router_url: str = ""               # Router 地址，如 http://192.168.1.1:7700

    # --- 状态 ---
    first_run_done: bool = False      # 首次启动引导是否走完

    # ------------------------------------------------------------------
    @property
    def effective_account(self) -> str:
        return (self.account or self.student_id).strip()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AppConfig":
        known = set(cls.__dataclass_fields__)
        clean = {k: v for k, v in (d or {}).items() if k in known}
        # 类型兜底：手改过的 JSON 很容易把 int/bool 写成字符串
        for key, typ in (("check_interval", int), ("auto_connect", bool),
                         ("start_minimized", bool), ("minimize_to_tray", bool),
                         ("close_to_tray", bool), ("autostart", bool),
                         ("vision_node_enabled", bool),
                         ("first_run_done", bool)):
            if key in clean:
                try:
                    if typ is bool:
                        v = clean[key]
                        clean[key] = v if isinstance(v, bool) else \
                            str(v).strip().lower() in ("1", "true", "yes", "on")
                    else:
                        clean[key] = typ(clean[key])
                except Exception:
                    clean.pop(key, None)
        if not isinstance(clean.get("profile_override"), dict):
            clean["profile_override"] = {}
        return cls(**clean)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        """
        读配置。文件不存在 / 损坏 / 权限不足都返回默认值，绝不抛异常 ——
        首次启动时这些都是正常情况，不能因为读不到配置就起不来。
        """
        p = path or paths.config_file()
        try:
            if not p.exists():
                return cls()
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return cls()
            return cls.from_dict(data)
        except Exception:
            return cls()

    def save(self, path: Path | None = None) -> bool:
        """
        原子写：先写临时文件再替换。

        直接覆盖的话，写到一半断电/被杀进程会留下半个 JSON，
        下次启动就读不出来了。
        """
        p = path or paths.config_file()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fp:
                    json.dump(self.to_dict(), fp, ensure_ascii=False, indent=2)
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(tmp, p)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
            return True
        except Exception:
            return False

    # --- 密码（转手给凭据管理器）---
    def get_password(self) -> str:
        return secrets.load(self.profile_name, self.effective_account)

    def set_password(self, password: str) -> bool:
        return secrets.save(self.profile_name, self.effective_account, password)

    def forget_password(self) -> None:
        secrets.delete(self.profile_name, self.effective_account)

    def has_password(self) -> bool:
        return bool(self.get_password())

    # --- 给界面用的组合视图 ---
    def summary(self) -> dict:
        return {
            "学(工)号": self.student_id or "（未填写）",
            "上网账号": self.effective_account or "（未填写）",
            "密码": "已保存" if self.has_password() else "未保存",
            "运营商": self.operator,
            "门户": self.profile_name or "（未选择）",
            "自动重连": "开" if self.auto_connect else "关",
            "开机启动": "开" if self.autostart else "关",
        }
