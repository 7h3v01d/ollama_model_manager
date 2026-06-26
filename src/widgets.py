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

from utils import human_bytes, parse_time, DEFAULT_BASE_URL, ServerRegistry
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



class MultiServerBar(QWidget):
    """
    Header bar showing the active server with a dropdown of all saved
    servers.  Emits server_changed(url) when the user switches.
    """
    server_changed = pyqtSignal(str)   # emits the new active URL

    def __init__(self, registry, parent=None):
        super().__init__(parent)
        self._registry = registry

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        icon = QLabel("⬡")
        icon.setStyleSheet("color:#3b82f6; font-size:20px;")
        layout.addWidget(icon)

        srv_label = QLabel("SERVER")
        srv_label.setObjectName("label_section")
        layout.addWidget(srv_label)

        # Server selector dropdown
        self.server_combo = QComboBox()
        self.server_combo.setMinimumWidth(200)
        self.server_combo.setMaximumWidth(280)
        self._rebuild_combo()
        self.server_combo.currentIndexChanged.connect(self._on_combo_changed)
        layout.addWidget(self.server_combo)

        # Add server button
        self.add_btn = QPushButton("+")
        self.add_btn.setFixedWidth(30)
        self.add_btn.setFixedHeight(28)
        self.add_btn.setToolTip("Add server")
        self.add_btn.clicked.connect(self._on_add)
        layout.addWidget(self.add_btn)

        # Remove server button
        self.remove_btn = QPushButton("−")
        self.remove_btn.setFixedWidth(30)
        self.remove_btn.setFixedHeight(28)
        self.remove_btn.setToolTip("Remove selected server")
        self.remove_btn.clicked.connect(self._on_remove)
        layout.addWidget(self.remove_btn)

        layout.addWidget(Separator(vertical=True))

        # Status badge
        self.badge = QLabel("● OFFLINE")
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

    # ── Combo management ──────────────────────────────────────────────

    def _rebuild_combo(self):
        self.server_combo.blockSignals(True)
        self.server_combo.clear()
        active_url = self._registry.active().url
        for i, entry in enumerate(self._registry.entries()):
            self.server_combo.addItem(f"{entry.name}  —  {entry.url}", userData=entry.url)
            if entry.url.rstrip("/") == active_url.rstrip("/"):
                self.server_combo.setCurrentIndex(i)
        self.server_combo.blockSignals(False)

    def active_url(self) -> str:
        url = self.server_combo.currentData()
        return url or self._registry.active().url

    def set_connected(self, connected: bool):
        entry = self._registry.active()
        label = entry.name
        if connected:
            self.badge.setText(f"● {label.upper()}  CONNECTED")
            self.badge.setObjectName("conn_badge_connected")
        else:
            self.badge.setText(f"● {label.upper()}  OFFLINE")
            self.badge.setObjectName("conn_badge_disconnected")
        self.badge.setStyle(self.badge.style())

    def refresh(self):
        """Rebuild combo from registry (call after add/remove/rename)."""
        self._rebuild_combo()
        self.set_connected(False)

    # ── Slots ─────────────────────────────────────────────────────────

    def _on_combo_changed(self, index: int):
        if index < 0:
            return
        url = self.server_combo.itemData(index)
        if url:
            self._registry.set_active(url)
            self.server_changed.emit(url)

    def _on_add(self):
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout
        dlg = QDialog()
        dlg.setWindowTitle("Add Server")
        dlg.setModal(True)
        dlg.resize(400, 160)
        dlg.setStyleSheet("QDialog{background:#111827;border:1px solid #1e2533;}")
        fl = QFormLayout(dlg)
        fl.setContentsMargins(20, 20, 20, 16)
        fl.setSpacing(12)
        name_input = QLineEdit("Remote")
        url_input  = QLineEdit("http://")
        url_input.setPlaceholderText("http://192.168.1.x:11434")
        notes_input = QLineEdit()
        notes_input.setPlaceholderText("Optional notes")
        fl.addRow("Name:",  name_input)
        fl.addRow("URL:",   url_input)
        fl.addRow("Notes:", notes_input)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        fl.addRow(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = name_input.text().strip() or "Server"
            url  = url_input.text().strip()
            if url and url != "http://":
                self._registry.add(name, url, notes_input.text().strip())
                self._registry.set_active(url)
                self._rebuild_combo()
                self.server_changed.emit(url)

    def _on_remove(self):
        url = self.active_url()
        if url == DEFAULT_BASE_URL and len(self._registry.entries()) == 1:
            return   # don't remove the last server
        self._registry.remove(url)
        self._rebuild_combo()
        new_url = self._registry.active().url
        self.server_changed.emit(new_url)


# Keep ConnectionBar as an alias so any code still referencing it won't break
ConnectionBar = MultiServerBar


class DetailPanel(QWidget):
    """
    Right-side panel — full LLM Profile view.

    Shows: capability tags, stat cards, usage quick-ref,
    system prompt preview, template preview, and raw JSON tab.
    """

    # ── Tag definitions ────────────────────────────────────────────────────
    # (label, bg, border/fg, tag_id)
    _T_VISION  = ("👁  Vision",        "#0a1a2e", "#3b82f6", "vision")
    _T_MMEDIA  = ("🖼  Multimodal",    "#150d2e", "#8b5cf6", "multimodal")
    _T_TOOLS   = ("🔧  Tools",         "#0a1f10", "#22c55e", "tools")
    _T_THINK   = ("🤔  Thinking",      "#160a2e", "#a855f7", "thinking")
    _T_CODE    = ("💻  Code",          "#0a1a0a", "#4ade80", "code")
    _T_MATH    = ("🧮  Math",          "#1f1000", "#f97316", "math")
    _T_EMBED   = ("🔗  Embed",         "#111111", "#6b7280", "embed")
    _T_MULTI   = ("🌐  Multilingual",  "#001f1f", "#06b6d4", "multilingual")
    _T_TEXT    = ("📝  Text Only",     "#0e1117", "#374151", "text")

    # keyword → tag  (checked in order; first hit per tag_id wins)
    _CAP_RULES: list = [
        # Vision
        ("llava-phi",          _T_VISION),
        ("llava-llama",        _T_VISION),
        ("llava",              _T_VISION),
        ("bakllava",           _T_VISION),
        ("moondream",          _T_VISION),
        ("minicpm-v",          _T_VISION),
        ("qwen2.5-vl",         _T_VISION),
        ("qwen2-vl",           _T_VISION),
        ("qwen-vl",            _T_VISION),
        ("internvl",           _T_VISION),
        ("phi-3-vision",       _T_VISION),
        ("phi3-vision",        _T_VISION),
        ("phi3.5-vision",      _T_VISION),
        ("pixtral",            _T_VISION),
        ("llama3.2-vision",    _T_VISION),
        ("llama-3.2-vision",   _T_VISION),
        ("granite3-vision",    _T_VISION),
        ("cogvlm",             _T_VISION),
        ("idefics",            _T_VISION),
        ("vision",             _T_VISION),
        # Multimodal
        ("minicpm-o",          _T_MMEDIA),
        ("multimodal",         _T_MMEDIA),
        # Tools
        ("functionary",        _T_TOOLS),
        ("mistral-nemo",       _T_TOOLS),
        ("command-r",          _T_TOOLS),
        ("hermes",             _T_TOOLS),
        ("nexusraven",         _T_TOOLS),
        ("gorilla",            _T_TOOLS),
        ("toolbench",          _T_TOOLS),
        ("xlam",               _T_TOOLS),
        ("hammer",             _T_TOOLS),
        ("firefunction",       _T_TOOLS),
        ("granite3-dense",     _T_TOOLS),
        ("granite3-moe",       _T_TOOLS),
        ("smollm",             _T_TOOLS),
        # Thinking / CoT
        ("deepseek-r1",        _T_THINK),
        ("deepseek-r2",        _T_THINK),
        ("qwq",                _T_THINK),
        ("sky-t1",             _T_THINK),
        ("marco-o1",           _T_THINK),
        ("thinker",            _T_THINK),
        ("thinking",           _T_THINK),
        # Code
        ("codellama",          _T_CODE),
        ("deepseek-coder",     _T_CODE),
        ("starcoder2",         _T_CODE),
        ("starcoder",          _T_CODE),
        ("codegemma",          _T_CODE),
        ("qwen2.5-coder",      _T_CODE),
        ("qwen-coder",         _T_CODE),
        ("codeqwen",           _T_CODE),
        ("wizard-coder",       _T_CODE),
        ("phind-codellama",    _T_CODE),
        ("granite-code",       _T_CODE),
        ("opencoder",          _T_CODE),
        ("coder",              _T_CODE),
        ("code",               _T_CODE),
        # Math
        ("mathstral",          _T_MATH),
        ("metamath",           _T_MATH),
        ("deepseek-math",      _T_MATH),
        ("math",               _T_MATH),
        # Embed
        ("nomic-embed",        _T_EMBED),
        ("mxbai-embed",        _T_EMBED),
        ("all-minilm",         _T_EMBED),
        ("bge-m3",             _T_EMBED),
        ("bge-large",          _T_EMBED),
        ("snowflake-arctic-embed", _T_EMBED),
        ("embed",              _T_EMBED),
        # Multilingual
        ("aya",                _T_MULTI),
        ("salamandra",         _T_MULTI),
        ("qwen",               _T_MULTI),
        ("multilingual",       _T_MULTI),
    ]

    # tag_id → (one-liner, usage note)
    _TAG_INFO: dict = {
        "vision":      ("Accepts image inputs",
                        "Pass base64 images in /api/chat messages."),
        "multimodal":  ("Handles images + audio/video",
                        "Check model card for supported input types."),
        "tools":       ("Function / tool calling",
                        "Pass tools=[] and tool_choice in /api/chat payload."),
        "thinking":    ("Extended chain-of-thought",
                        "Uses <think> tokens internally — longer TTFT, stronger reasoning."),
        "code":        ("Code generation & completion",
                        "Best with low temperature (0.0–0.2) and high context."),
        "math":        ("Mathematical reasoning",
                        "Suited for step-by-step proofs and numeric problems."),
        "embed":       ("Embedding model",
                        "Use /api/embeddings — NOT /api/chat. Returns float vectors."),
        "multilingual":("Strong non-English coverage",
                        "Set language in system prompt for best results."),
        "text":        ("General purpose text model",
                        "Text-in / text-out. No image, audio, or tool-call support."),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(320)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Name / meta header ──────────────────────────────────────────
        hdr = QWidget()
        hdr.setStyleSheet("background:#080c13; border-bottom:1px solid #1e2533;")
        hdr_l = QVBoxLayout(hdr)
        hdr_l.setContentsMargins(16, 14, 16, 12)
        hdr_l.setSpacing(4)

        self.model_name_lbl = QLabel("Select a model")
        self.model_name_lbl.setObjectName("label_detail_heading")
        self.model_name_lbl.setWordWrap(True)
        hdr_l.addWidget(self.model_name_lbl)

        self.model_meta_lbl = QLabel("")
        self.model_meta_lbl.setObjectName("label_muted")
        hdr_l.addWidget(self.model_meta_lbl)

        # Tag badges row
        self._caps_row = QHBoxLayout()
        self._caps_row.setSpacing(6)
        self._caps_row.setContentsMargins(0, 6, 0, 0)
        self._cap_badges: list = []
        hdr_l.addLayout(self._caps_row)
        root.addWidget(hdr)

        # ── Stat cards ──────────────────────────────────────────────────
        stats_w = QWidget()
        stats_w.setStyleSheet("background:#0a0f18; border-bottom:1px solid #1e2533;")
        sl = QHBoxLayout(stats_w)
        sl.setContentsMargins(16, 10, 16, 10)
        sl.setSpacing(10)
        self.stat_size   = StatCard("Size")
        self.stat_params = StatCard("Params")
        self.stat_ctx    = StatCard("Context")
        self.stat_quant  = StatCard("Quant")
        self.stat_family = StatCard("Family")
        for c in (self.stat_size, self.stat_params, self.stat_ctx,
                  self.stat_quant, self.stat_family):
            sl.addWidget(c)
        sl.addStretch(1)
        root.addWidget(stats_w)

        # ── Scrollable profile body ─────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background:#0a0f18; border:none; }")

        body = QWidget()
        body.setStyleSheet("background:#0a0f18;")
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(16, 14, 16, 16)
        self._body_layout.setSpacing(14)
        self._body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    # ── Internal builders ──────────────────────────────────────────────────

    def _clear_body(self):
        while self._body_layout.count() > 1:
            item = self._body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _section(self, title: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet(
            "background:#0e1117; border:1px solid #1e2533; border-radius:8px;")
        return w

    def _add_section(self, title: str) -> tuple:
        """Add a titled card to the body. Returns (card_widget, inner_layout)."""
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background:#0e1117; border:1px solid #1e2533; border-radius:8px; }")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 12)
        cl.setSpacing(8)

        title_lbl = QLabel(title.upper())
        title_lbl.setStyleSheet(
            "color:#374151; font-size:10px; font-weight:700; letter-spacing:0.8px;"
            "border:none; background:transparent;")
        cl.addWidget(title_lbl)

        self._body_layout.insertWidget(self._body_layout.count() - 1, card)
        return card, cl

    def _kv_row(self, parent_layout, key: str, value: str, val_style: str = ""):
        row = QHBoxLayout()
        row.setSpacing(8)
        k = QLabel(key)
        k.setStyleSheet(
            "color:#4b5563; font-size:11px; font-weight:600; "
            "min-width:110px; max-width:110px; background:transparent; border:none;")
        v = QLabel(value)
        v.setWordWrap(True)
        base = "color:#94a3b8; font-size:11px; background:transparent; border:none;"
        v.setStyleSheet(base + val_style)
        row.addWidget(k)
        row.addWidget(v, 1)
        parent_layout.addLayout(row)

    def _code_block(self, parent_layout, text: str, max_lines: int = 8):
        preview = "\n".join(text.strip().splitlines()[:max_lines])
        if len(text.strip().splitlines()) > max_lines:
            preview += "\n…"
        lbl = QLabel(preview)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            "color:#60a5fa; font-size:11px; "
            "font-family:'Cascadia Code','Consolas',monospace; "
            "background:#080c13; border:1px solid #1e2533; border-radius:4px; "
            "padding:8px; line-height:160%;")
        parent_layout.addWidget(lbl)

    # ── Capability inference ───────────────────────────────────────────────

    def _infer_tags(self, model_name: str, details: dict) -> list:
        """Return list of (label, bg, fg, tag_id) for matched tags."""
        families = details.get("families") or []
        fam_str = " ".join(families) if isinstance(families, list) else str(families)
        key = (model_name + " " + details.get("family", "") + " " + fam_str).lower()

        seen: set = set()
        tags: list = []
        for keyword, tag in self._CAP_RULES:
            label, bg, fg, tag_id = tag
            if tag_id not in seen and keyword in key:
                tags.append(tag)
                seen.add(tag_id)

        # Text Only fallback
        if not (seen - {"embed", "multilingual"}):
            tags.append(self._T_TEXT)

        return tags

    def _render_tags(self, tags: list):
        """Rebuild badge row from tag list."""
        for b in self._cap_badges:
            b.setParent(None)
            b.deleteLater()
        self._cap_badges.clear()
        while self._caps_row.count():
            item = self._caps_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for label, bg, fg, tag_id in tags:
            badge = QLabel(label)
            badge.setStyleSheet(
                f"background:{bg}; border:1px solid {fg}; border-radius:4px; "
                f"color:{fg}; font-size:10px; font-weight:700; padding:2px 8px;")
            self._caps_row.addWidget(badge)
            self._cap_badges.append(badge)
        self._caps_row.addStretch(1)

    # ── Profile builders ───────────────────────────────────────────────────

    def _build_capabilities_section(self, tags: list):
        if not tags:
            return
        _, cl = self._add_section("Capabilities")
        for label, bg, fg, tag_id in tags:
            info = self._TAG_INFO.get(tag_id, ("", ""))
            row = QHBoxLayout()
            row.setSpacing(10)

            badge = QLabel(label)
            badge.setFixedWidth(130)
            badge.setStyleSheet(
                f"background:{bg}; border:1px solid {fg}; border-radius:4px; "
                f"color:{fg}; font-size:10px; font-weight:700; padding:3px 8px;")
            row.addWidget(badge)

            desc_col = QVBoxLayout()
            desc_col.setSpacing(2)
            if info[0]:
                one_liner = QLabel(info[0])
                one_liner.setStyleSheet(
                    "color:#cbd5e1; font-size:11px; font-weight:600; "
                    "background:transparent; border:none;")
                desc_col.addWidget(one_liner)
            if info[1]:
                usage = QLabel(info[1])
                usage.setWordWrap(True)
                usage.setStyleSheet(
                    "color:#4b5563; font-size:10px; "
                    "background:transparent; border:none;")
                desc_col.addWidget(usage)
            row.addLayout(desc_col, 1)
            cl.addLayout(row)

    def _build_usage_section(self, model_name: str, details: dict,
                             model_info: dict, parameters: str, tags: list):
        tag_ids = {t[3] for t in tags}
        _, cl = self._add_section("Quick Usage")

        # API endpoint recommendation
        if "embed" in tag_ids:
            endpoint = "POST /api/embeddings"
            ep_color = "color:#a855f7;"
        else:
            endpoint = "POST /api/chat  or  POST /api/generate"
            ep_color = "color:#22c55e;"
        self._kv_row(cl, "Endpoint", endpoint, ep_color)

        # Recommended temperature
        if "thinking" in tag_ids:
            temp = "0.6 – 0.8  (model manages internal temp)"
        elif "code" in tag_ids or "math" in tag_ids:
            temp = "0.0 – 0.2  (low for determinism)"
        elif "embed" in tag_ids:
            temp = "N/A  (embeddings ignore temperature)"
        else:
            temp = "0.7  (default)"
        self._kv_row(cl, "Temperature", temp)

        # Context window
        ctx = None
        for k in ("llama.context_length", "context_length",
                  "qwen2.context_length", "gemma.context_length",
                  "phi3.context_length", "mistral.context_length"):
            if k in model_info:
                ctx = model_info[k]
                break
        if ctx is None and parameters:
            for line in parameters.splitlines():
                if "num_ctx" in line:
                    for p in line.split():
                        if p.isdigit():
                            ctx = int(p)
                            break
        if ctx:
            ctx_k = int(ctx) // 1000
            self._kv_row(cl, "Max context",
                         f"{int(ctx):,} tokens  ({ctx_k}k)",
                         "color:#60a5fa;")

        # Tool call note
        if "tools" in tag_ids:
            self._kv_row(cl, "Tool calling",
                         "Supported — pass tools=[] array in /api/chat",
                         "color:#22c55e;")

        # Vision note
        if "vision" in tag_ids or "multimodal" in tag_ids:
            self._kv_row(cl, "Image input",
                         'Pass {"role":"user","content":[{"type":"image_url",...}]}',
                         "color:#3b82f6;")

        # Thinking note
        if "thinking" in tag_ids:
            self._kv_row(cl, "Thinking mode",
                         "Model emits <think>…</think> before final answer",
                         "color:#a855f7;")

        # Ollama API example
        self._kv_row(cl, "Ollama model ID", model_name, "font-family:monospace;")

    def _build_model_info_section(self, details: dict, model_info: dict):
        _, cl = self._add_section("Model Info")

        pairs = [
            ("Format",       details.get("format", "—")),
            ("Family",       details.get("family", "—")),
            ("Architecture", model_info.get("general.architecture", "—")),
            ("Parameters",   details.get("parameter_size", "—")),
            ("Quantisation", details.get("quantization_level", "—")),
        ]
        # Attention heads
        for k in ("llama.attention.head_count", "qwen2.attention.head_count"):
            if k in model_info:
                pairs.append(("Attn heads", str(model_info[k])))
                break
        # KV heads
        for k in ("llama.attention.head_count_kv", "qwen2.attention.head_count_kv"):
            if k in model_info:
                pairs.append(("KV heads", str(model_info[k])))
                break
        # Layers
        for k in ("llama.block_count", "qwen2.block_count", "phi3.block_count"):
            if k in model_info:
                pairs.append(("Layers", str(model_info[k])))
                break
        # Embedding length
        for k in ("llama.embedding_length", "qwen2.embedding_length"):
            if k in model_info:
                pairs.append(("Embedding dim", f"{model_info[k]:,}"))
                break

        for key, val in pairs:
            if val and val != "—":
                self._kv_row(cl, key, str(val))

    def _build_system_prompt_section(self, system: str):
        if not system or not system.strip():
            return
        _, cl = self._add_section("Built-in System Prompt")
        self._code_block(cl, system, max_lines=10)

    def _build_parameters_section(self, parameters: str):
        if not parameters or not parameters.strip():
            return
        _, cl = self._add_section("Parameters (Modelfile)")
        self._code_block(cl, parameters, max_lines=12)

    def _build_template_section(self, template: str):
        if not template or not template.strip():
            return
        _, cl = self._add_section("Chat Template")
        self._code_block(cl, template, max_lines=10)

    # ── Public API ─────────────────────────────────────────────────────────

    def show_loading(self, name: str):
        self.model_name_lbl.setText(name)
        self.model_meta_lbl.setText("Loading profile…")
        self.stat_size.set_value("—")
        self.stat_params.set_value("—")
        self.stat_ctx.set_value("—")
        self.stat_quant.set_value("—")
        self.stat_family.set_value("—")
        self._render_tags([])
        self._clear_body()

    def show_data(self, row, detail: dict | None):
        self.model_name_lbl.setText(row.name)
        self.model_meta_lbl.setText(f"Modified {parse_time(row.modified_at)}")
        self.stat_size.set_value(human_bytes(row.size))
        self._clear_body()

        details    = {}
        model_info = {}
        parameters = ""
        system     = ""
        template   = ""

        if detail:
            details    = detail.get("details", {}) or {}
            model_info = detail.get("model_info", {}) or {}
            parameters = detail.get("parameters", "") or ""
            system     = detail.get("system", "") or ""
            template   = detail.get("template", "") or ""

        # Stat cards
        self.stat_params.set_value(details.get("parameter_size", "—") or "—")
        self.stat_family.set_value(details.get("family", "—") or "—")

        quant = details.get("quantization_level", "")
        if not quant:
            for q in ("q4_k_m", "q4_k_s", "q8_0", "q4_0", "q5_k_m",
                      "q5_0", "q6_k", "q3_k_m", "fp16", "f16", "bf16"):
                if q in row.name.lower():
                    quant = q.upper()
                    break
        self.stat_quant.set_value(quant or "—")

        ctx = None
        for k in ("llama.context_length", "context_length",
                  "qwen2.context_length", "gemma.context_length",
                  "phi3.context_length", "mistral.context_length"):
            if k in model_info:
                ctx = model_info[k]
                break
        if ctx is None and parameters:
            for line in parameters.splitlines():
                if "num_ctx" in line:
                    for p in line.split():
                        if p.isdigit():
                            ctx = int(p)
                            break
        self.stat_ctx.set_value(f"{int(ctx):,}" if ctx else "—")

        # Tags
        tags = self._infer_tags(row.name, details)
        self._render_tags(tags)

        # Profile sections
        self._build_capabilities_section(tags)
        self._build_usage_section(row.name, details, model_info, parameters, tags)
        self._build_model_info_section(details, model_info)
        if system:
            self._build_system_prompt_section(system)
        if parameters:
            self._build_parameters_section(parameters)
        if template:
            self._build_template_section(template)

    def show_multi(self, count: int):
        self.model_name_lbl.setText(f"{count} models selected")
        self.model_meta_lbl.setText("Select a single model to view its profile")
        self.stat_size.set_value("—")
        self.stat_params.set_value("—")
        self.stat_ctx.set_value("—")
        self.stat_quant.set_value("—")
        self.stat_family.set_value("—")
        self._render_tags([])
        self._clear_body()

    def clear(self):
        self.model_name_lbl.setText("Select a model")
        self.model_meta_lbl.setText("")
        self.stat_size.set_value("—")
        self.stat_params.set_value("—")
        self.stat_ctx.set_value("—")
        self.stat_quant.set_value("—")
        self.stat_family.set_value("—")
        self._render_tags([])
        self._clear_body()



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

    def __init__(self, res: BenchResult, is_winner: bool = False,
                 gpu_label: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("bench_result_winner" if is_winner else "bench_result")
        self._build(res, is_winner, gpu_label)

    def _build(self, res: BenchResult, is_winner: bool, gpu_label: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        name = QLabel(res.model)
        name.setStyleSheet("color: #e2e8f0; font-size: 14px; font-weight: 700;")
        hdr.addWidget(name)
        if gpu_label:
            g_lbl = QLabel(f"🖥 {gpu_label}")
            g_lbl.setStyleSheet(
                "background:#0d1f14; border:1px solid #166534; border-radius:4px;"
                "color:#4ade80; font-size:11px; font-weight:600; padding:2px 7px;"
            )
            hdr.addWidget(g_lbl)
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

