import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
import collections
import platform
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QObject, QThread,
    pyqtSignal, QTimer, QSize,
)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QPalette, QPixmap, QPainter,
    QBrush, QPen, QLinearGradient, QSyntaxHighlighter, QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QProgressBar, QScrollArea, QSizePolicy, QSplitter, QSpacerItem,
    QStackedWidget, QStatusBar, QTabWidget, QTableView, QTextEdit,
    QToolBar, QVBoxLayout, QWidget, QAbstractItemView,
)

from utils import human_bytes, parse_time, DEFAULT_BASE_URL
from models import InstalledModelsTableModel, RunningModelsTableModel
from workers import BenchResult

#  CUSTOM WIDGETS
# ─────────────────────────────────────────────

class SectionLabel(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setObjectName("label_section")

    
class Separator(QFrame):
    def __init__(self, vertical: bool = False, parent=None):
        super().__init__(parent)
        if vertical:
            self.setObjectName("separator_v")
            self.setFrameShape(QFrame.Shape.VLine)
        else:
            self.setObjectName("separator")
            self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Plain)


class StatCard(QFrame):
    """A small stats card widget."""
    def __init__(self, label: str, value: str = "—", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumWidth(110)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        lbl = QLabel(label.upper())
        lbl.setObjectName("label_section")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.value_lbl = QLabel(value)
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_lbl.setStyleSheet("color: #e2e8f0; font-size: 20px; font-weight: 700;")

        layout.addWidget(lbl)
        layout.addWidget(self.value_lbl)

    def set_value(self, v: str):
        self.value_lbl.setText(v)


class ConnectionBar(QWidget):
    connect_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        # Server icon/label
        icon_lbl = QLabel("⬡")
        icon_lbl.setStyleSheet("color: #3b82f6; font-size: 20px;")
        layout.addWidget(icon_lbl)

        url_label = QLabel("SERVER")
        url_label.setObjectName("label_section")
        layout.addWidget(url_label)

        self.url_input = QLineEdit(DEFAULT_BASE_URL)
        self.url_input.setPlaceholderText("http://localhost:11434")
        self.url_input.setMaximumWidth(280)
        self.url_input.setMinimumWidth(200)
        layout.addWidget(self.url_input)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("btn_primary")
        self.connect_btn.setMinimumWidth(90)
        self.connect_btn.clicked.connect(self._emit_connect)
        layout.addWidget(self.connect_btn)

        layout.addWidget(Separator(vertical=True))

        # Status badge
        self.badge = QLabel("OFFLINE")
        self.badge.setObjectName("conn_badge_disconnected")
        layout.addWidget(self.badge)

        layout.addStretch(1)

        # Filter
        filter_label = QLabel("SEARCH")
        filter_label.setObjectName("label_section")
        layout.addWidget(filter_label)

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter models…")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.setMaximumWidth(220)
        layout.addWidget(self.filter_input)

    def _emit_connect(self):
        self.connect_requested.emit(self.url_input.text().strip())

    def set_connected(self, connected: bool):
        if connected:
            self.badge.setText("● CONNECTED")
            self.badge.setObjectName("conn_badge_connected")
        else:
            self.badge.setText("● OFFLINE")
            self.badge.setObjectName("conn_badge_disconnected")
        # Force style refresh
        self.badge.setStyle(self.badge.style())


class DetailPanel(QWidget):
    """Right-side panel showing formatted model details."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setStyleSheet("background-color: #0e1519; border-bottom: 1px solid #1e2533;")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 16, 16, 14)

        self.model_name_lbl = QLabel("Select a model")
        self.model_name_lbl.setObjectName("label_detail_heading")
        self.model_name_lbl.setWordWrap(True)

        self.model_meta_lbl = QLabel("")
        self.model_meta_lbl.setObjectName("label_muted")

        header_layout.addWidget(self.model_name_lbl)
        header_layout.addWidget(self.model_meta_lbl)
        layout.addWidget(header)

        # Stats row
        self.stats_widget = QWidget()
        self.stats_widget.setStyleSheet("background-color: #0a0f18; border-bottom: 1px solid #1e2533;")
        stats_layout = QHBoxLayout(self.stats_widget)
        stats_layout.setContentsMargins(16, 10, 16, 10)
        stats_layout.setSpacing(12)

        self.stat_size = StatCard("Size")
        self.stat_format = StatCard("Format")
        self.stat_family = StatCard("Family")
        self.stat_params = StatCard("Params")

        stats_layout.addWidget(self.stat_size)
        stats_layout.addWidget(self.stat_format)
        stats_layout.addWidget(self.stat_family)
        stats_layout.addWidget(self.stat_params)
        stats_layout.addStretch(1)

        layout.addWidget(self.stats_widget)

        # Raw JSON
        raw_label = QWidget()
        raw_label.setStyleSheet("background-color: #0e1117; padding: 8px 16px 4px 16px;")
        raw_l = QHBoxLayout(raw_label)
        raw_l.setContentsMargins(0, 0, 0, 0)
        lbl = SectionLabel("Raw Detail")
        raw_l.addWidget(lbl)
        raw_l.addStretch(1)
        layout.addWidget(raw_label)

        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setPlaceholderText("Select a model to view details…")
        self.detail_text.setStyleSheet(
            "QTextEdit { border: none; border-radius: 0; background-color: #080c13; "
            "color: #4b6a9e; font-size: 12px; padding: 12px 16px; }"
        )
        layout.addWidget(self.detail_text)

    def show_loading(self, name: str):
        self.model_name_lbl.setText(name)
        self.model_meta_lbl.setText("Loading…")
        self.stat_size.set_value("—")
        self.stat_format.set_value("—")
        self.stat_family.set_value("—")
        self.stat_params.set_value("—")
        self.detail_text.setPlainText("Fetching model details…")

    def show_data(self, row, detail: dict | None):
        self.model_name_lbl.setText(row.name)
        self.model_meta_lbl.setText(f"Modified {parse_time(row.modified_at)}")
        self.stat_size.set_value(human_bytes(row.size))

        if detail:
            details = detail.get("details", {})
            self.stat_format.set_value(details.get("format", "—"))
            self.stat_family.set_value(details.get("family", "—"))
            params = details.get("parameter_size", "—")
            self.stat_params.set_value(params if params else "—")
            self.detail_text.setPlainText(json.dumps(detail, indent=2, ensure_ascii=False))
        else:
            self.stat_format.set_value("—")
            self.stat_family.set_value("—")
            self.stat_params.set_value("—")
            self.detail_text.setPlainText(json.dumps(row.raw, indent=2, ensure_ascii=False))

    def show_multi(self, count: int):
        self.model_name_lbl.setText(f"{count} models selected")
        self.model_meta_lbl.setText("Select a single model to view details")
        self.stat_size.set_value("—")
        self.stat_format.set_value("—")
        self.stat_family.set_value("—")
        self.stat_params.set_value("—")
        self.detail_text.setPlainText("")

    def clear(self):
        self.model_name_lbl.setText("Select a model")
        self.model_meta_lbl.setText("")
        self.stat_size.set_value("—")
        self.stat_format.set_value("—")
        self.stat_family.set_value("—")
        self.stat_params.set_value("—")
        self.detail_text.setPlainText("")




# ─────────────────────────────────────────────
#  GAUGE WIDGETS
# ─────────────────────────────────────────────

class GaugeBar(QWidget):
    def __init__(self, label: str, color: str = "#3b82f6", parent=None):
        super().__init__(parent)
        self._pct = 0.0
        self._color = color
        self._label = label
        self.setFixedHeight(34)

    def set_value(self, pct: float):
        self._pct = max(0.0, min(100.0, pct))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QColor("#4b5563"))
        font = p.font(); font.setPointSize(9); font.setBold(True); p.setFont(font)
        p.drawText(0, 0, 78, h, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._label.upper())
        tx, ty, tw = 84, h // 2 - 4, w - 84 - 52
        p.setBrush(QColor("#1a2235")); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(tx, ty, tw, 8, 4, 4)
        fw = int(tw * self._pct / 100)
        if fw > 0:
            p.setBrush(QColor(self._color)); p.drawRoundedRect(tx, ty, fw, 8, 4, 4)
        p.setPen(QColor("#e2e8f0"))
        font.setPointSize(10); p.setFont(font)
        p.drawText(w - 50, 0, 50, h,
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   f"{self._pct:.0f}%")
        p.end()


class GaugeCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("gauge_card")
        self.setMinimumHeight(115)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(5)
        hdr = QHBoxLayout()
        self._title_lbl = QLabel(title.upper())
        self._title_lbl.setStyleSheet(
            "color:#3b82f6; font-size:11px; font-weight:700; letter-spacing:0.8px;")
        self._value_lbl = QLabel("—")
        self._value_lbl.setStyleSheet("color:#e2e8f0; font-size:17px; font-weight:700;")
        hdr.addWidget(self._title_lbl); hdr.addStretch(1); hdr.addWidget(self._value_lbl)
        layout.addLayout(hdr)
        self._bars: list = []
        self._bar_layout = QVBoxLayout(); self._bar_layout.setSpacing(2)
        layout.addLayout(self._bar_layout)

    def set_value(self, text: str): self._value_lbl.setText(text)

    def add_bar(self, label: str, color: str = "#3b82f6") -> "GaugeBar":
        bar = GaugeBar(label, color)
        self._bar_layout.addWidget(bar); self._bars.append(bar); return bar

    def update_bar(self, idx: int, pct: float):
        if 0 <= idx < len(self._bars): self._bars[idx].set_value(pct)


# ─────────────────────────────────────────────
#  SPARKLINE CHART
# ─────────────────────────────────────────────

class MonitorSparkWidget(QWidget):
    COLORS = {"cpu": "#3b82f6", "ram": "#8b5cf6", "vram": "#10b981"}
    LABELS = {"cpu": "CPU", "ram": "RAM", "vram": "VRAM"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict = {"cpu": [], "ram": [], "vram": []}

    def update_data(self, cpu: list, ram: list, vram: list):
        self._data = {"cpu": list(cpu), "ram": list(ram), "vram": list(vram)}
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pl, pr, pt, pb = 46, 12, 8, 22
        cw, ch = w - pl - pr, h - pt - pb
        p.fillRect(0, 0, w, h, QColor("#080c13"))
        for pct in (25, 50, 75, 100):
            y = pt + ch - int(ch * pct / 100)
            p.setPen(QPen(QColor("#1e2533"), 1, Qt.PenStyle.DotLine))
            p.drawLine(pl, y, w - pr, y)
            p.setPen(QColor("#374151"))
            font = p.font(); font.setPointSize(8); p.setFont(font)
            p.drawText(2, y - 6, 40, 14,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{pct}%")
        for key in ("cpu", "ram", "vram"):
            series = self._data[key]
            if len(series) < 2:
                continue
            n = len(series)
            color = QColor(self.COLORS[key])
            p.setPen(QPen(color, 1.5))
            pts = [(pl + int(i / (n - 1) * cw), pt + ch - int(ch * v / 100))
                   for i, v in enumerate(series)]
            for i in range(len(pts) - 1):
                p.drawLine(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1])
            if pts:
                lx, ly = pts[-1]
                p.setBrush(color); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(lx - 3, ly - 3, 6, 6)
                p.setPen(color)
                font = p.font(); font.setPointSize(8); p.setFont(font)
                p.drawText(lx + 6, ly - 8, 60, 16,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           f"{self.LABELS[key]} {series[-1]:.0f}%")
        p.setPen(QColor("#1e2533"))
        p.drawLine(pl, pt + ch, w - pr, pt + ch)
        p.end()


# ─────────────────────────────────────────────
#  MODELFILE SYNTAX HIGHLIGHTER
# ─────────────────────────────────────────────

from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat

class ModelfileHighlighter(QSyntaxHighlighter):
    KEYWORDS = ["FROM","SYSTEM","PARAMETER","TEMPLATE","ADAPTER","LICENSE","MESSAGE"]
    PARAMS   = ["temperature","top_p","top_k","num_ctx","num_predict","repeat_penalty",
                "repeat_last_n","seed","stop","tfs_z","num_thread","num_gpu",
                "mirostat","mirostat_tau","mirostat_eta"]

    def __init__(self, document):
        super().__init__(document)
        self._rules = []
        kw = QTextCharFormat(); kw.setForeground(QColor("#60a5fa"))
        kw.setFontWeight(QFont.Weight.Bold)
        for k in self.KEYWORDS:
            self._rules.append((re.compile(rf"^{k}\b", re.MULTILINE), kw))
        pm = QTextCharFormat(); pm.setForeground(QColor("#a78bfa"))
        for pp in self.PARAMS:
            self._rules.append((re.compile(rf"\b{pp}\b", re.IGNORECASE), pm))
        sm = QTextCharFormat(); sm.setForeground(QColor("#6ee7b7"))
        self._rules.append((re.compile(r'"[^"]*"'), sm))
        cm = QTextCharFormat(); cm.setForeground(QColor("#374151"))
        self._rules.append((re.compile(r"#[^\n]*"), cm))
        nm = QTextCharFormat(); nm.setForeground(QColor("#fbbf24"))
        self._rules.append((re.compile(r"\b\d+\.?\d*\b"), nm))

    def highlightBlock(self, text):
        for pat, fmt in self._rules:
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


# ─────────────────────────────────────────────
#  REGISTRY WIDGETS
# ─────────────────────────────────────────────

class RegistryModelCard(QFrame):
    """A clickable card representing one model from the registry."""
    pull_requested = pyqtSignal(str)
    tags_requested = pyqtSignal(str)

    def __init__(self, info: dict, installed_names: set, parent=None):
        super().__init__(parent)
        self.setObjectName("registry_card")
        self.info = info
        self.slug = info["slug"]
        self._setup_ui(installed_names)

    def _setup_ui(self, installed_names: set):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # Header row
        top = QHBoxLayout()
        name_lbl = QLabel(self.info.get("name") or self.slug)
        name_lbl.setStyleSheet("color: #e2e8f0; font-size: 14px; font-weight: 700;")
        top.addWidget(name_lbl)
        top.addStretch(1)

        # Installed badge
        is_installed = any(
            n == self.slug or n.startswith(self.slug + ":")
            for n in installed_names
        )
        if is_installed:
            ib = QLabel("✓ installed")
            ib.setObjectName("tag_badge_installed")
            top.addWidget(ib)
        layout.addLayout(top)

        # Description
        desc = (self.info.get("description") or "").strip()
        if desc:
            dlbl = QLabel(desc[:120] + ("…" if len(desc) > 120 else ""))
            dlbl.setWordWrap(True)
            dlbl.setStyleSheet("color: #64748b; font-size: 12px;")
            layout.addWidget(dlbl)

        # Meta row
        meta = QHBoxLayout()
        meta.setSpacing(8)
        if self.info.get("pulls"):
            pl = QLabel(f"↓ {self.info['pulls']}")
            pl.setStyleSheet("color: #4b5563; font-size: 11px;")
            meta.addWidget(pl)
        if self.info.get("tags"):
            tl = QLabel(f"◈ {self.info['tags']} tags")
            tl.setStyleSheet("color: #4b5563; font-size: 11px;")
            meta.addWidget(tl)
        if self.info.get("updated"):
            ul = QLabel(self.info["updated"])
            ul.setStyleSheet("color: #374151; font-size: 11px;")
            meta.addWidget(ul)
        meta.addStretch(1)
        layout.addLayout(meta)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        view_btn = QPushButton("View Tags")
        view_btn.setFixedHeight(28)
        view_btn.setStyleSheet("font-size: 12px; padding: 0 10px;")
        view_btn.clicked.connect(lambda: self.tags_requested.emit(self.slug))

        pull_btn = QPushButton("Pull")
        pull_btn.setObjectName("btn_primary")
        pull_btn.setFixedHeight(28)
        pull_btn.setFixedWidth(70)
        pull_btn.setStyleSheet("font-size: 12px;")
        pull_btn.clicked.connect(lambda: self.pull_requested.emit(self.slug))

        btn_row.addWidget(view_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(pull_btn)
        layout.addLayout(btn_row)


class TagsDialog(QDialog):
    """Shows available tags for a registry model with sizes."""
    pull_tag_requested = pyqtSignal(str)

    def __init__(self, slug: str, tags: list[dict], installed_names: set, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{slug} — Available Tags")
        self.setModal(True)
        self.resize(520, 480)
        self.setStyleSheet("QDialog { background-color: #111827; border: 1px solid #1e2533; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        title = QLabel(f"Available tags for  {slug}")
        title.setStyleSheet("color: #e2e8f0; font-size: 15px; font-weight: 700;")
        layout.addWidget(title)

        sub = QLabel("Select a tag to pull directly into Ollama.")
        sub.setStyleSheet("color: #4b5563; font-size: 12px;")
        layout.addWidget(sub)

        layout.addWidget(Separator())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 4, 0, 4)
        inner_layout.setSpacing(6)

        if not tags:
            no_tags = QLabel("No tag data available — check your internet connection.")
            no_tags.setStyleSheet("color: #4b5563; font-size: 13px;")
            inner_layout.addWidget(no_tags)
        else:
            for t in tags:
                tag_name = t.get("tag", "")
                tag_size = t.get("size", "")
                full_name = f"{slug}:{tag_name}"
                is_installed = full_name in installed_names or (
                    tag_name == "latest" and slug in installed_names
                )

                row = QFrame()
                row.setObjectName("registry_card")
                row.setFixedHeight(44)
                rl = QHBoxLayout(row)
                rl.setContentsMargins(12, 0, 12, 0)

                tl = QLabel(tag_name)
                tl.setStyleSheet("color: #e2e8f0; font-size: 13px; font-weight: 600;")
                rl.addWidget(tl)

                if tag_size:
                    sl = QLabel(tag_size)
                    sl.setStyleSheet("color: #64748b; font-size: 12px;")
                    rl.addWidget(sl)
                rl.addStretch(1)

                if is_installed:
                    il = QLabel("✓ installed")
                    il.setObjectName("tag_badge_installed")
                    rl.addWidget(il)

                pb = QPushButton("Pull")
                pb.setObjectName("btn_primary")
                pb.setFixedSize(60, 26)
                pb.setStyleSheet("font-size: 11px;")
                pb.clicked.connect(lambda _, fn=full_name: (
                    self.pull_tag_requested.emit(fn), self.accept()
                ))
                rl.addWidget(pb)
                inner_layout.addWidget(row)

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("btn_cancel")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)


# ─────────────────────────────────────────────
#  BENCHMARK WIDGETS
# ─────────────────────────────────────────────

class BenchResultCard(QFrame):
    """Displays a single model's benchmark result."""

    def __init__(self, res: BenchResult, is_winner: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("bench_result_winner" if is_winner else "bench_result")
        self._build(res, is_winner)

    def _build(self, res: BenchResult, is_winner: bool):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        name = QLabel(res.model)
        name.setStyleSheet("color: #e2e8f0; font-size: 14px; font-weight: 700;")
        hdr.addWidget(name)
        hdr.addStretch(1)
        if is_winner:
            w_lbl = QLabel("⚡ fastest")
            w_lbl.setObjectName("tag_badge_installed")
            hdr.addWidget(w_lbl)
        if res.status == "error":
            e_lbl = QLabel("✕ error")
            e_lbl.setStyleSheet(
                "background:#1c0909; border:1px solid #7f1d1d; border-radius:4px;"
                "color:#f87171; font-size:11px; font-weight:600; padding:2px 7px;"
            )
            hdr.addWidget(e_lbl)
        layout.addLayout(hdr)

        if res.status == "error":
            el = QLabel(res.error[:200])
            el.setWordWrap(True)
            el.setStyleSheet("color: #f87171; font-size: 12px;")
            layout.addWidget(el)
            return

        # Metrics grid
        mg = QHBoxLayout()
        mg.setSpacing(0)
        metrics = [
            ("Tokens/s", f"{res.tokens_per_sec:.1f}" if res.tokens_per_sec else "—"),
            ("First token", f"{res.first_token_ms:.0f} ms" if res.first_token_ms else "—"),
            ("Total time", f"{res.total_ms/1000:.2f} s" if res.total_ms else "—"),
            ("Eval tokens", str(res.eval_tokens) if res.eval_tokens else "—"),
        ]
        for label, value in metrics:
            mc = QWidget()
            mcl = QVBoxLayout(mc)
            mcl.setContentsMargins(0, 0, 16, 0)
            mcl.setSpacing(2)
            vl = QLabel(value)
            vl.setStyleSheet(
                "color: #4ade80; font-size: 18px; font-weight: 700;" if is_winner
                else "color: #60a5fa; font-size: 18px; font-weight: 700;"
            )
            ll = QLabel(label.upper())
            ll.setStyleSheet("color: #374151; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;")
            mcl.addWidget(vl)
            mcl.addWidget(ll)
            mg.addWidget(mc)
        mg.addStretch(1)
        layout.addLayout(mg)

        # Output preview
        if res.output:
            preview = res.output[:300].replace("\n", " ")
            if len(res.output) > 300:
                preview += "…"
            ol = QLabel(preview)
            ol.setWordWrap(True)
            ol.setStyleSheet(
                "color: #4b5563; font-size: 11px; font-family: 'Cascadia Code', 'Consolas', monospace;"
                "background:#080c13; border-radius:4px; padding: 6px 8px;"
            )
            layout.addWidget(ol)


# ─────────────────────────────────────────────
#  DIALOGS
# ─────────────────────────────────────────────

class ConfirmDeleteDialog(QDialog):
    def __init__(self, model_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Confirm Deletion")
        self.model_name = model_name
        self.setModal(True)
        self.resize(520, 200)
        self.setStyleSheet("QDialog { background-color: #111827; border: 1px solid #1e2533; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        icon_row = QHBoxLayout()
        icon_lbl = QLabel("⚠")
        icon_lbl.setStyleSheet("color: #f59e0b; font-size: 28px;")
        title_lbl = QLabel("Delete Model")
        title_lbl.setStyleSheet("color: #f1f5f9; font-size: 17px; font-weight: 700;")
        icon_row.addWidget(icon_lbl)
        icon_row.addWidget(title_lbl)
        icon_row.addStretch(1)
        layout.addLayout(icon_row)

        desc = QLabel(
            f"This will permanently remove <b style='color:#f87171'>{model_name}</b> "
            "from your local Ollama store.<br>"
            "To confirm, type the exact model name:"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #94a3b8; font-size: 13px;")
        layout.addWidget(desc)

        self.input = QLineEdit()
        self.input.setPlaceholderText(model_name)
        layout.addWidget(self.input)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("btn_cancel")
        self.ok_btn = QPushButton("Delete Model")
        self.ok_btn.setObjectName("btn_danger")
        self.ok_btn.setEnabled(False)
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.ok_btn)
        layout.addLayout(btns)

        self.input.textChanged.connect(self._on_change)
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self.accept)

    def _on_change(self, text: str):
        self.ok_btn.setEnabled(text.strip() == self.model_name)


def prompt_text(parent, title: str, label: str, placeholder: str):
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.resize(460, 170)
    dlg.setStyleSheet("QDialog { background-color: #111827; border: 1px solid #1e2533; }")

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(24, 24, 24, 20)
    layout.setSpacing(14)

    lab = QLabel(label)
    lab.setWordWrap(True)
    lab.setStyleSheet("color: #94a3b8; font-size: 13px;")
    layout.addWidget(lab)

    entry = QLineEdit()
    entry.setPlaceholderText(placeholder)
    layout.addWidget(entry)

    btns = QHBoxLayout()
    btns.addStretch(1)
    btn_cancel = QPushButton("Cancel")
    btn_cancel.setObjectName("btn_cancel")
    btn_ok = QPushButton("Confirm")
    btn_ok.setObjectName("btn_primary")
    btns.addWidget(btn_cancel)
    btns.addWidget(btn_ok)
    layout.addLayout(btns)

    btn_cancel.clicked.connect(dlg.reject)
    btn_ok.clicked.connect(dlg.accept)

    code = dlg.exec()
    return entry.text(), (code == QDialog.DialogCode.Accepted)


# ─────────────────────────────────────────────
#  MAIN WINDOW
# ─────────────────────────────────────────────

