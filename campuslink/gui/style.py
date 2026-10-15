# -*- coding: utf-8 -*-
"""
界面主题：配色、字体、QSS 样式表、图标绘制。

全部用代码生成，不依赖任何图片资源文件 ——
这样打包体积小、换色只要改这里的常量，也不会出现「图标路径在打包后找不到」的问题。

设计取向：浅色、留白足够、圆角、主色克制。
目标用户是完全不懂电脑的人，所以不用深色主题、不用花哨动效，
字号偏大、按钮够大、状态一眼能看懂。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)

# ------------------------------------------------------------------ 配色
PRIMARY = "#2563EB"          # 主色
PRIMARY_HOVER = "#1D4ED8"
PRIMARY_PRESSED = "#1E40AF"
PRIMARY_SOFT = "#EFF6FF"

TEXT = "#1F2937"
TEXT_MUTED = "#6B7280"
TEXT_FAINT = "#9CA3AF"

BG = "#F5F7FA"
CARD = "#FFFFFF"
BORDER = "#E5E7EB"
BORDER_FOCUS = PRIMARY

OK = "#16A34A"               # 已联网
OK_SOFT = "#ECFDF5"
WARN = "#D97706"             # 需要认证
WARN_SOFT = "#FFFBEB"
ERR = "#DC2626"              # 出错
ERR_SOFT = "#FEF2F2"
MUTED = "#9CA3AF"            # 未知 / 离线

RADIUS = 10


def app_font(size: int = 10) -> QFont:
    """
    选一个在中文 Windows 上好看的字体。

    「Microsoft YaHei UI」是 Win10/11 的界面字体，比默认的 SimSun 好太多；
    找不到就退回系统默认。
    """
    for family in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                   "Noto Sans CJK SC", "Segoe UI"):
        f = QFont(family, size)
        if f.exactMatch() or family.startswith("Segoe"):
            return f
    return QFont("", size)


QSS = f"""
QWidget {{
    color: {TEXT};
}}
QMainWindow, #Root {{
    background: {BG};
}}

/* ---- 卡片 ---- */
#Card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}

/* ---- 标题 ---- */
#AppTitle {{
    font-size: 17px;
    font-weight: 600;
    color: {TEXT};
}}
#AppSubtitle {{
    font-size: 12px;
    color: {TEXT_MUTED};
}}
#SectionTitle {{
    font-size: 12px;
    font-weight: 600;
    color: {TEXT_MUTED};
    padding-bottom: 2px;
}}
#FieldLabel {{
    font-size: 13px;
    color: {TEXT_MUTED};
}}
#Hint {{
    font-size: 11px;
    color: {TEXT_FAINT};
}}

/* ---- 输入框 ---- */
QLineEdit, QComboBox {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 9px 12px;
    font-size: 14px;
    selection-background-color: {PRIMARY};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {BORDER_FOCUS};
}}
QLineEdit:disabled, QComboBox:disabled {{
    background: {BG};
    color: {TEXT_FAINT};
}}
QLineEdit[echoMode="2"] {{
    letter-spacing: 2px;
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox QAbstractItemView {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    background: {CARD};
    selection-background-color: {PRIMARY_SOFT};
    selection-color: {TEXT};
    padding: 4px;
    outline: none;
}}

/* ---- 按钮 ---- */
QPushButton {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 9px 16px;
    font-size: 14px;
    color: {TEXT};
}}
QPushButton:hover  {{ background: {BG}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: {BG}; }}

QPushButton#Primary {{
    background: {PRIMARY};
    border: none;
    color: #FFFFFF;
    font-size: 15px;
    font-weight: 600;
    padding: 12px 20px;
}}
QPushButton#Primary:hover   {{ background: {PRIMARY_HOVER}; }}
QPushButton#Primary:pressed {{ background: {PRIMARY_PRESSED}; }}
QPushButton#Primary:disabled {{ background: #A5B4D4; color: #FFFFFF; }}

