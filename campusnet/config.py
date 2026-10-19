"""配置读取：开发模式读取项目根目录 config.json，打包后读取 %APPDATA%。"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any


def _app_dir() -> Path:
    """程序所在目录：开发时是项目根目录，打包后是 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _resource_dir() -> Path:
    """只读资源目录：打包后指向 PyInstaller 的解包目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", str(_app_dir())))
    return _app_dir()


APP_DIR = _app_dir()
RESOURCE_DIR = _resource_dir()
# 开发模式沿用项目目录；打包后配置和日志放到用户可写的 %APPDATA%
DATA_DIR = (
    Path(os.environ.get("APPDATA", str(APP_DIR))) / "CampusLinkSilent"
    if getattr(sys, "frozen", False)
    else APP_DIR
)
CONFIG_PATH = DATA_DIR / "config.json"


class ConfigError(Exception):
    """配置文件读取失败。"""


DEFAULT_CONFIG: dict[str, Any] = {
    "_说明": [
        "1. 填写 account 下的学(工)号、上网账号、密码。",
        "2. 保存后双击 setup.bat 安装（自动装依赖、设置开机自启）。",
        "3. 密码以明文保存在本文件，请勿把本文件发送给他人。",
    ],
    "account": {
        "student_id": "",
        "username": "",
        "password": "",
    },
    "portal": {
        "base_url": "http://10.6.6.6",
        "isp": "自动选择",
        "save_login_info": False,
    },
    "monitor": {
        "check_interval_seconds": 20,
        "request_timeout_seconds": 6,
        "max_captcha_retries": 6,
        "retry_delay_seconds": 1.2,
        "login_verify_seconds": 5,
        "network_cooldown_seconds": 30,
        "account_cooldown_seconds": 600,
        "antibot_cooldown_seconds": 60,
        "captcha_fail_cooldown_seconds": 120,
        "open_browser_on_failure": True,
        "browser_cooldown_seconds": 1800,
    },
    "captcha": {
        "model_path": "models/crnn_math.onnx",
        "confidence_threshold": 0.9,
    },
    "logging": {
        "level": "INFO",
        "max_bytes": 1048576,
        "backup_count": 3,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def ensure_config_file() -> None:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_config() -> dict[str, Any]:
    ensure_config_file()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"读取 config.json 失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config.json 顶层必须是一个 JSON 对象")

    config = _deep_merge(DEFAULT_CONFIG, raw)

    model_override = os.environ.get("CAMPUSLINK_CRNN_MODEL")
    if model_override:
        config["captcha"]["model_path"] = model_override
    model_path = Path(str(config["captcha"]["model_path"]))
    if not model_path.is_absolute():
        model_path = RESOURCE_DIR / model_path
    config["captcha"]["model_path"] = str(model_path)
    return config


def missing_account_fields(config: dict[str, Any]) -> list[str]:
    account = config.get("account", {})
    fields = (
        ("student_id", "学(工)号"),
        ("username", "上网账号"),
        ("password", "密码"),
    )
    return [label for key, label in fields if not str(account.get(key, "")).strip()]
