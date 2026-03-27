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


class BenchmarkMixin:
    def _tab_benchmark(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        # ── Config group ─────────────────────────
        cfg_group = QGroupBox("Benchmark Configuration")
        cfg_layout = QVBoxLayout(cfg_group)
        cfg_layout.setSpacing(10)

        # Prompt row
        prompt_row = QHBoxLayout()
        prompt_lbl = QLabel("Prompt:")
        prompt_lbl.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 600;")
        prompt_lbl.setFixedWidth(80)

        self.bench_prompt = QLineEdit()
        self.bench_prompt.setText(
            "Explain the difference between supervised and unsupervised machine learning "
            "in three concise sentences."
        )
        self.bench_prompt.setPlaceholderText("Enter a prompt to benchmark against all selected models")
        prompt_row.addWidget(prompt_lbl)
        prompt_row.addWidget(self.bench_prompt, 1)
        cfg_layout.addLayout(prompt_row)

        # Model selection row
        model_sel_row = QHBoxLayout()
        model_sel_lbl = QLabel("Models:")
        model_sel_lbl.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 600;")
        model_sel_lbl.setFixedWidth(80)

        self.bench_model_list = QTableView()
        self.bench_model_list.setMaximumHeight(130)
        self.bench_model_list.setModel(self.installed_model)
        self.bench_model_list.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.bench_model_list.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.bench_model_list.setAlternatingRowColors(True)
        self.bench_model_list.setShowGrid(False)
        self.bench_model_list.verticalHeader().hide()
        self.bench_model_list.verticalHeader().setDefaultSectionSize(32)
        self.bench_model_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bench_model_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.bench_model_list.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.bench_model_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        model_sel_row.addWidget(model_sel_lbl, 0, Qt.AlignmentFlag.AlignTop)
        model_sel_row.addWidget(self.bench_model_list, 1)
        cfg_layout.addLayout(model_sel_row)

        # Runs + action row
        action_row = QHBoxLayout()
        hint = QLabel("Select models above (Ctrl/Shift+click for multiple), then run.")
        hint.setObjectName("label_muted")
        action_row.addWidget(hint)
        action_row.addStretch(1)

        self.bench_run_btn = QPushButton("▶  Run Benchmark")
        self.bench_run_btn.setObjectName("btn_primary")
        self.bench_run_btn.setMinimumWidth(150)
        self.bench_run_btn.clicked.connect(self._bench_run)

        self.bench_stop_btn = QPushButton("■  Stop")
        self.bench_stop_btn.setObjectName("btn_cancel")
        self.bench_stop_btn.setMinimumWidth(80)
        self.bench_stop_btn.setEnabled(False)
        self.bench_stop_btn.clicked.connect(self._bench_stop)

        self.bench_clear_btn = QPushButton("Clear")
        self.bench_clear_btn.setMinimumWidth(70)
        self.bench_clear_btn.clicked.connect(self._bench_clear)

        action_row.addWidget(self.bench_clear_btn)
        action_row.addWidget(self.bench_stop_btn)
        action_row.addWidget(self.bench_run_btn)
        cfg_layout.addLayout(action_row)

        layout.addWidget(cfg_group)

        # ── Progress bar ─────────────────────────
        self.bench_progress = QProgressBar()
        self.bench_progress.setRange(0, 100)
        self.bench_progress.setValue(0)
        self.bench_progress.setFormat("Idle")
        layout.addWidget(self.bench_progress)

        # ── Results area ─────────────────────────
        results_header = QHBoxLayout()
        results_header.addWidget(SectionLabel("Results"))
        self.bench_summary_lbl = QLabel("")
        self.bench_summary_lbl.setObjectName("label_muted")
        results_header.addWidget(self.bench_summary_lbl)
        results_header.addStretch(1)
        layout.addLayout(results_header)

        self.bench_results_scroll = QScrollArea()
        self.bench_results_scroll.setWidgetResizable(True)
        self.bench_results_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.bench_results_widget = QWidget()
        self.bench_results_layout = QVBoxLayout(self.bench_results_widget)
        self.bench_results_layout.setContentsMargins(0, 0, 8, 0)
        self.bench_results_layout.setSpacing(8)
        self.bench_results_layout.addStretch(1)

        self.bench_results_scroll.setWidget(self.bench_results_widget)
        layout.addWidget(self.bench_results_scroll)

        return container


    def _bench_run(self):
        prompt = self.bench_prompt.text().strip()
        if not prompt:
            self._warn("Missing Prompt", "Enter a prompt to benchmark.")
            return

        sels = self.bench_model_list.selectionModel().selectedRows()
        models = []
        for idx in sels:
            if idx.row() < len(self.installed_model.rows()):
                r = self.installed_model.rows()[idx.row()]
                if r.name:
                    models.append(r.name)
        if not models:
            self._warn("No Models Selected",
                       "Select one or more models in the table above (Ctrl+click for multiple).")
            return

        if self._bench_thread and self._bench_thread.isRunning():
            self._warn("Already Running", "A benchmark is in progress. Stop it first.")
            return

        self._bench_clear()

        # Create placeholder cards
        self._bench_result_cards: list[QFrame] = []
        self._bench_results: list[BenchResult] = []
        for i, model in enumerate(models):
            res = BenchResult(model=model, prompt=prompt, status="pending")
            self._bench_results.append(res)
            card = self._make_bench_placeholder(model)
            self._bench_result_cards.append(card)
            self.bench_results_layout.insertWidget(
                self.bench_results_layout.count() - 1, card
            )

        self.bench_run_btn.setEnabled(False)
        self.bench_stop_btn.setEnabled(True)
        self.bench_progress.setValue(0)
        self.bench_progress.setFormat("Running…")
        self._bench_done_count = 0
        self._bench_total = len(models)
        self._set_status(f"Benchmarking {len(models)} model(s)…")

        runs = [(i, m, prompt) for i, m in enumerate(models)]
        worker = BenchmarkWorker(self.client, runs)
        worker.result_update.connect(self._bench_on_update)
        worker.finished.connect(self._bench_on_finished)
        worker.failed.connect(self._bench_on_failed)

        self._bench_worker = worker
        self._bench_thread = self._start_worker(worker)


    def _make_bench_placeholder(self, model_name: str) -> QFrame:
        card = QFrame()
        card.setObjectName("bench_result")
        card.setMinimumHeight(80)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(14, 0, 14, 0)
        lbl = QLabel(f"⏳  {model_name}  —  waiting…")
        lbl.setStyleSheet("color: #374151; font-size: 13px;")
        card_layout.addWidget(lbl)
        return card


    def _bench_on_update(self, idx: int, res: BenchResult):
        if idx >= len(self._bench_result_cards):
            return
        self._bench_results[idx] = res

        # Replace placeholder card
        old_card = self._bench_result_cards[idx]
        pos = self.bench_results_layout.indexOf(old_card)
        if pos < 0:
            return
        old_card.setParent(None)
        old_card.deleteLater()

        if res.status == "running":
            new_card = QFrame()
            new_card.setObjectName("bench_result")
            new_card.setMinimumHeight(80)
            ncl = QHBoxLayout(new_card)
            ncl.setContentsMargins(14, 0, 14, 0)
            lbl = QLabel(f"▶  {res.model}  —  running…")
            lbl.setStyleSheet("color: #60a5fa; font-size: 13px;")
            ncl.addWidget(lbl)
        else:
            new_card = BenchResultCard(res, is_winner=False)
            if res.status == "done":
                self._bench_done_count += 1
                pct = int(self._bench_done_count / self._bench_total * 100)
                self.bench_progress.setValue(pct)
                self.bench_progress.setFormat(
                    f"{self._bench_done_count}/{self._bench_total} complete"
                )

        self._bench_result_cards[idx] = new_card
        self.bench_results_layout.insertWidget(pos, new_card)


    def _bench_on_finished(self, results: list):
        self.bench_run_btn.setEnabled(True)
        self.bench_stop_btn.setEnabled(False)
        self.bench_progress.setValue(100)
        self.bench_progress.setFormat("Complete")
        self._bench_worker = None
        self._bench_thread = None

        done = [r for r in self._bench_results if r.status == "done" and r.tokens_per_sec > 0]
        if done:
            winner = max(done, key=lambda r: r.tokens_per_sec)
            for i, res in enumerate(self._bench_results):
                if res.model == winner.model and res.status == "done":
                    old = self._bench_result_cards[i]
                    pos = self.bench_results_layout.indexOf(old)
                    if pos >= 0:
                        old.setParent(None)
                        old.deleteLater()
                        new_card = BenchResultCard(res, is_winner=True)
                        self._bench_result_cards[i] = new_card
                        self.bench_results_layout.insertWidget(pos, new_card)
            self.bench_summary_lbl.setText(
                f"Fastest: {winner.model}  ({winner.tokens_per_sec:.1f} tok/s)"
            )

        n_ok = sum(1 for r in self._bench_results if r.status == "done")
        n_err = sum(1 for r in self._bench_results if r.status == "error")
        self._set_status(
            f"Benchmark complete — {n_ok} succeeded, {n_err} failed."
        )


    def _bench_on_failed(self, msg: str):
        self.bench_run_btn.setEnabled(True)
        self.bench_stop_btn.setEnabled(False)
        self.bench_progress.setFormat("Failed")
        self._bench_worker = None
        self._bench_thread = None
        self._err("Benchmark Failed", msg)


    def _bench_stop(self):
        if self._bench_worker:
            self._bench_worker.stop()
        self.bench_stop_btn.setEnabled(False)
        self.bench_progress.setFormat("Stopped")
        self._set_status("Benchmark stopped.")


    def _bench_clear(self):
        self._bench_results = []
        self._bench_result_cards = []
        self.bench_summary_lbl.setText("")
        self.bench_progress.setValue(0)
        self.bench_progress.setFormat("Idle")
        while self.bench_results_layout.count() > 1:
            item = self.bench_results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


    # ══════════════════════════════════════════
    # RESOURCE MONITOR TAB
    # ══════════════════════════════════════════
