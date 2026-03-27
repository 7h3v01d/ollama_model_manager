"""
Mixin — imported via multiple inheritance into MainWindow.
All methods reference self which is a MainWindow instance.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

import json, os, re, subprocess, sys, time, zipfile, tempfile
from logger import log
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


class DiskMixin:
    def _tab_disk(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        tb = QHBoxLayout()
        dir_lbl = QLabel("Models dir:")
        dir_lbl.setStyleSheet("color:#64748b; font-size:12px; font-weight:600;")
        dir_lbl.setFixedWidth(90)
        self.disk_dir_input = QLineEdit(str(default_models_dir_guess()))
        browse_btn = QPushButton("Browse…"); browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._disk_browse)
        self.disk_scan_btn = QPushButton("⬡  Scan Disk")
        self.disk_scan_btn.setObjectName("btn_primary")
        self.disk_scan_btn.setMinimumWidth(120)
        self.disk_scan_btn.clicked.connect(self._disk_scan)
        self.disk_status_lbl = QLabel("Click Scan to analyse your models directory")
        self.disk_status_lbl.setObjectName("label_muted")

        tb.addWidget(dir_lbl); tb.addWidget(self.disk_dir_input, 1)
        tb.addWidget(browse_btn); tb.addSpacing(8); tb.addWidget(self.disk_scan_btn)
        tb.addStretch(1); tb.addWidget(self.disk_status_lbl)
        layout.addLayout(tb)
        layout.addWidget(Separator())

        stat_row = QHBoxLayout(); stat_row.setSpacing(12)
        self.disk_stat_total   = StatCard("Total Used")
        self.disk_stat_blobs   = StatCard("Blobs")
        self.disk_stat_models  = StatCard("Models")
        self.disk_stat_orphans = StatCard("Orphans")
        for sc in (self.disk_stat_total, self.disk_stat_blobs,
                   self.disk_stat_models, self.disk_stat_orphans):
            stat_row.addWidget(sc)
        stat_row.addStretch(1)
        layout.addLayout(stat_row)
        layout.addWidget(Separator())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.disk_content_widget = QWidget()
        self.disk_content_layout = QVBoxLayout(self.disk_content_widget)
        self.disk_content_layout.setContentsMargins(0, 0, 8, 0)
        self.disk_content_layout.setSpacing(6)
        self.disk_content_layout.addStretch(1)
        scroll.setWidget(self.disk_content_widget)
        layout.addWidget(scroll)
        return container


    def _disk_browse(self):
        d = QFileDialog.getExistingDirectory(
            self, "Select Ollama Models Directory",
            self.disk_dir_input.text().strip() or str(Path.home()))
        if d:
            self.disk_dir_input.setText(d)


    def _disk_scan(self):
        models_dir = Path(self.disk_dir_input.text().strip()).expanduser()
        log.info("_disk_scan: requested  dir=%s", models_dir)
        if not models_dir.exists():
            log.warning("_disk_scan: directory does not exist: %s", models_dir)
            self._err("Not Found", f"Directory does not exist:\n{models_dir}")
            return
        self.disk_scan_btn.setEnabled(False)
        self.disk_status_lbl.setText("Scanning…")
        self._set_status("Analysing disk usage…")
        installed = [r.name for r in self.installed_model.rows()]
        log.info("_disk_scan: launching worker  installed_models=%d", len(installed))
        worker = DiskAnalysisWorker(models_dir, installed)
        worker.finished.connect(self._disk_on_result)
        worker.failed.connect(self._disk_on_fail)
        # Keep a strong Python reference so the worker isn't GC'd before
        # the thread fires started — local variables get collected immediately
        # after _start_worker() returns on CPython/Windows.
        self._disk_worker = worker
        worker.finished.connect(lambda _: setattr(self, "_disk_worker", None))
        worker.failed.connect(lambda _: setattr(self, "_disk_worker", None))
        self._start_worker(worker)

    def _disk_on_fail(self, msg: str):
        log.error("_disk_scan: worker failed: %s", msg)
        self._err("Scan Failed", msg)
        self.disk_scan_btn.setEnabled(True)
        self.disk_status_lbl.setText("Scan failed")


    def _disk_on_result(self, result: dict):
        log.info(
            "_disk_on_result: received result  type=%s  keys=%s",
            type(result).__name__,
            list(result.keys()) if isinstance(result, dict) else "NOT A DICT"
        )
        if not isinstance(result, dict):
            log.error("_disk_on_result: result is not a dict — got %r", result)
            self._err("Scan Error", f"Internal error: unexpected result type {type(result).__name__}\n\nCheck ollama_manager.log for details.")
            self.disk_scan_btn.setEnabled(True)
            self.disk_status_lbl.setText("Scan failed — see log")
            return
        self._disk_result = result
        self.disk_scan_btn.setEnabled(True)
        orphans = result["orphan_blobs"]
        orphan_sz = sum(o["size"] for o in orphans)
        self.disk_stat_total.set_value(human_bytes(result["total_size"]))
        self.disk_stat_blobs.set_value(human_bytes(result["blobs_dir_size"]))
        self.disk_stat_models.set_value(str(len(result["per_model"])))
        self.disk_stat_orphans.set_value(str(len(orphans)))
        status = (f"Scanned — {len(result['per_model'])} models, "
                  f"{len(orphans)} orphan(s)")
        if orphans:
            status += f"  ({human_bytes(orphan_sz)} reclaimable)"
        self.disk_status_lbl.setText(status)
        self._set_status(status)
        self._disk_render(result)


    def _disk_render(self, result: dict):
        while self.disk_content_layout.count() > 1:
            item = self.disk_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        total = result["total_size"] or 1

        per_model = sorted(result["per_model"].items(),
                           key=lambda kv: kv[1]["size"], reverse=True)
        if per_model:
            self.disk_content_layout.insertWidget(
                self.disk_content_layout.count() - 1,
                SectionLabel(f"Per-model  ({len(per_model)} models)"))
            for model_name, info in per_model:
                sz = info["size"]
                pct = sz / total * 100
                row = QFrame(); row.setObjectName("disk_model_row")
                row.setFixedHeight(46)
                rl = QHBoxLayout(row); rl.setContentsMargins(14, 0, 14, 0)
                name_lbl = QLabel(model_name)
                name_lbl.setStyleSheet(
                    "color:#e2e8f0; font-size:13px; font-weight:600;")
                name_lbl.setMinimumWidth(240)
                rl.addWidget(name_lbl)
                bar = GaugeBar("", "#3b82f6")
                bar.set_value(pct)
                bar.setMinimumWidth(180)
                rl.addWidget(bar, 1)
                for text, style in [
                    (human_bytes(sz),
                     "color:#94a3b8; font-size:12px; font-weight:600; min-width:90px;"),
                    (f"{pct:.1f}%",
                     "color:#4b5563; font-size:11px; min-width:44px;"),
                ]:
                    lbl = QLabel(text); lbl.setStyleSheet(style)
                    lbl.setAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    rl.addWidget(lbl)
                self.disk_content_layout.insertWidget(
                    self.disk_content_layout.count() - 1, row)

        orphans = result["orphan_blobs"]
        if orphans:
            orphan_total = sum(o["size"] for o in orphans)
            hdr_w = QWidget()
            hdr_l = QHBoxLayout(hdr_w); hdr_l.setContentsMargins(0, 8, 0, 4)
            hdr_l.addWidget(SectionLabel(
                f"Orphaned Blobs  ({len(orphans)}, "
                f"{human_bytes(orphan_total)} reclaimable)"))
            hdr_l.addStretch(1)
            purge_btn = QPushButton(
                f"✕  Delete All Orphans  ({human_bytes(orphan_total)})")
            purge_btn.setObjectName("btn_danger")
            purge_btn.clicked.connect(self._disk_purge_orphans)
            hdr_l.addWidget(purge_btn)
            self.disk_content_layout.insertWidget(
                self.disk_content_layout.count() - 1, hdr_w)
            for o in orphans[:50]:
                blob_path = o["path"]
                row = QFrame(); row.setObjectName("orphan_row")
                row.setFixedHeight(38)
                rl = QHBoxLayout(row); rl.setContentsMargins(14, 0, 14, 0)
                rl.addWidget(QLabel(blob_path.name[:52]))
                rl.itemAt(0).widget().setStyleSheet(
                    "color:#f87171; font-size:12px; font-family:monospace;")
                rl.addStretch(1)
                sz_lbl = QLabel(human_bytes(o["size"]))
                sz_lbl.setStyleSheet(
                    "color:#9ca3af; font-size:12px; min-width:80px;")
                sz_lbl.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                rl.addWidget(sz_lbl)
                self.disk_content_layout.insertWidget(
                    self.disk_content_layout.count() - 1, row)
        elif not per_model:
            lbl = QLabel("Nothing found — check the models directory path.")
            lbl.setObjectName("label_muted")
            self.disk_content_layout.insertWidget(
                self.disk_content_layout.count() - 1, lbl)


    def _disk_purge_orphans(self):
        orphans = self._disk_result.get("orphan_blobs", [])
        if not orphans:
            return
        total_sz = sum(o["size"] for o in orphans)
        typed, ok = prompt_text(
            self, "Confirm Purge",
            f"Permanently delete {len(orphans)} orphaned blob(s) "
            f"({human_bytes(total_sz)}) not referenced by any model?\n\n"
            "Type PURGE to confirm:", "PURGE"
        )
        if not ok or typed.strip() != "PURGE":
            return
        self.disk_status_lbl.setText("Purging orphans…")
        worker = Worker()

        def run():
            deleted, failed = 0, []
            for o in orphans:
                try:
                    Path(o["path"]).unlink(); deleted += 1
                except Exception as e:
                    failed.append(f"{o['path'].name}: {e}")
            worker.finished.emit({"deleted": deleted, "failed": failed,
                                  "freed": total_sz})

        worker.run = run

        def on_done(payload):
            if payload["failed"]:
                self._warn("Some Deletions Failed",
                           "\n".join(payload["failed"][:10]))
            self._info("Purge Complete",
                       f"Deleted {payload['deleted']} blob(s), "
                       f"freed {human_bytes(payload['freed'])}.")
            self.disk_status_lbl.setText(
                f"Purged {payload['deleted']} orphan(s)")
            self._disk_scan()

        worker.finished.connect(on_done)
        worker.failed.connect(lambda msg: self._err("Purge Failed", msg))
        self._start_worker(worker)


    # ── Shutdown ─────────────────────────────
