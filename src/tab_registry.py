"""
Mixin — imported via multiple inheritance into MainWindow.
All methods reference self which is a MainWindow instance.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

import json, os, re, subprocess, sys, time, zipfile, tempfile
from datetime import datetime
from pathlib import Path

import requests
from PyQt6.QtCore import Qt, QModelIndex, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QProgressBar, QScrollArea, QSplitter,
    QTableView, QTextEdit, QVBoxLayout, QWidget)
from utils import (
    human_bytes, parse_time, safe_mkdir, default_models_dir_guess,
    get_system_stats,
    blob_filename_from_digest)
from workers import (
    Worker, PullWorker, ExportImportWorker, start_worker,
    RegistryFetchWorker, BenchmarkWorker, BenchResult,
    ResourceMonitorWorker, DiskAnalysisWorker)
from widgets import (
    SectionLabel, Separator, StatCard, GaugeBar, GaugeCard,
    MonitorSparkWidget, ModelfileHighlighter, RegistryModelCard,
    TagsDialog, BenchResultCard, ConfirmDeleteDialog, prompt_text)
from models import (
    analyse_disk,
    InstalledModelRow, RunningModelRow,
    InstalledModelsTableModel, RunningModelsTableModel)


class RegistryMixin:
    def _tab_registry(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Toolbar
        toolbar = QHBoxLayout()
        self.reg_search = QLineEdit()
        self.reg_search.setPlaceholderText("Search models by name…")
        self.reg_search.setClearButtonEnabled(True)
        self.reg_search.textChanged.connect(self._reg_apply_filter)
        self.reg_search.setMaximumWidth(340)

        self.reg_refresh_btn = QPushButton("↻  Fetch Registry")
        self.reg_refresh_btn.setObjectName("btn_primary")
        self.reg_refresh_btn.clicked.connect(self._reg_fetch)

        self.reg_status_lbl = QLabel("Click 'Fetch Registry' to load models from ollama.com")
        self.reg_status_lbl.setObjectName("label_muted")

        toolbar.addWidget(self.reg_search)
        toolbar.addWidget(self.reg_refresh_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.reg_status_lbl)
        layout.addLayout(toolbar)

        layout.addWidget(Separator())

        # Scroll area for cards
        self.reg_scroll = QScrollArea()
        self.reg_scroll.setWidgetResizable(True)
        self.reg_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.reg_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.reg_cards_widget = QWidget()
        self.reg_cards_layout = QVBoxLayout(self.reg_cards_widget)
        self.reg_cards_layout.setContentsMargins(0, 0, 8, 0)
        self.reg_cards_layout.setSpacing(8)
        self.reg_cards_layout.addStretch(1)

        self.reg_scroll.setWidget(self.reg_cards_widget)
        layout.addWidget(self.reg_scroll)

        return container


    def _reg_fetch(self):
        self.reg_refresh_btn.setEnabled(False)
        self.reg_status_lbl.setText("Fetching from ollama.com…")
        self._set_status("Fetching model registry…")

        worker = RegistryFetchWorker(self.client)
        worker.finished.connect(self._reg_on_data)
        worker.failed.connect(self._reg_on_fail)
        self._start_worker(worker)


    def _reg_on_data(self, models: list):
        self._registry_cache = models
        self.reg_refresh_btn.setEnabled(True)
        self.reg_status_lbl.setText(f"{len(models)} models")
        self._set_status(f"Registry loaded — {len(models)} models.")
        self._reg_apply_filter(self.reg_search.text())


    def _reg_on_fail(self, msg: str):
        self.reg_refresh_btn.setEnabled(True)
        self.reg_status_lbl.setText("Fetch failed — see error")
        self._set_status(f"Registry fetch failed: {msg[:120]}")
        self._err(
            "Registry Fetch Failed",
            "Could not load models from ollama.com.\n\n"
            "Possible causes:\n"
            "  • No internet connection\n"
            "  • ollama.com API changed (check for app updates)\n"
            "  • A proxy or firewall is blocking the request\n\n"
            f"Detail: {msg}"
        )


    def _reg_apply_filter(self, text: str = ""):
        f = (text or "").strip().lower()
        models = [
            m for m in self._registry_cache
            if not f or f in (m.get("name") or m.get("slug", "")).lower()
               or f in (m.get("description") or "").lower()
        ]
        self._reg_render_cards(models)


    def _reg_render_cards(self, models: list):
        # Clear existing cards (keep the trailing stretch)
        while self.reg_cards_layout.count() > 1:
            item = self.reg_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        installed = set(r.name for r in self.installed_model.rows())

        # Two-column grid using nested HBoxLayouts
        row_layout = None
        for i, info in enumerate(models):
            if i % 2 == 0:
                row_layout = QHBoxLayout()
                row_layout.setSpacing(8)
                self.reg_cards_layout.insertLayout(
                    self.reg_cards_layout.count() - 1, row_layout
                )
            card = RegistryModelCard(info, installed)
            card.pull_requested.connect(self._reg_pull_model)
            card.tags_requested.connect(self._reg_show_tags)
            if row_layout is not None:
                row_layout.addWidget(card, 1)

        # Pad last row if odd count
        if models and len(models) % 2 == 1 and row_layout is not None:
            row_layout.addWidget(QWidget(), 1)


    def _reg_pull_model(self, slug: str):
        model_name = slug
        self.tabs.setCurrentIndex(2)   # Jump to Pull & Backup tab
        self.pull_model_name.setText(model_name)
        self._set_status(f"Ready to pull {model_name} — press Pull Model.")


    def _reg_show_tags(self, slug: str):
        self.reg_status_lbl.setText(f"Loading tags for {slug}…")
        self._set_status(f"Fetching tags for {slug}…")
        worker = RegistryFetchWorker(self.client, fetch_tags_for=slug)
        worker.finished.connect(lambda _: None)
        worker.tags_ready.connect(lambda s, tags: self._reg_open_tags_dialog(s, tags))
        worker.failed.connect(lambda msg: (
            self.reg_status_lbl.setText("Tags fetch failed"),
            self._err("Tags Failed", msg)
        ))
        self._start_worker(worker)


    def _reg_open_tags_dialog(self, slug: str, tags: list):
        self.reg_status_lbl.setText(f"{len(self._registry_cache)} models")
        installed = set(r.name for r in self.installed_model.rows())
        dlg = TagsDialog(slug, tags, installed, self)
        dlg.pull_tag_requested.connect(self._reg_pull_tag)
        dlg.exec()


    def _reg_pull_tag(self, full_name: str):
        self.tabs.setCurrentIndex(2)
        self.pull_model_name.setText(full_name)
        self._set_status(f"Ready to pull {full_name} — press Pull Model.")

    # ── Benchmark Tab ─────────────────────────────
