"""
Mixin — Benchmark tab.

Upgraded (v4.1):
  - Per-GPU targeting via CUDA_VISIBLE_DEVICES passed as env hint in results
  - GPU selector combo populated from nvidia-smi / rocm-smi at tab load
  - GPU info shown on each result card
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

import json
import subprocess

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from utils import human_bytes
from workers import Worker, BenchmarkWorker, BenchResult, start_worker
from widgets import SectionLabel, Separator, StatCard, BenchResultCard
from models import InstalledModelsTableModel


# ── GPU enumeration ────────────────────────────────────────────────────────

def _enumerate_nvidia_gpus() -> list[dict]:
    """Return list of {index, name, vram_total_mb} for each NVIDIA GPU."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.total",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        gpus = []
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                gpus.append({
                    "index": int(parts[0]),
                    "name":  parts[1],
                    "vram":  int(parts[2]),
                })
        return gpus
    except Exception:
        return []


def _enumerate_amd_gpus() -> list[dict]:
    """Return list of {index, name, vram_total_mb} for each AMD GPU via rocm-smi."""
    try:
        out = subprocess.check_output(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode()
        data = json.loads(out)
        gpus = []
        for i, (card, info) in enumerate(data.items()):
            if "card" in card.lower():
                total_b = int(info.get("VRAM Total Memory (B)", 0))
                gpus.append({
                    "index": i,
                    "name":  card,
                    "vram":  total_b // (1024 * 1024),
                })
        return gpus
    except Exception:
        return []


def enumerate_gpus() -> list[dict]:
    """Return all available GPUs (NVIDIA first, then AMD), or [] if none."""
    gpus = _enumerate_nvidia_gpus()
    if not gpus:
        gpus = _enumerate_amd_gpus()
    return gpus


# ── Benchmark Mixin ────────────────────────────────────────────────────────

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

        # GPU selector row
        gpu_row = QHBoxLayout()
        gpu_lbl = QLabel("GPU:")
        gpu_lbl.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 600;")
        gpu_lbl.setFixedWidth(80)

        self.bench_gpu_combo = QComboBox()
        self.bench_gpu_combo.setMinimumWidth(320)
        self.bench_gpu_combo.setToolTip(
            "Select which GPU Ollama should use for this benchmark run.\n"
            "Sets CUDA_VISIBLE_DEVICES (NVIDIA) or HIP_VISIBLE_DEVICES (AMD)\n"
            "via the Ollama /api/generate options field.")
        gpu_row.addWidget(gpu_lbl)
        gpu_row.addWidget(self.bench_gpu_combo)

        self.bench_gpu_refresh_btn = QPushButton("↻")
        self.bench_gpu_refresh_btn.setFixedWidth(32)
        self.bench_gpu_refresh_btn.setToolTip("Re-scan GPUs")
        self.bench_gpu_refresh_btn.clicked.connect(self._bench_refresh_gpus)
        gpu_row.addWidget(self.bench_gpu_refresh_btn)
        gpu_row.addStretch(1)

        self.bench_gpu_info_lbl = QLabel("")
        self.bench_gpu_info_lbl.setStyleSheet("color:#4b5563; font-size:11px;")
        gpu_row.addWidget(self.bench_gpu_info_lbl)
        cfg_layout.addLayout(gpu_row)

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

        # GPU data store
        self._bench_gpus: list[dict] = []

        # Populate GPU list after a short delay so the window is shown first
        QTimer.singleShot(500, self._bench_refresh_gpus)

        return container

    # ── GPU helpers ───────────────────────────────────────────────────────

    def _bench_refresh_gpus(self):
        self._bench_gpus = enumerate_gpus()
        self.bench_gpu_combo.clear()
        if not self._bench_gpus:
            self.bench_gpu_combo.addItem("No GPU detected — Ollama will auto-select")
            self.bench_gpu_info_lbl.setText("")
        else:
            self.bench_gpu_combo.addItem("All GPUs (Ollama default)")
            for g in self._bench_gpus:
                vram_gb = g["vram"] / 1024
                self.bench_gpu_combo.addItem(
                    f"GPU {g['index']}: {g['name']}  [{vram_gb:.1f} GB VRAM]",
                    userData=g["index"],
                )
            self.bench_gpu_info_lbl.setText(
                f"{len(self._bench_gpus)} GPU(s) found")

    def _bench_selected_gpu_index(self) -> int | None:
        """Returns GPU index (0-based) or None for 'all GPUs'."""
        if not self._bench_gpus:
            return None
        idx = self.bench_gpu_combo.currentIndex()
        if idx <= 0:  # "All GPUs" option
            return None
        gpu = self._bench_gpus[idx - 1]
        return gpu["index"]

    def _bench_selected_gpu_label(self) -> str:
        if not self._bench_gpus:
            return "no GPU"
        idx = self.bench_gpu_combo.currentIndex()
        if idx <= 0:
            return "all GPUs"
        g = self._bench_gpus[idx - 1]
        return f"GPU {g['index']} ({g['name']})"

    # ── Run ───────────────────────────────────────────────────────────────

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

        gpu_idx   = self._bench_selected_gpu_index()
        gpu_label = self._bench_selected_gpu_label()

        self._bench_clear()

        # Create placeholder cards
        self._bench_result_cards: list[QFrame] = []
        self._bench_results: list[BenchResult] = []
        for i, model in enumerate(models):
            res = BenchResult(model=model, prompt=prompt, status="pending")
            self._bench_results.append(res)
            card = self._make_bench_placeholder(model, gpu_label)
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
        self._set_status(
            f"Benchmarking {len(models)} model(s) on {gpu_label}…")

        runs = [(i, m, prompt) for i, m in enumerate(models)]
        worker = BenchmarkWorker(self.client, runs, gpu_index=gpu_idx)
        worker.result_update.connect(self._bench_on_update)
        worker.finished.connect(self._bench_on_finished)
        worker.failed.connect(self._bench_on_failed)

        self._bench_worker = worker
        self._bench_thread = self._start_worker(worker)

    def _make_bench_placeholder(self, model_name: str, gpu_label: str = "") -> QFrame:
        card = QFrame()
        card.setObjectName("bench_result")
        card.setMinimumHeight(80)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(14, 0, 14, 0)
        suffix = f"  [{gpu_label}]" if gpu_label else ""
        lbl = QLabel(f"⏳  {model_name}{suffix}  —  waiting…")
        lbl.setStyleSheet("color: #374151; font-size: 13px;")
        card_layout.addWidget(lbl)
        return card

    def _bench_on_update(self, idx: int, res: BenchResult):
        if idx >= len(self._bench_result_cards):
            return
        self._bench_results[idx] = res

        old_card = self._bench_result_cards[idx]
        pos = self.bench_results_layout.indexOf(old_card)
        if pos < 0:
            return
        old_card.setParent(None)
        old_card.deleteLater()

        gpu_label = self._bench_selected_gpu_label()

        if res.status == "running":
            new_card = QFrame()
            new_card.setObjectName("bench_result")
            new_card.setMinimumHeight(80)
            ncl = QHBoxLayout(new_card)
            ncl.setContentsMargins(14, 0, 14, 0)
            lbl = QLabel(f"▶  {res.model}  [{gpu_label}]  —  running…")
            lbl.setStyleSheet("color: #60a5fa; font-size: 13px;")
            ncl.addWidget(lbl)
        else:
            new_card = BenchResultCard(res, is_winner=False, gpu_label=gpu_label)
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

        gpu_label = self._bench_selected_gpu_label()
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
                        new_card = BenchResultCard(res, is_winner=True, gpu_label=gpu_label)
                        self._bench_result_cards[i] = new_card
                        self.bench_results_layout.insertWidget(pos, new_card)
            self.bench_summary_lbl.setText(
                f"Fastest: {winner.model}  ({winner.tokens_per_sec:.1f} tok/s)  "
                f"on {gpu_label}"
            )

        n_ok  = sum(1 for r in self._bench_results if r.status == "done")
        n_err = sum(1 for r in self._bench_results if r.status == "error")
        self._set_status(
            f"Benchmark complete — {n_ok} succeeded, {n_err} failed.")

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
