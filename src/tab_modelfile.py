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


class ModelfileMixin:
    _MODELFILE_TEMPLATE = 'FROM {base_model}\n\n# System prompt\nSYSTEM """\nYou are a helpful assistant.\n"""\n\n# Inference parameters\nPARAMETER temperature 0.7\nPARAMETER top_p 0.9\nPARAMETER top_k 40\nPARAMETER num_ctx 4096\nPARAMETER repeat_penalty 1.1\n'

    def _tab_modelfile(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        tb = QHBoxLayout(); tb.setSpacing(10)
        base_lbl = QLabel("Base model:")
        base_lbl.setStyleSheet("color:#64748b; font-size:12px; font-weight:600;")
        self.mf_base_combo = QComboBox()
        self.mf_base_combo.setMinimumWidth(220)
        self.mf_base_combo.setEditable(True)
        self.mf_base_combo.lineEdit().setPlaceholderText("Select or type base model…")
        self.mf_base_combo.currentTextChanged.connect(self._mf_update_base)

        name_lbl = QLabel("New name:")
        name_lbl.setStyleSheet("color:#64748b; font-size:12px; font-weight:600;")
        self.mf_name_input = QLineEdit()
        self.mf_name_input.setPlaceholderText("e.g. mymodel:custom")
        self.mf_name_input.setMaximumWidth(200)
        self.mf_name_input.textChanged.connect(lambda _: self._mf_update_preview())

        self.mf_create_btn = QPushButton("Create Model")
        self.mf_create_btn.setObjectName("btn_primary")
        self.mf_create_btn.setMinimumWidth(130)
        self.mf_create_btn.clicked.connect(self._mf_create)

        self.mf_reset_btn = QPushButton("Reset")
        self.mf_reset_btn.clicked.connect(self._mf_reset)
        self.mf_load_btn = QPushButton("Load File…")
        self.mf_load_btn.clicked.connect(self._mf_load_file)
        self.mf_save_btn = QPushButton("Save File…")
        self.mf_save_btn.clicked.connect(self._mf_save_file)

        tb.addWidget(base_lbl); tb.addWidget(self.mf_base_combo)
        tb.addSpacing(8); tb.addWidget(name_lbl); tb.addWidget(self.mf_name_input)
        tb.addStretch(1)
        for b in (self.mf_reset_btn, self.mf_load_btn, self.mf_save_btn, self.mf_create_btn):
            tb.addWidget(b)
        layout.addLayout(tb)
        layout.addWidget(Separator())

        # Quick param controls
        params_group = QGroupBox("Quick Parameters")
        pl = QHBoxLayout(params_group); pl.setSpacing(18)
        self._mf_param_widgets: dict = {}
        for name, lo, hi, default, decs in [
            ("temperature", 0.0, 2.0, 0.7, 2),
            ("top_p",       0.0, 1.0, 0.9, 2),
            ("top_k",       1,   200, 40,  0),
            ("num_ctx",     512, 32768, 4096, 0),
            ("repeat_penalty", 0.5, 2.0, 1.1, 2),
        ]:
            col = QVBoxLayout(); col.setSpacing(3)
            lbl = QLabel(name)
            lbl.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fmt = f"{default:.{decs}f}" if decs else str(int(default))
            val_lbl = QLabel(fmt)
            val_lbl.setStyleSheet("color:#e2e8f0; font-size:14px; font-weight:700;")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(lbl); col.addWidget(val_lbl)
            pl.addLayout(col)
            self._mf_param_widgets[name] = (val_lbl, lo, hi, default, decs)
        pl.addStretch(1)
        sync_btn = QPushButton("↓  Sync to Editor")
        sync_btn.setFixedHeight(28)
        sync_btn.setStyleSheet("font-size:11px;")
        sync_btn.clicked.connect(self._mf_sync_params)
        pl.addWidget(sync_btn)
        layout.addWidget(params_group)

        # Editor / preview splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)

        ew = QWidget(); el = QVBoxLayout(ew)
        el.setContentsMargins(0, 0, 0, 0); el.setSpacing(4)
        el.addWidget(SectionLabel("Modelfile"))
        self.mf_editor = QTextEdit()
        self.mf_editor.setObjectName("modelfile_editor")
        self._mf_highlighter = ModelfileHighlighter(self.mf_editor.document())
        self.mf_editor.textChanged.connect(self._mf_on_edit)
        el.addWidget(self.mf_editor)

        pw = QWidget(); pll = QVBoxLayout(pw)
        pll.setContentsMargins(0, 0, 0, 0); pll.setSpacing(4)
        pll.addWidget(SectionLabel("Create Command Preview"))
        self.mf_preview = QTextEdit()
        self.mf_preview.setObjectName("modelfile_preview")
        self.mf_preview.setReadOnly(True)
        pll.addWidget(self.mf_preview)

        splitter.addWidget(ew); splitter.addWidget(pw)
        splitter.setSizes([700, 340])
        layout.addWidget(splitter)

        self.mf_status_lbl = QLabel("")
        self.mf_status_lbl.setObjectName("label_muted")
        layout.addWidget(self.mf_status_lbl)

        self._mf_populate_combo()
        self._mf_reset()
        return container


    def _mf_populate_combo(self):
        current = self.mf_base_combo.currentText()
        self.mf_base_combo.blockSignals(True)
        self.mf_base_combo.clear()
        for row in self.installed_model.rows():
            self.mf_base_combo.addItem(row.name)
        if current:
            idx = self.mf_base_combo.findText(current)
            if idx >= 0:
                self.mf_base_combo.setCurrentIndex(idx)
        self.mf_base_combo.blockSignals(False)


    def _mf_reset(self):
        base = self.mf_base_combo.currentText().strip() or "llama3.2"
        text = self._MODELFILE_TEMPLATE.replace("{base_model}", base)
        self.mf_editor.blockSignals(True)
        self.mf_editor.setPlainText(text)
        self.mf_editor.blockSignals(False)
        self._mf_update_preview()


    def _mf_update_base(self, text: str):
        if not text:
            return
        content = self.mf_editor.toPlainText()
        content = re.sub(r"^FROM\s+\S+", f"FROM {text}", content, flags=re.MULTILINE)
        if not re.search(r"^FROM\s", content, re.MULTILINE):
            content = f"FROM {text}\n" + content
        self.mf_editor.blockSignals(True)
        self.mf_editor.setPlainText(content)
        self.mf_editor.blockSignals(False)
        self._mf_update_preview()


    def _mf_on_edit(self):
        self._mf_update_preview()


    def _mf_update_preview(self):
        name = self.mf_name_input.text().strip() or "<model-name>"
        body = self.mf_editor.toPlainText()[:500]
        self.mf_preview.setPlainText(
            f"# ollama create {name} -f ./Modelfile\n\n"
            f"# Content preview:\n{body}"
        )


    def _mf_sync_params(self):
        content = self.mf_editor.toPlainText()
        lines = [l for l in content.splitlines()
                 if not l.strip().startswith("PARAMETER ")]
        param_lines = [f"PARAMETER {n} {w[0].text()}"
                       for n, w in self._mf_param_widgets.items()]
        self.mf_editor.setPlainText(
            "\n".join(lines) + "\n" + "\n".join(param_lines))


    def _mf_load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Modelfile", str(Path.home()),
            "Modelfile (*Modelfile *.txt *.modelfile)"
        )
        if path:
            try:
                self.mf_editor.setPlainText(Path(path).read_text(encoding="utf-8"))
                self.mf_status_lbl.setText(f"Loaded: {path}")
            except Exception as e:
                self._err("Load Failed", str(e))


    def _mf_save_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Modelfile", str(Path.home() / "Modelfile"), "Modelfile (*)"
        )
        if path:
            try:
                Path(path).write_text(self.mf_editor.toPlainText(), encoding="utf-8")
                self.mf_status_lbl.setText(f"Saved: {path}")
            except Exception as e:
                self._err("Save Failed", str(e))


    def _mf_create(self):
        name = self.mf_name_input.text().strip()
        if not name:
            self._warn("Missing Name", "Enter a name for the new model (e.g. mymodel:v1).")
            return
        content = self.mf_editor.toPlainText().strip()
        if not content:
            self._warn("Empty Modelfile", "The Modelfile editor is empty.")
            return
        typed, ok = prompt_text(
            self, "Confirm Create",
            f"Run  ollama create {name}  from the current Modelfile?\n"
            "Type CREATE to proceed:", "CREATE"
        )
        if not ok or typed.strip() != "CREATE":
            return

        import tempfile
        self.mf_status_lbl.setText(f"Creating {name}…")
        self._set_status(f"Running ollama create {name}…")
        worker = Worker()

        def run():
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".Modelfile", delete=False, encoding="utf-8"
                ) as tf:
                    tf.write(content)
                    tf_path = tf.name
                result = subprocess.run(
                    ["ollama", "create", name, "-f", tf_path],
                    capture_output=True, text=True, timeout=120
                )
                os.unlink(tf_path)
                if result.returncode == 0:
                    worker.finished.emit({"ok": True, "name": name})
                else:
                    worker.finished.emit(
                        {"ok": False, "name": name,
                         "err": result.stderr or result.stdout})
            except Exception as e:
                worker.failed.emit(str(e))

        worker.run = run

        def on_done(payload):
            if payload.get("ok"):
                self.mf_status_lbl.setText(f"✓ Created {payload['name']}")
                self._set_status(f"Model created: {payload['name']}")
                self._info("Created", f"Model '{payload['name']}' created successfully.")
                self.refresh_installed()
            else:
                self.mf_status_lbl.setText("Creation failed")
                self._err("Create Failed", payload.get("err", "Unknown error"))

        worker.finished.connect(on_done)
        worker.failed.connect(lambda msg: (
            self._err("Create Failed", msg),
            self.mf_status_lbl.setText("Failed")
        ))
        self._start_worker(worker)

    # ══════════════════════════════════════════
    # DISK ANALYSER TAB
    # ══════════════════════════════════════════
