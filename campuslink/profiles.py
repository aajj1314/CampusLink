# -*- coding: utf-8 -*-
"""
门户档案（PortalProfile）的加载与保存。

思路：把「学校之间的差异」全部收敛到一份 JSON 里 ——
门户地址、表单字段名、成功判定规则、验证码参数。
换一所学校只需要新增一份档案，代码一行都不用改。
这也是给 OpenWrt 移植留的口子。

档案查找顺序（后者覆盖前者）：
    1. 程序自带的 profiles/          （随安装包发布，只读）
    2. 用户数据目录的 profiles/       （换学校时生成，可写）
"""
from __future__ import annotations

import json
from pathlib import Path

from . import paths
from .core.models import PortalProfile

DEFAULT_PROFILE_NAME = "桂林信息科技学院"


def _load_dir(d: Path) -> dict[str, PortalProfile]:
    out: dict[str, PortalProfile] = {}
    try:
        if not d.exists():
            return out
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                prof = PortalProfile.from_dict(data)
                if not prof.name:
                    prof.name = f.stem
                out[prof.name] = prof
            except Exception:
                continue
    except Exception:
        pass
    return out


def list_profiles() -> list[PortalProfile]:
    """全部可用档案，内置的在前、用户自定义的在后。"""
    merged = _load_dir(paths.builtin_profiles_dir())
    merged.update(_load_dir(paths.profiles_dir()))
    return list(merged.values())


def profile_names() -> list[str]:
    return [p.name for p in list_profiles()]


def load_profile(name: str) -> PortalProfile:
    """按名字取档案。找不到时退回第一个可用的；一个都没有就给个空壳。"""
    for p in list_profiles():
        if p.name == name:
            return p
    profs = list_profiles()
    if profs:
        return profs[0]
    return PortalProfile(name=name or DEFAULT_PROFILE_NAME,
                         portal_url="auto",
                         field_map={},
                         success_check={"type": "contains", "value": "成功"})


def save_profile(profile: PortalProfile) -> bool:
    """把档案存到用户数据目录（内置目录通常在 Program Files，不可写）。"""
    try:
        d = paths.profiles_dir()
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{profile.name or 'profile'}.json"
        f.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return True
    except Exception:
        return False
