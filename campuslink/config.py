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
class Account:
    """
    一套账号信息（非敏感部分）。密码走 secrets 模块，不进这里。

    多账号支持（2026-11-18 新增）：一台电脑上可能有多个人共用，
    或者一个人有多个上网账号。每套账号单独存，靠 label 区分。

    密码的存储键是 secrets.entry_name(portal_name, account)，
    所以不同账号天然不冲突 —— 这一点在加多账号之前就已经成立。
    """

    label: str = ""           # 显示名，如「我」「室友」「备用」
    student_id: str = ""      # 学(工)号
    account: str = ""         # 上网账号（留空则用学工号）
    operator: str = "自动选择"

    def effective_account(self) -> str:
        """上网账号留空时回退到学工号 —— 门户里两者常常是同一个。"""
        return (self.account or self.student_id or "").strip()

    def display_name(self) -> str:
        """界面下拉框里显示的文字。优先 label，其次账号，最后「未命名」。"""
        if self.label.strip():
            return self.label.strip()
        acc = self.effective_account()
        return acc if acc else "（未命名）"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


@dataclass
class AppConfig:
    """全部用户设置。刻意没有任何密码字段。"""

    # --- 账号信息（非敏感）---
    student_id: str = ""              # 学(工)号
    account: str = ""                 # 上网账号（留空则用学工号）
    operator: str = "自动选择"

    # --- 多账号（2026-11-18 新增）---
    #: 账号列表。上表三个字段是「当前账号」的镜像，
    #: 保留是为了兼容旧配置文件 —— 老版本只有这三个字段，
    #: 加载时自动迁移成 accounts 里的一条。
    accounts: list = field(default_factory=list)
    #: 当前选中的账号在 accounts 里的索引。-1 表示「用顶层的三个字段」，
    #: 这是旧配置和老版本行为的兼容路径。
    current_account_index: int = -1

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
    #: 验证码人工输入：每次登录弹出验证码图片，由用户手工填答案。
    #: 算式型验证码的自动识别只有约 13% 正确率且错答会消耗登录尝试，
    #: 所以对这类门户推荐打开。字符型门户保持关闭（自动识别 90%+）。
    captcha_manual: bool = False

    #: 注销验证码的置信度门限（2026-11-18 新增）。
    #: ddddocr 的 probability 输出低于此值时**不提交**，直接换图。
    #: 实测注销验证码（80×24 的 4 位字符）的置信度在 0.95~0.99，
    #: 0.90 的门限基本只会拦下真正认错的，不会误杀。
    #: 调高到 0.95 更保守（重试次数会多一点），调低到 0.80 更激进（可能提交错答案）。
    logout_min_confidence: float = 0.90

    # --- Router 视觉节点（默认关闭，不影响现有独立模式）---
    vision_node_enabled: bool = False  # 作为视觉节点连接 Router
    router_url: str = ""               # Router 地址，如 http://192.168.1.1:7700

    # --- 校园网共享（Windows 移动热点 TTL 修复）---
    #: 启用共享助手：修 inbound TTL<=1（救回校园网代理 SYN-ACK）与出站 TTL 归一。
    #: 需要管理员权限 + pydivert；失败不影响认证功能。
    hotspot_ttl: bool = False

    # --- HTTP UA 归一（2026-11-18 新增）---
    #: 把明文 HTTP 请求的 User-Agent 统一成一个值。
    #: 私接检测不只看 TTL，还看 UA 的多样性 —— 同一出口出现
    #: iPhone / Android / Windows 多种 UA 就是"多设备"的铁证。
    #: 只对 80 端口有效（HTTPS 的 UA 在 TLS 加密层里）。
    ua_mask: bool = False
    #: 目标 UA。建议保持默认（11 字节）。
    #: 越短覆盖面越广 —— 新 UA 不能比原 UA 长，短 UA 请求会被跳过。
    target_ua: str = "Mozilla/5.0"

    # --- 状态 ---
    first_run_done: bool = False      # 首次启动引导是否走完

    # ------------------------------------------------------------------
    @property
    def effective_account(self) -> str:
        return (self.account or self.student_id).strip()

    # -------------------------------------------------- 多账号（2026-11-18）
    def current_account(self) -> Account:
        """
        取当前账号。

        兼容路径：`current_account_index < 0` 时，用顶层三个字段
        临时拼一个 Account 返回 —— 这是旧配置（只有 student_id/account/
        operator）和老版本行为。这样调用方永远拿到 Account，
        不用到处判断"有没有多账号"。
        """
        if 0 <= self.current_account_index < len(self.accounts):
            a = self.accounts[self.current_account_index]
            return a if isinstance(a, Account) else Account.from_dict(a)
        return Account(
            student_id=self.student_id,
            account=self.account,
            operator=self.operator,
        )

    def set_current_account(self, acc: Account) -> None:
        """
        把 acc 写回「当前账号」，同时镜像到顶层三个字段。

        为什么两边都写：顶层字段是文件格式的一部分（向后兼容），
        而且 CLI 和旧代码都直接读它们。镜像一次比到处改调用方便宜。
        """
        self.student_id = acc.student_id
        self.account = acc.account
        self.operator = acc.operator
        # 如果这套账号已经在列表里，同步更新；否则不改索引
        for i, a in enumerate(self.accounts):
            existing = a if isinstance(a, Account) else Account.from_dict(a)
            if (existing.student_id == acc.student_id
                    and existing.effective_account() == acc.effective_account()):
                self.accounts[i] = acc
                self.current_account_index = i
                return

    def add_account(self, acc: Account) -> int:
        """
        追加一套账号，返回它的索引。

        同一学工号 + 同账号的已存在时，更新它而不是重复添加 ——
        用户改了个备注名再点"添加"，不该出现两条一样的。
        """
        for i, a in enumerate(self.accounts):
            existing = a if isinstance(a, Account) else Account.from_dict(a)
            if (existing.student_id == acc.student_id
                    and existing.effective_account() == acc.effective_account()):
                self.accounts[i] = acc
                return i
        self.accounts.append(acc)
        return len(self.accounts) - 1

    def remove_account(self, index: int) -> bool:
        """
        删除指定账号。返回是否删了。

        删的是当前账号时，索引归到 -1（回到顶层字段路径），
        避免索引越界或指向错误项。
        """
        if not (0 <= index < len(self.accounts)):
            return False
        self.accounts.pop(index)
        if self.current_account_index == index:
            self.current_account_index = -1
            self.student_id = ""
            self.account = ""
            self.operator = "自动选择"
        elif self.current_account_index > index:
            # 删的是前面的，索引要跟着前移
            self.current_account_index -= 1
        return True

    def account_labels(self) -> list[str]:
        """给下拉框用的显示名列表。"""
        return [(a if isinstance(a, Account) else Account.from_dict(a))
                .display_name() for a in self.accounts]

    def to_dict(self) -> dict:
        d = asdict(self)
        # accounts 里的 Account 已经被 asdict 递归处理成 dict 了
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AppConfig":
        known = set(cls.__dataclass_fields__)
        clean = {k: v for k, v in (d or {}).items() if k in known}
        # 类型兜底：手改过的 JSON 很容易把 int/bool 写成字符串
        for key, typ in (("check_interval", int), ("auto_connect", bool),
                         ("start_minimized", bool), ("minimize_to_tray", bool),
                         ("close_to_tray", bool), ("autostart", bool),
                         ("vision_node_enabled", bool),
                         ("hotspot_ttl", bool),
                         ("ua_mask", bool),
                         ("captcha_manual", bool),
                         ("logout_min_confidence", float),
                         ("first_run_done", bool),
                         ("current_account_index", int)):
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

        # accounts 反序列化：JSON 里是 dict 列表，内存里要 Account 对象
        raw_accounts = clean.get("accounts")
        if isinstance(raw_accounts, list):
            clean["accounts"] = [
                Account.from_dict(x) if isinstance(x, dict) else x
                for x in raw_accounts
            ]
        else:
            clean["accounts"] = []

        # 旧配置迁移：有账号但 accounts 为空 → 自动建一条
        if not clean["accounts"] and (clean.get("student_id") or clean.get("account")):
            clean["accounts"] = [Account(
                student_id=clean.get("student_id", ""),
                account=clean.get("account", ""),
                operator=clean.get("operator", "自动选择"),
            )]
            clean["current_account_index"] = 0

        # 索引越界保护（手改 JSON 很容易写错）
        idx = clean.get("current_account_index", -1)
        if idx >= len(clean["accounts"]) or idx < -1:
            clean["current_account_index"] = -1

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
