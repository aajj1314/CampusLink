# -*- coding: utf-8 -*-
"""
验证码标注工具（tkinter，键盘操作，为「一口气标上千张」优化）。

为什么要自己写：1000 张靠鼠标点太慢。这里的设计是
**输入框永远有焦点、回车即下一张**，配合 autolabel.py 的预标注，
正确的图只需按一次回车。

用法：
    python ml/scripts/label_tool.py

操作：
    输入 `7+9` / `7加9` / `7*9` / `28/4`  → 回车保存并下一张
        符号会被自动归一成汉字（+→加，*→乘，/→除，-→减）
    直接回车   → 用预填的自动识别结果作为标注（若预填为空则跳过）
    Esc        → 保存并退出
    Ctrl+Z     → 回退到上一张（清掉它的标注）

产物：ml/dataset/labels/labels.csv（两列：filename,label）
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

from PIL import Image, ImageTk  # noqa: E402

ZOOM = 3                      # 200x60 放大到 600x180，看得清干扰
OK_COLOR = "#1a7f37"
BAD_COLOR = "#b42318"
HINT_COLOR = "#57606a"


class LabelTool:
    def __init__(self, root: tk.Tk, raw_dir: Path, labels_dir: Path):
        self.root = root
        self.raw_dir = raw_dir
        self.labels_path = labels_dir / C.LABEL_FILE
        self.auto_path = labels_dir / "labels_auto.csv"

        self.confirmed: dict[str, str] = C.read_labels(self.labels_path)
        self.auto: dict[str, str] = C.read_labels(self.auto_path)

        # 待标的图：所有图，按文件名排序；已确认的跳过
        self.images = C.list_images(raw_dir)
        self.queue = [p for p in self.images if p.name not in self.confirmed]
        self.total = len(self.images)
        self.cursor = 0
        self.history: list[str] = []      # 提交过的文件名，供 Ctrl+Z 回退
        self._photo: ImageTk.PhotoImage | None = None

        self._build_ui()
        self._load_current()

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.root.title("CampusLink 算式验证码标注")
        self.root.configure(bg="#ffffff")
        self.root.geometry("680x400")
        self.root.resizable(False, False)

        self.lb_progress = tk.Label(self.root, text="", font=("Microsoft YaHei UI", 11),
                                    bg="#ffffff", fg=HINT_COLOR, anchor="w")
        self.lb_progress.pack(fill="x", padx=16, pady=(12, 4))

        self.pic = tk.Label(self.root, bg="#f6f8fa", width=600, height=180,
                            borderwidth=1, relief="solid")
        self.pic.pack(padx=16, pady=6)

        self.entry = tk.Entry(self.root, font=("Consolas", 22), justify="center",
                              width=24, relief="solid", borderwidth=1)
        self.entry.pack(pady=(10, 4))
        self.entry.bind("<Return>", lambda e: self._commit())
        self.entry.bind("<Escape>", lambda e: self._quit())
        self.entry.bind("<KeyRelease>", lambda e: self._preview())
        self.root.bind("<Control-z>", lambda e: self._undo())
        self.entry.focus_set()

        self.lb_state = tk.Label(self.root, text="", font=("Microsoft YaHei UI", 11),
                                 bg="#ffffff", fg=HINT_COLOR)
        self.lb_state.pack(pady=(0, 2))

        self.lb_hint = tk.Label(
            self.root,
            text="输入 7+9 / 7加9 / 28/4 后回车    ·    回车=确认下一张    ·    Esc=退出    ·    Ctrl+Z=回退",
            font=("Microsoft YaHei UI", 9), bg="#ffffff", fg="#8c959f")
        self.lb_hint.pack(pady=(0, 10))

    # ------------------------------------------------------------ 数据
    def _current(self) -> Path | None:
        return self.queue[self.cursor] if self.cursor < len(self.queue) else None

    def _load_current(self) -> None:
        done = len(self.confirmed)
        path = self._current()
        if path is None:
            self.pic.configure(image="", text="✅ 全部标完了", font=("Microsoft YaHei UI", 16),
                               fg=OK_COLOR)
            self.lb_progress.configure(text=f"已确认 {done} / {self.total}")
            self.lb_state.configure(text=f"标注文件：{self.labels_path}")
            self.entry.configure(state="disabled")
            return

        self.lb_progress.configure(
            text=f"待标 {len(self.queue) - self.cursor} / {len(self.queue)}    "
                 f"已确认 {done} / {self.total}    {path.name}")

        try:
            img = Image.open(path).convert("RGB")
            img = img.resize((img.width * ZOOM, img.height * ZOOM), Image.Resampling.LANCZOS)
            self._photo = ImageTk.PhotoImage(img)
            self.pic.configure(image=self._photo, text="", width=img.width, height=img.height)
        except Exception as exc:
            self.pic.configure(image="", text=f"读图失败：{exc}")

        # 预填自动识别结果（通常是对的，回车即可）
        self.entry.delete(0, "end")
        auto = self.auto.get(path.name, "")
        if auto:
            self.entry.insert(0, auto)
            self.entry.select_range(0, "end")
        self._preview()

    def _preview(self) -> None:
        raw = self.entry.get().strip()
        norm = C.normalize_text(raw)
        if not raw:
            self.lb_state.configure(text="（空 → 回车跳过）", fg=HINT_COLOR)
            return
        ok = len(norm) >= 3 and any(ch in "加减乘除" for ch in norm)
        if ok:
            extra = f"  （预标注）" if self.auto.get(self._current().name if self._current() else "") == norm else ""
            self.lb_state.configure(text=f"→ {norm}{extra}", fg=OK_COLOR)
        else:
            self.lb_state.configure(text=f"格式不对：{norm or raw}  需要「数字 运算符 数字」",
                                    fg=BAD_COLOR)

    def _commit(self) -> None:
        path = self._current()
        if path is None:
            return
        raw = self.entry.get().strip()
        norm = C.normalize_text(raw)

        if not raw:
            # 空 → 跳过（不写入）
            self.cursor += 1
            self._load_current()
            return

        if len(norm) < 3 or not any(ch in "加减乘除" for ch in norm):
            self.lb_state.configure(text=f"格式不对：{norm or raw}", fg=BAD_COLOR)
            self.root.bell()
            return

        self.confirmed[path.name] = norm
        self.history.append(path.name)
        C.write_labels(self.labels_path, self.confirmed)   # 每张都落盘，防意外丢数据
        self.cursor += 1
        self._load_current()

    def _undo(self) -> None:
        if not self.history:
            self.lb_state.configure(text="没有可回退的", fg=BAD_COLOR)
            return
        name = self.history.pop()
        self.confirmed.pop(name, None)
        C.write_labels(self.labels_path, self.confirmed)
        if self.cursor > 0:
            self.cursor -= 1
        self._load_current()
        self.lb_state.configure(text=f"已撤销 {name}", fg=HINT_COLOR)

    def _quit(self) -> None:
        done = len(self.confirmed)
        if messagebox.askokcancel("退出", f"已确认 {done}/{self.total} 张，退出标注？"):
            self.root.destroy()


def main() -> int:
    root_dir = Path(__file__).resolve().parent.parent
    raw_dir = root_dir / "dataset" / "raw"
    labels_dir = root_dir / "dataset" / "labels"

    if not raw_dir.is_dir() or not C.list_images(raw_dir):
        print(f"没找到图片：{raw_dir}\n先跑：python tools/captcha_sampler.py --count 1000 "
              f"--out ml/dataset/raw", file=sys.stderr)
        return 2
    labels_dir.mkdir(parents=True, exist_ok=True)

    root = tk.Tk()
    LabelTool(root, raw_dir, labels_dir)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
