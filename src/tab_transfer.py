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
    QTableView, QTextEdit, QVBoxLayout, QWidget,
)
from utils import (
    human_bytes, parse_time, safe_mkdir, default_models_dir_guess, get_system_stats,
    blob_filename_from_digest,
)
from workers import (
    Worker, PullWorker, ExportImportWorker, start_worker,
    RegistryFetchWorker, BenchmarkWorker, BenchResult,
    ResourceMonitorWorker, DiskAnalysisWorker,
)
from widgets import (
    SectionLabel, Separator, StatCard, GaugeBar, GaugeCard,
    MonitorSparkWidget, ModelfileHighlighter, RegistryModelCard,
    TagsDialog, BenchResultCard, ConfirmDeleteDialog, prompt_text,
)
from models import (
    InstalledModelRow, RunningModelRow,
    InstalledModelsTableModel, RunningModelsTableModel,
    compute_export_files_for_models,
)


class TransferMixin:
    def _tab_transfer(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # ── Pull ────────────────────────────────
        pull_group = QGroupBox("Pull Model")
        pull_layout = QVBoxLayout(pull_group)
        pull_layout.setSpacing(10)

        pull_row = QHBoxLayout()
        pull_model_label = QLabel("Model name:")
        pull_model_label.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 600;")
        pull_model_label.setFixedWidth(90)

        self.pull_model_name = QLineEdit()
        self.pull_model_name.setPlaceholderText("e.g. llama3.2, mistral:7b, gemma3:latest")
        self.pull_model_name.returnPressed.connect(self.on_pull)

        self.pull_insecure = QCheckBox("Allow insecure")

        self.pull_btn = QPushButton("Pull Model")
        self.pull_btn.setObjectName("btn_primary")
        self.pull_btn.setMinimumWidth(110)
        self.pull_btn.clicked.connect(self.on_pull)

        self.pull_cancel_btn = QPushButton("Cancel")
        self.pull_cancel_btn.setObjectName("btn_cancel")
        self.pull_cancel_btn.setMinimumWidth(80)
        self.pull_cancel_btn.clicked.connect(self.on_pull_cancel)
        self.pull_cancel_btn.setEnabled(False)

        pull_row.addWidget(pull_model_label)
        pull_row.addWidget(self.pull_model_name, 1)
        pull_row.addWidget(self.pull_insecure)
        pull_row.addWidget(self.pull_btn)
        pull_row.addWidget(self.pull_cancel_btn)
        pull_layout.addLayout(pull_row)

        self.pull_progress = QProgressBar()
        self.pull_progress.setRange(0, 100)
        self.pull_progress.setValue(0)
        self.pull_progress.setFormat("Ready")
        pull_layout.addWidget(self.pull_progress)

        self.pull_log = QTextEdit()
        self.pull_log.setReadOnly(True)
        self.pull_log.setPlaceholderText("Pull output will stream here…")
        self.pull_log.setMaximumHeight(160)
        pull_layout.addWidget(self.pull_log)

        layout.addWidget(pull_group)

        # ── Backup / Restore ────────────────────
        backup_group = QGroupBox("Backup & Restore")
        backup_layout = QVBoxLayout(backup_group)
        backup_layout.setSpacing(10)

        # Models dir row
        dir_row = QHBoxLayout()
        dir_label = QLabel("Models dir:")
        dir_label.setStyleSheet("color: #64748b; font-size: 12px; font-weight: 600;")
        dir_label.setFixedWidth(90)

        self.models_dir = QLineEdit(str(default_models_dir_guess()))
        self.models_dir.setPlaceholderText("Path to Ollama models directory")

        btn_browse = QPushButton("Browse…")
        btn_browse.setFixedWidth(90)
        btn_browse.clicked.connect(self.browse_models_dir)

        dir_row.addWidget(dir_label)
        dir_row.addWidget(self.models_dir, 1)
        dir_row.addWidget(btn_browse)
        backup_layout.addLayout(dir_row)

        # Action buttons
        action_row = QHBoxLayout()
        self.export_selected_btn = QPushButton("⬆  Export Selected…")
        self.export_selected_btn.setObjectName("btn_primary")
        self.export_selected_btn.clicked.connect(self.export_selected_zip)

        self.export_full_btn = QPushButton("⬆  Export All…")
        self.export_full_btn.clicked.connect(self.export_full_zip)

        self.import_btn = QPushButton("⬇  Import ZIP…")
        self.import_btn.clicked.connect(self.import_zip)

        action_row.addWidget(self.export_selected_btn)
        action_row.addWidget(self.export_full_btn)
        action_row.addWidget(self.import_btn)
        action_row.addStretch(1)
        backup_layout.addLayout(action_row)

        self.file_progress = QProgressBar()
        self.file_progress.setRange(0, 100)
        self.file_progress.setValue(0)
        self.file_progress.setFormat("Idle")
        backup_layout.addWidget(self.file_progress)

        self.file_log = QTextEdit()
        self.file_log.setReadOnly(True)
        self.file_log.setPlaceholderText("Export / import log will appear here…")
        self.file_log.setMaximumHeight(160)
        backup_layout.addWidget(self.file_log)

        layout.addWidget(backup_group)
        layout.addStretch(1)
        return container

    # ── Status helpers ───────────────────────


    def on_pull(self):
        model = self.pull_model_name.text().strip()
        if not model:
            self._warn("Missing Model", "Enter a model name to pull (e.g. llama3.2 or gemma3:latest).")
            return

        if self._pull_thread is not None and self._pull_thread.isRunning():
            self._warn("Pull In Progress", "A pull is already running. Cancel it first.")
            return

        self.pull_log.clear()
        self.pull_progress.setValue(0)
        self.pull_progress.setFormat("Starting…")
        self.pull_btn.setEnabled(False)
        self.pull_cancel_btn.setEnabled(True)
        self._set_status(f"Pulling {model}…")

        worker = PullWorker(self.client, model=model, insecure=self.pull_insecure.isChecked())
        worker.progress.connect(self._on_pull_progress)
        worker.finished.connect(self._on_pull_finished)
        worker.failed.connect(self._on_pull_failed)

        self._pull_worker = worker
        self._pull_thread = self._start_worker(worker)


    def on_pull_cancel(self):
        if self._pull_worker:
            self._pull_worker.stop()
            self.pull_log.append("[Cancellation requested — server may continue in background]")
            self._set_status("Cancelling pull…")


    def _on_pull_progress(self, obj: dict):
        status = obj.get("status", "")
        completed = obj.get("completed")
        total = obj.get("total")

        if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and total > 0:
            pct = int((completed / total) * 100)
            self.pull_progress.setValue(max(0, min(100, pct)))
            self.pull_progress.setFormat(
                f"{pct}%  —  {human_bytes(int(completed))} / {human_bytes(int(total))}"
            )
        else:
            if status:
                self.pull_progress.setFormat(status)

        if status:
            line = status
            if completed and total:
                line += f"  ({human_bytes(int(completed))} / {human_bytes(int(total))})"
            self.pull_log.append(line)

        if status == "success":
            self.pull_progress.setValue(100)


    def _on_pull_finished(self, last_obj):
        self.pull_btn.setEnabled(True)
        self.pull_cancel_btn.setEnabled(False)
        self.pull_log.append("\n── Pull complete ──")
        self.pull_progress.setFormat("Complete")
        self._set_status("Pull finished.")
        self.refresh_installed()
        self._pull_worker = None
        self._pull_thread = None


    def _on_pull_failed(self, msg: str):
        self.pull_btn.setEnabled(True)
        self.pull_cancel_btn.setEnabled(False)
        self.pull_progress.setFormat("Failed")
        self._err("Pull Failed", msg)
        self._set_status("Pull failed.")
        self._pull_worker = None
        self._pull_thread = None

    # ── Backup / Restore ─────────────────────


    def browse_models_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Select Ollama Models Directory",
            self.models_dir.text().strip() or str(Path.home()),
        )
        if d:
            self.models_dir.setText(d)


    def _get_models_dir(self) -> Path:
        return Path(self.models_dir.text().strip()).expanduser()


    def _default_zip_name(self, prefix: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str((Path.home() / f"{prefix}_{ts}.zip").resolve())


    def export_selected_zip(self):
        names = self.installed_selected_names()
        if not names:
            self._warn("No Selection", "Select one or more models in the Installed Models tab first.")
            return

        models_dir = self._get_models_dir()
        if not models_dir.exists():
            self._err("Directory Not Found", f"Models directory does not exist:\n\n{models_dir}")
            return

        suggested = self._default_zip_name("ollama_selected_backup")
        out, _ = QFileDialog.getSaveFileName(self, "Save Backup ZIP", suggested, "Zip archives (*.zip)")
        if not out:
            return

        pairs, meta = compute_export_files_for_models(models_dir, names)
        if meta["missing_manifests"]:
            self._warn(
                "Some Manifests Missing",
                "Some selected models could not be resolved to manifest files.\n"
                "Those models will NOT be exported.\n\n"
                + "\n".join(meta["missing_manifests"][:20])
            )

        if not pairs:
            self._err("Export Failed", "No files were resolved for export.")
            return

        self._run_file_worker(ExportImportWorker(
            mode="export_selected",
            models_dir=models_dir,
            zip_path=Path(out),
            model_names=names,
        ))


    def export_full_zip(self):
        models_dir = self._get_models_dir()
        if not models_dir.exists():
            self._err("Directory Not Found", f"Models directory does not exist:\n\n{models_dir}")
            return

        suggested = self._default_zip_name("ollama_full_store_backup")
        out, _ = QFileDialog.getSaveFileName(self, "Save Full Backup ZIP", suggested, "Zip archives (*.zip)")
        if not out:
            return

        self._run_file_worker(ExportImportWorker(
            mode="export_full",
            models_dir=models_dir,
            zip_path=Path(out),
        ))


    def import_zip(self):
        models_dir = self._get_models_dir()
        safe_mkdir(models_dir)

        z, _ = QFileDialog.getOpenFileName(self, "Select Backup ZIP to Import", str(Path.home()), "Zip archives (*.zip)")
        if not z:
            return

        typed, ok = prompt_text(
            self, "Confirm Import",
            "This will merge manifests and blobs into your models directory.\n\nType IMPORT to proceed:",
            "IMPORT",
        )
        if not ok or typed.strip() != "IMPORT":
            return

        self._run_file_worker(ExportImportWorker(
            mode="import_zip",
            models_dir=models_dir,
            zip_path=Path(z),
        ))


    def _run_file_worker(self, worker: ExportImportWorker):
        self.file_log.clear()
        self.file_progress.setValue(0)
        self.file_progress.setFormat("Working…")

        worker.progress.connect(self._on_file_progress)
        worker.finished.connect(self._on_file_finished)
        worker.failed.connect(lambda msg: self._err("Operation Failed", msg))
        self._start_worker(worker)


    def _on_file_progress(self, done: int, total: int, msg: str):
        if total > 0:
            pct = int((done / total) * 100)
            self.file_progress.setValue(max(0, min(100, pct)))
            self.file_progress.setFormat(f"{pct}%  ({done}/{total})")
        self.file_log.append(msg)


    def _on_file_finished(self, payload: dict):
        self.file_progress.setValue(100)
        self.file_progress.setFormat("Complete")
        mode = payload.get("mode", "import_zip")

        if "zip" in payload:
            self.file_log.append(f"\n── Export complete ──\n{payload.get('zip')}")
            self._set_status("Export complete.")
            self._info("Export Complete", f"Backup saved to:\n\n{payload.get('zip')}")
        else:
            self.file_log.append(f"\n── Import complete ──\n{payload.get('imported_files', 0)} files")
            self._set_status("Import complete.")
            self._info("Import Complete", f"Models installed to:\n\n{payload.get('models_dir')}\n\nRefreshing model list…")
            self.refresh_installed()

    # ── Registry Tab ─────────────────────────────
