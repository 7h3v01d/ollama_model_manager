"""
Mixin — About / Info tab.
Shows version, architecture overview, keyboard shortcuts,
API surface, and third-party dependency info.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from widgets import Separator, SectionLabel

APP_VERSION  = "4.0.0"
APP_NAME     = "Ollama Manager Pro"
ORGANIZATION = "KeystoneAI"
AUTHOR       = "Leon"


class AboutMixin:

    def _tab_about(self) -> QWidget:
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Scroll wrapper so nothing clips on small windows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 36, 40, 36)
        layout.setSpacing(32)

        # ── Hero ──────────────────────────────────────────────────────
        hero = QWidget()
        hero.setStyleSheet(
            "background-color:#080c13; border:1px solid #1e2533; border-radius:12px;")
        hero_l = QVBoxLayout(hero)
        hero_l.setContentsMargins(32, 28, 32, 28)
        hero_l.setSpacing(8)

        title = QLabel(APP_NAME)
        title.setStyleSheet(
            "color:#60a5fa; font-size:26px; font-weight:800; "
            "letter-spacing:0.5px; background:transparent; border:none;")
        title.setAlignment(Qt.AlignmentFlag.AlignLeft)

        ver = QLabel(f"Version {APP_VERSION}  ·  {ORGANIZATION}  ·  Built by {AUTHOR}")
        ver.setStyleSheet(
            "color:#4b5563; font-size:13px; background:transparent; border:none;")

        tagline = QLabel(
            "A commercial-grade desktop manager for local Ollama LLM models.\n"
            "Pull, benchmark, monitor, edit Modelfiles, and chat — all in one place.")
        tagline.setWordWrap(True)
        tagline.setStyleSheet(
            "color:#94a3b8; font-size:13px; line-height:1.6; "
            "background:transparent; border:none;")

        hero_l.addWidget(title)
        hero_l.addWidget(ver)
        hero_l.addSpacing(6)
        hero_l.addWidget(tagline)
        layout.addWidget(hero)

        # ── Tabs quick-reference ──────────────────────────────────────
        layout.addWidget(SectionLabel("Tabs"))

        TABS = [
            ("Installed Models",
             "Browse all locally installed models. Select one to see format, family, "
             "parameter size, and raw detail from /api/show. Multi-select for batch delete."),
            ("Running",
             "Live view of models currently loaded in memory via /api/ps. "
             "Auto-refresh every 5 s."),
            ("Servers",
             "Manage multiple Ollama server connections. Ping each server, view "
             "model counts and latency, add/remove/edit, and switch active server. "
             "Config persists to disk between sessions."),
            ("Pull & Backup",
             "Stream model downloads with live progress. Export selected models or the "
             "entire store to a ZIP archive (manifest + blobs). Import archives back."),
            ("Registry",
             "Browse ollama.com/library without leaving the app. Filter, view available "
             "tags with sizes, and pull any tag directly."),
            ("Benchmark",
             "Race installed models head-to-head on a prompt. Reports tokens/s, "
             "time-to-first-token, and total wall time. Evicts each model from VRAM "
             "after its run so later models aren't competing for memory."),
            ("Batch Runner",
             "Load prompts from JSONL, CSV, or plain text and run them against a model "
             "sequentially. Live per-row status in a results table. Export results to "
             "JSONL or CSV with prompt, response, tokens/s, and timing columns."),
            ("Prompts",
             "Prompt library backed by SQLite. Browse, create, edit, tag and search "
             "saved prompts and system prompts. One-click inject into the Chat tab. "
             "Seeded with useful defaults on first run."),
            ("Chat",
             "Full streaming conversation panel. Ctrl+Enter to send. Maintains full "
             "conversation history across turns. System prompt editor, temperature, "
             "and context length controls in the toolbar."),
            ("Monitor",
             "Live CPU, RAM, and GPU/VRAM gauges with sparkline history. "
             "NVIDIA via nvidia-smi, AMD via rocm-smi. CPU/RAM requires psutil."),
            ("Modelfile",
             "Syntax-highlighted Modelfile editor. Quick-param sliders sync into the "
             "editor. One-click ollama create runs in the background."),
            ("Disk",
             "Per-model storage breakdown with proportional bars. Detects orphaned "
             "blobs not referenced by any manifest and offers a confirmed purge."),
        ]

        for tab_name, description in TABS:
            row = QFrame()
            row.setStyleSheet(
                "QFrame { background-color:#111827; border:1px solid #1e2533; "
                "border-radius:8px; }")
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(16, 12, 16, 12)
            row_l.setSpacing(16)

            name_lbl = QLabel(tab_name)
            name_lbl.setFixedWidth(148)
            name_lbl.setStyleSheet(
                "color:#60a5fa; font-size:13px; font-weight:700; "
                "background:transparent; border:none;")
            name_lbl.setAlignment(
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

            sep = QFrame()
            sep.setObjectName("separator_v")
            sep.setFrameShape(QFrame.Shape.VLine)

            desc_lbl = QLabel(description)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet(
                "color:#64748b; font-size:12px; background:transparent; border:none;")

            row_l.addWidget(name_lbl)
            row_l.addWidget(sep)
            row_l.addWidget(desc_lbl, 1)
            layout.addWidget(row)

        # ── Keyboard shortcuts ────────────────────────────────────────
        layout.addWidget(SectionLabel("Keyboard Shortcuts"))

        SHORTCUTS = [
            ("Ctrl+Enter", "Send message (Chat tab)"),
            ("Ctrl+click", "Multi-select models in tables"),
            ("Delete / Backspace", "Not bound — use the Delete buttons"),
        ]
        shortcuts_frame = QFrame()
        shortcuts_frame.setStyleSheet(
            "QFrame { background-color:#111827; border:1px solid #1e2533; "
            "border-radius:8px; }")
        sf_l = QVBoxLayout(shortcuts_frame)
        sf_l.setContentsMargins(16, 12, 16, 12)
        sf_l.setSpacing(8)

        for keys, action in SHORTCUTS:
            sr = QHBoxLayout()
            k_lbl = QLabel(keys)
            k_lbl.setFixedWidth(180)
            k_lbl.setStyleSheet(
                "color:#e2e8f0; font-size:12px; font-weight:700; font-family:monospace; "
                "background:#1e2533; border:1px solid #2a3347; border-radius:4px; "
                "padding:2px 8px;")
            a_lbl = QLabel(action)
            a_lbl.setStyleSheet(
                "color:#64748b; font-size:12px; background:transparent; border:none;")
            sr.addWidget(k_lbl)
            sr.addWidget(a_lbl)
            sr.addStretch(1)
            sf_l.addLayout(sr)

        layout.addWidget(shortcuts_frame)

        # ── API surface ───────────────────────────────────────────────
        layout.addWidget(SectionLabel("Ollama API Endpoints Used"))

        ENDPOINTS = [
            ("GET",    "/api/tags",      "Installed model list"),
            ("POST",   "/api/show",      "Model detail panel"),
            ("DELETE", "/api/delete",    "Model deletion"),
            ("GET",    "/api/ps",        "Running models & monitor"),
            ("POST",   "/api/pull",      "Model download (streaming)"),
            ("POST",   "/api/generate",  "Benchmark & VRAM eviction (keep_alive: 0)"),
            ("POST",   "/api/chat",      "Chat inference (streaming)"),
        ]

        api_frame = QFrame()
        api_frame.setStyleSheet(
            "QFrame { background-color:#111827; border:1px solid #1e2533; "
            "border-radius:8px; }")
        af_l = QVBoxLayout(api_frame)
        af_l.setContentsMargins(16, 12, 16, 12)
        af_l.setSpacing(6)

        METHOD_COLORS = {
            "GET":    "#10b981",
            "POST":   "#3b82f6",
            "DELETE": "#f87171",
        }

        for method, path, desc in ENDPOINTS:
            ar = QHBoxLayout()
            m_lbl = QLabel(method)
            m_lbl.setFixedWidth(56)
            color = METHOD_COLORS.get(method, "#64748b")
            m_lbl.setStyleSheet(
                f"color:{color}; font-size:11px; font-weight:700; font-family:monospace; "
                f"background:transparent; border:none;")
            p_lbl = QLabel(path)
            p_lbl.setFixedWidth(180)
            p_lbl.setStyleSheet(
                "color:#a5f3fc; font-size:12px; font-family:monospace; "
                "background:transparent; border:none;")
            d_lbl = QLabel(desc)
            d_lbl.setStyleSheet(
                "color:#4b5563; font-size:12px; background:transparent; border:none;")
            ar.addWidget(m_lbl)
            ar.addWidget(p_lbl)
            ar.addWidget(d_lbl)
            ar.addStretch(1)
            af_l.addLayout(ar)

        layout.addWidget(api_frame)

        # ── Dependencies ──────────────────────────────────────────────
        layout.addWidget(SectionLabel("Dependencies"))

        DEPS = [
            ("PyQt6",    "≥ 6.5",  "UI framework — Qt6 widgets, signals, threading"),
            ("requests", "≥ 2.28", "HTTP client — Ollama API + ollama.com registry"),
            ("psutil",   "≥ 5.9",  "Optional — CPU and RAM gauges in Monitor tab"),
            ("nvidia-smi", "system", "Optional — NVIDIA GPU VRAM metrics"),
            ("rocm-smi",   "system", "Optional — AMD GPU VRAM metrics"),
        ]

        deps_frame = QFrame()
        deps_frame.setStyleSheet(
            "QFrame { background-color:#111827; border:1px solid #1e2533; "
            "border-radius:8px; }")
        df_l = QVBoxLayout(deps_frame)
        df_l.setContentsMargins(16, 12, 16, 12)
        df_l.setSpacing(6)

        for pkg, ver, desc in DEPS:
            dr = QHBoxLayout()
            pkg_lbl = QLabel(pkg)
            pkg_lbl.setFixedWidth(100)
            pkg_lbl.setStyleSheet(
                "color:#e2e8f0; font-size:12px; font-weight:700; "
                "background:transparent; border:none;")
            ver_lbl = QLabel(ver)
            ver_lbl.setFixedWidth(70)
            ver_lbl.setStyleSheet(
                "color:#64748b; font-size:12px; font-family:monospace; "
                "background:transparent; border:none;")
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet(
                "color:#4b5563; font-size:12px; background:transparent; border:none;")
            dr.addWidget(pkg_lbl)
            dr.addWidget(ver_lbl)
            dr.addWidget(desc_lbl)
            dr.addStretch(1)
            df_l.addLayout(dr)

        layout.addWidget(deps_frame)

        # ── Footer ────────────────────────────────────────────────────
        footer = QLabel(
            f"Built by {AUTHOR} · {ORGANIZATION} · "
            "Log file: ollama_manager.log  ·  Run with: python main.py"
        )
        footer.setStyleSheet(
            "color:#1f2937; font-size:11px; background:transparent; border:none;")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(footer)

        layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll)
        return container
