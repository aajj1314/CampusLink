# -*- coding: utf-8 -*-
"""
Web 版标注工具 —— Linux 服务器（无桌面环境）也能用。

用 tkinter 那版需要图形环境，而训练机往往是 SSH 上去的纯命令行机器。
这里用标准库起一个 HTTP 服务，浏览器打开即可标注；也可以配合 SSH 端口转发：

    # 在训练机上
    python ml/scripts/label_web.py --port 8765 --host 127.0.0.1

    # 在你本机
    ssh -L 8765:127.0.0.1:8765 user@训练机
    # 然后浏览器开 http://127.0.0.1:8765

操作：输入 `7+9` / `7加9` / `28/4` → 回车确认并下一张。
有预标注（labels_auto.csv）时会自动填好，正确的直接回车。

零第三方依赖：只要 Python 3.9+。图片由服务端读取，不需要装 PIL。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>CampusLink 算式验证码标注</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: "Microsoft YaHei UI","PingFang SC","Noto Sans CJK SC",sans-serif;
         background:#f6f8fa; color:#1f2328; display:flex; align-items:center;
         justify-content:center; min-height:100vh; }
  .card { background:#fff; border:1px solid #d0d7de; border-radius:12px; padding:22px 26px;
          width:720px; box-shadow:0 6px 24px rgba(31,35,40,.06); }
  .bar { display:flex; justify-content:space-between; font-size:13px; color:#57606a; margin-bottom:12px; }
  .imgwrap { background:#f6f8fa; border:1px solid #d0d7de; border-radius:8px;
             display:flex; align-items:center; justify-content:center; min-height:200px; }
  img { display:block; max-width:100%; image-rendering:pixelated; }
  input { width:100%; margin-top:16px; padding:12px 14px; font-size:26px; text-align:center;
          font-family:Consolas,Menlo,monospace; letter-spacing:2px; border:2px solid #d0d7de;
          border-radius:8px; outline:none; }
  input:focus { border-color:#0969da; }
  .state { height:20px; margin-top:8px; font-size:13px; }
  .ok { color:#1a7f37; } .bad { color:#cf222e; } .muted { color:#8c959f; }
  .hint { margin-top:10px; font-size:12px; color:#8c959f; line-height:1.7; }
  kbd { background:#eaeef2; border:1px solid #d0d7de; border-bottom-width:2px;
        border-radius:5px; padding:1px 6px; font-size:11px; font-family:inherit; }
</style>
</head>
<body>
<div class="card">
  <div class="bar"><span id="progress">加载中…</span><span id="file"></span></div>
  <div class="imgwrap"><img id="pic" alt=""></div>
  <input id="inp" autocomplete="off" spellcheck="false" placeholder="例如 7+9">
  <div class="state" id="state"></div>
  <div class="hint">
    输入 <b>7+9</b> / <b>7加9</b> / <b>28/4</b>（+ 加、* 乘、/ 除、- 减，符号会自动转汉字）<br>
    <kbd>Enter</kbd> 确认下一张　<kbd>Ctrl+Z</kbd> 撤销上一张　<kbd>Enter</kbd> 留空则跳过
  </div>
</div>
<script>
const inp = document.getElementById('inp');
const pic = document.getElementById('pic');
const stateEl = document.getElementById('state');
const progEl = document.getElementById('progress');
const fileEl = document.getElementById('file');
let cur = null;

function preview() {
  const raw = inp.value.trim();
  if (!raw) { stateEl.textContent = '（空 → 回车跳过）'; stateEl.className = 'state muted'; return; }
  let s = raw.replace(/＋/g,'加').replace(/\+/g,'加')
             .replace(/－|−|—|-/g,'减').replace(/＊|\*|×|x|X/g,'乘')
             .replace(/／|\//g,'除');
  s = s.replace(/[^0-9加减乘除]/g,'');
  const ok = /[0-9][加减乘除][0-9]/.test(s);
  stateEl.textContent = ok ? ('→ ' + s) : ('格式不对：' + (s || raw));
  stateEl.className = 'state ' + (ok ? 'ok' : 'bad');
}

async function load() {
  const r = await fetch('/api/next');
  const d = await r.json();
  if (d.done) {
    pic.removeAttribute('src');
    pic.alt = '✅ 全部标完了';
    inp.disabled = true;
    progEl.textContent = '已确认 ' + d.confirmed + ' / ' + d.total;
    fileEl.textContent = '';
    stateEl.textContent = '';
    return;
  }
  cur = d.name;
  pic.src = '/img/' + encodeURIComponent(d.name) + '?t=' + Date.now();
  fileEl.textContent = d.name;
  progEl.textContent = '待标 ' + d.remaining + '　已确认 ' + d.confirmed + ' / ' + d.total;
  inp.value = d.auto || '';
  inp.focus(); inp.select();
  preview();
}

inp.addEventListener('input', preview);
inp.addEventListener('keydown', async (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    const raw = inp.value.trim();
    if (!raw) { await fetch('/api/skip', {method:'POST', headers:{'Content-Type':'application/json'},
                                          body: JSON.stringify({name: cur})}); return load(); }
    const res = await fetch('/api/label', {method:'POST', headers:{'Content-Type':'application/json'},
                                           body: JSON.stringify({name: cur, label: raw})});
    const d = await res.json();
    if (!d.ok) { stateEl.textContent = d.error || '保存失败'; stateEl.className = 'state bad'; return; }
    load();
  }
});
document.addEventListener('keydown', async (e) => {
  if (e.ctrlKey && (e.key === 'z' || e.key === 'Z')) {
    e.preventDefault();
    const r = await fetch('/api/undo', {method:'POST'});
    const d = await r.json();
    if (d.ok) load();
  }
});
load();
</script>
</body>
</html>
"""


