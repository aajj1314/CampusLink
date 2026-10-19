"""单实例控制：避免重复启动多个后台进程。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\CampusLinkSilent"


class SingleInstance:
    def __init__(self, name: str = MUTEX_NAME):
        self._handle = None
        self._acquired = True
        if not hasattr(ctypes, "WinDLL"):
            return

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        create_mutex.restype = wintypes.HANDLE

        self._handle = create_mutex(None, False, name)
        if not self._handle:
            self._acquired = True
            return
        self._acquired = ctypes.get_last_error() != ERROR_ALREADY_EXISTS

    @property
    def acquired(self) -> bool:
        return self._acquired