QPushButton#Ghost {{
    background: transparent;
    border: 1px solid {BORDER};
    color: {TEXT_MUTED};
}}
QPushButton#Ghost:hover {{ background: {CARD}; color: {TEXT}; }}

QPushButton#LinkBtn {{
    background: transparent;
    border: none;
    color: {PRIMARY};
    font-size: 12px;
    padding: 2px 4px;
    text-align: left;
}}
QPushButton#LinkBtn:hover {{ color: {PRIMARY_HOVER}; text-decoration: underline; }}

QPushButton#Eye {{
    background: transparent;
    border: none;
    padding: 0 6px;
    color: {TEXT_MUTED};
    font-size: 15px;
}}

/* ---- 复选框 ---- */
QCheckBox {{
    font-size: 13px;
    color: {TEXT};
    spacing: 8px;
    padding: 3px 0;
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border: 1px solid #CBD5E1;
    border-radius: 5px;
    background: {CARD};
}}
QCheckBox::indicator:hover {{ border-color: {PRIMARY}; }}
QCheckBox::indicator:checked {{
    background: {PRIMARY};
    border-color: {PRIMARY};
    image: none;
}}

/* ---- 状态条 ---- */
#StatusBar {{
    border-radius: 8px;
    padding: 11px 14px;
    font-size: 13px;
}}

/* ---- 滚动区 ---- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 9px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #CBD5E1; border-radius: 4px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #94A3B8; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- 托盘菜单 ---- */
QMenu {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 22px 7px 14px;
    border-radius: 6px;
    font-size: 13px;
}}
QMenu::item:selected {{ background: {PRIMARY_SOFT}; color: {TEXT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
"""


# ------------------------------------------------------------------ 图标
def _dot_pixmap(color: str, size: int = 14) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return pm


def state_dot(color: str, size: int = 14) -> QIcon:
    return QIcon(_dot_pixmap(color, size))


def app_icon(size: int = 256, connected: bool | None = None) -> QIcon:
    """
    画应用/托盘图标：圆角方块 + 一个 Wi-Fi 弧。

    程序化绘制，不用图片文件 —— 打包后不存在「找不到图标」的问题，
    也能顺便用颜色表示状态（连上是绿色，未连是蓝灰）。
    """
    base = QColor(OK if connected else PRIMARY if connected is False else PRIMARY)
    if connected is None:
        base = QColor(PRIMARY)

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    # 圆角底
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, base.lighter(118))
    grad.setColorAt(1.0, base.darker(112))
    path = QPainterPath()
    r = size * 0.22
    path.addRoundedRect(QRectF(0, 0, size, size), r, r)
    p.fillPath(path, QBrush(grad))

    # Wi-Fi 三段弧
    pen = QPen(QColor("#FFFFFF"))
    pen.setCapStyle(Qt.RoundCap)
    cx, cy = size / 2, size * 0.72
    for i, (rad, width) in enumerate(((0.16, 0.055), (0.28, 0.065), (0.40, 0.075))):
        pen.setWidthF(size * width)
        p.setPen(pen)
        rr = size * rad
        p.drawArc(QRectF(cx - rr, cy - rr, rr * 2, rr * 2), 30 * 16, 120 * 16)

    # 中心圆点
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#FFFFFF"))
    d = size * 0.11
    p.drawEllipse(QPointF(cx, cy), d / 2, d / 2)
    p.end()
    return QIcon(pm)


def status_colors(state_value: str) -> tuple[str, str, str]:
    """
    按网络状态返回 (前景色, 背景色, 展示文字)。

    state_value 用 NetworkState.value：unknown/offline/need_login/online/checking
    """
    return {
        "online":     (OK,    OK_SOFT,   "已联网"),
        "need_login": (WARN,  WARN_SOFT, "需要认证"),
        "offline":    (MUTED, "#F3F4F6", "未连接网络"),
        "checking":   (PRIMARY, PRIMARY_SOFT, "检测中…"),
        "unknown":    (MUTED, "#F3F4F6", "未检测"),
    }.get(state_value, (MUTED, "#F3F4F6", "未知"))
