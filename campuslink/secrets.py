# -*- coding: utf-8 -*-
"""
密码存储 —— 走操作系统凭据管理器，**绝不落明文**。

    Windows : 凭据管理器（控制面板 → 用户帐户 → 凭据管理器 → Windows 凭据）
              条目名形如 CampusLink:<门户>:<账号>
    Linux   : SecretService（gnome-keyring / KWallet）
    macOS   : Keychain

设计原则（对应任务书「不得明文存储」）：

* **没有可用的后端时，宁可不保存密码**，也不能退化成写明文文件。
  `save()` 会返回 False，GUI 据此提示用户「这台电脑无法安全保存密码，
  每次启动需要重新输入」。
* 配置 JSON 里只放账号、运营商这些非敏感项，密码永远不进 JSON。
* 读取失败（凭据被删、系统换了用户）时返回空串，让用户重填，而不是报错崩溃。

keyring 在部分环境下会直接抛异常（比如没装 D-Bus），所以每个入口都包了异常。
"""
from __future__ import annotations

SERVICE = "CampusLink"

_backend_ok: bool | None = None


def _keyring():
    """延迟导入 keyring —— 导入本身在一些精简环境里就可能失败。"""
    import keyring
    return keyring


def available() -> bool:
    """
    当前环境有没有可用的凭据后端。

    结果会缓存：这个检查比较慢（要试后端），不需要每次调用都做。
    """
    global _backend_ok
    if _backend_ok is not None:
        return _backend_ok
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        _backend_ok = not isinstance(backend, fail.Keyring) and \
            backend.__class__.__module__ != "keyring.backends.fail"
    except Exception:
        _backend_ok = False
    return _backend_ok


def backend_name() -> str:
    """给「关于」页面显示用，例如 Windows credential locker。"""
    try:
        return _keyring().get_keyring().__class__.__name__
    except Exception:
        return "不可用"


def entry_name(portal_name: str, account: str) -> str:
    """
    凭据条目的用户名部分。

    带上门户名，是为了同一台电脑上换学校 / 换账号时互不覆盖。
    """
    portal_name = (portal_name or "default").strip() or "default"
    account = (account or "").strip()
    return f"{portal_name}:{account}"


def save(portal_name: str, account: str, password: str) -> bool:
    """
    保存密码。返回是否成功。

    返回 False 时调用方**必须**告知用户密码没有被保存，
    不能假装成功 —— 否则用户会以为下次不用输密码，结果每次都要输。
    """
    if not password:
        return False
    if not available():
        return False
    try:
        _keyring().set_password(SERVICE, entry_name(portal_name, account), password)
        return True
    except Exception:
        return False


def load(portal_name: str, account: str) -> str:
    """读密码。取不到就返回空串（让用户重填）。"""
    if not available():
        return ""
    try:
        return _keyring().get_password(SERVICE, entry_name(portal_name, account)) or ""
    except Exception:
        return ""


def delete(portal_name: str, account: str) -> bool:
    """删除密码。找不到也算成功（幂等）。"""
    if not available():
        return True
    try:
        _keyring().delete_password(SERVICE, entry_name(portal_name, account))
        return True
    except Exception:
        # keyring.errors.PasswordDeleteError（不存在）也走这里，视作成功
        return True


def status_text() -> str:
    """给 GUI 显示的一句话状态。"""
    if available():
        return f"已启用系统凭据管理器（{backend_name()}）"
    return "系统凭据管理器不可用，密码将不会被保存"