class Store:
    """标注状态的唯一真相源，所有请求串行访问。"""

    def __init__(self, raw_dir: Path, labels_dir: Path):
        self.raw_dir = raw_dir
        self.labels_path = labels_dir / C.LABEL_FILE
        self.auto_path = labels_dir / "labels_auto.csv"
        self.lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        self.confirmed = C.read_labels(self.labels_path)
        self.auto = C.read_labels(self.auto_path)
        self.images = C.list_images(self.raw_dir)
        self.history: list[str] = []

    def next_pending(self) -> str | None:
        for p in self.images:
            if p.name not in self.confirmed:
                return p.name
        return None

    def snapshot(self) -> dict:
        name = self.next_pending()
        base = {"total": len(self.images), "confirmed": len(self.confirmed)}
        if name is None:
            return {**base, "done": True}
        remaining = sum(1 for p in self.images if p.name not in self.confirmed)
        return {**base, "done": False, "name": name, "remaining": remaining,
                "auto": self.auto.get(name, "")}

    def save(self, name: str, label: str) -> tuple[bool, str]:
        norm = C.normalize_text(label)
        if len(norm) < 3 or not any(ch in "加减乘除" for ch in norm):
            return False, f"格式不对：{norm or label}"
        self.confirmed[name] = norm
        self.history.append(name)
        C.write_labels(self.labels_path, self.confirmed)
        return True, norm

    def undo(self) -> bool:
        if not self.history:
            return False
        name = self.history.pop()
        self.confirmed.pop(name, None)
        C.write_labels(self.labels_path, self.confirmed)
        return True


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CampusLinkLabel/1.0"

        def log_message(self, fmt, *args):      # 静音，避免刷屏
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                return {}

        # -------------------------------------------------- GET
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if u.path == "/api/next":
                with store.lock:
                    self._json(store.snapshot())
                return
            if u.path == "/img":
                name = (parse_qs(u.query).get("name") or [""])[0]
                self._serve_image(name)
                return
            if u.path.startswith("/img/"):
                self._serve_image(u.path[len("/img/"):])
                return
            self._send(404, b"not found", "text/plain")

        def _serve_image(self, name: str) -> None:
            # 只允许 raw_dir 下的裸文件名，挡掉 ../ 之类
            safe = Path(name).name
            path = store.raw_dir / safe
            if not safe or not path.is_file():
                self._send(404, b"no such image", "text/plain")
                return
            self._send(200, path.read_bytes(), "image/png")

        # -------------------------------------------------- POST
        def do_POST(self):
            u = urlparse(self.path)
            body = self._read_json()
            with store.lock:
                if u.path == "/api/label":
                    ok, msg = store.save(str(body.get("name") or ""),
                                         str(body.get("label") or ""))
                    self._json({"ok": ok, "message": msg,
                                "error": None if ok else msg})
                    return
                if u.path == "/api/skip":
                    self._json({"ok": True})
                    return
                if u.path == "/api/undo":
                    ok = store.undo()
                    self._json({"ok": ok})
                    return
            self._json({"ok": False, "error": "unknown endpoint"}, 404)

    return Handler


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Web 标注工具（无第三方依赖）")
    ap.add_argument("--raw", default=str(root / "dataset" / "raw"))
    ap.add_argument("--labels-dir", default=str(root / "dataset" / "labels"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)

    raw_dir = Path(args.raw)
    labels_dir = Path(args.labels_dir)
    if not C.list_images(raw_dir):
        print(f"没找到图片：{raw_dir}", file=sys.stderr)
        return 2
    labels_dir.mkdir(parents=True, exist_ok=True)

    store = Store(raw_dir, labels_dir)
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    snap = store.snapshot()
    print(f"图片 {snap['total']} 张，已确认 {snap['confirmed']}，待标 {snap['total']-snap['confirmed']}")
    print(f"打开 http://{args.host}:{args.port}/ 开始标注（Ctrl+C 停止）")
    if args.host == "127.0.0.1":
        print("若在远程机器上，用 SSH 转发：ssh -L 8765:127.0.0.1:8765 user@host")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
