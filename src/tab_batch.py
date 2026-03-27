"""
Mixin — Batch Prompt Runner tab.

Load prompts from JSONL / CSV / plain-text, run them against a
selected model, stream results row by row, then export to
JSONL or CSV.

Input formats
-------------
  JSONL  — one JSON object per line: {"prompt": "...", "system": "..."}
  CSV    — columns: prompt  (+ optional: system, tags, notes)
  TXT    — one prompt per non-blank line

Output format (JSONL or CSV)
----------------------------
  prompt, system, model, response, status, error,
  prompt_tokens, eval_tokens, tokens_per_sec, total_ms
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

import csv
import io
import json
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from logger import log
from workers import BatchRunWorker, BatchRunResult
from widgets import SectionLabel, Separator


# ── Result table model ────────────────────────────────────────────────────

COLUMNS = [
    ("Row",      50,  False),
    ("Status",   72,  False),
    ("Prompt",   260, True),
    ("Response", 340, True),
    ("Tok/s",    60,  False),
    ("Tokens",   62,  False),
    ("ms",       58,  False),
    ("Error",    160, True),
]
COL_IDX = {name: i for i, (name, _, _) in enumerate(COLUMNS)}

STATUS_STYLES = {
    "pending": "color:#374151;",
    "running": "color:#f59e0b; font-weight:700;",
    "done":    "color:#10b981; font-weight:700;",
    "error":   "color:#f87171; font-weight:700;",
}


def _item(text: str, style: str = "") -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if style:
        # Parse basic color from style string
        if "color:" in style:
            from PyQt6.QtGui import QColor
            col = style.split("color:")[1].split(";")[0].strip()
            it.setForeground(QColor(col))
    return it


# ── Batch Mixin ───────────────────────────────────────────────────────────

class BatchMixin:

    def _tab_batch(self) -> QWidget:
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ── Config row ────────────────────────────────────────────────
        cfg_group = QFrame()
        cfg_group.setStyleSheet(
            "QFrame { background:#111827; border:1px solid #1e2533; "
            "border-radius:10px; }")
        cfg_l = QVBoxLayout(cfg_group)
        cfg_l.setContentsMargins(16, 14, 16, 14)
        cfg_l.setSpacing(10)

        # Row 1: model + system prompt
        r1 = QHBoxLayout()
        r1.setSpacing(12)

        model_lbl = QLabel("MODEL")
        model_lbl.setStyleSheet(
            "color:#4b5563; font-size:11px; font-weight:700; letter-spacing:0.5px;")
        model_lbl.setFixedWidth(52)
        r1.addWidget(model_lbl)

        self.batch_model_combo = QComboBox()
        self.batch_model_combo.setMinimumWidth(220)
        self.batch_model_combo.setPlaceholderText("Connect to Ollama first…")
        self._batch_populate_models()
        r1.addWidget(self.batch_model_combo)

        r1.addWidget(Separator(vertical=True))

        temp_lbl = QLabel("TEMP")
        temp_lbl.setStyleSheet(
            "color:#4b5563; font-size:11px; font-weight:700; letter-spacing:0.5px;")
        self.batch_temp = QLineEdit("0.7")
        self.batch_temp.setFixedWidth(50)
        self.batch_temp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        r1.addWidget(temp_lbl)
        r1.addWidget(self.batch_temp)

        ctx_lbl = QLabel("CTX")
        ctx_lbl.setStyleSheet(
            "color:#4b5563; font-size:11px; font-weight:700; letter-spacing:0.5px;")
        self.batch_ctx = QLineEdit("4096")
        self.batch_ctx.setFixedWidth(60)
        self.batch_ctx.setAlignment(Qt.AlignmentFlag.AlignCenter)
        r1.addWidget(ctx_lbl)
        r1.addWidget(self.batch_ctx)
        r1.addStretch(1)
        cfg_l.addLayout(r1)

        # Row 2: global system prompt
        r2 = QHBoxLayout()
        r2.setSpacing(12)
        sys_lbl = QLabel("SYSTEM")
        sys_lbl.setStyleSheet(
            "color:#4b5563; font-size:11px; font-weight:700; letter-spacing:0.5px;")
        sys_lbl.setFixedWidth(52)
        self.batch_system = QLineEdit()
        self.batch_system.setPlaceholderText(
            "Global system prompt (overridden per-row if 'system' column present)…")
        r2.addWidget(sys_lbl)
        r2.addWidget(self.batch_system, 1)
        cfg_l.addLayout(r2)

        root.addWidget(cfg_group)

        # ── Input toolbar ─────────────────────────────────────────────
        in_tb = QHBoxLayout()
        in_tb.setSpacing(8)

        self.batch_load_btn = QPushButton("⬆  Load File…")
        self.batch_load_btn.setObjectName("btn_primary")
        self.batch_load_btn.setMinimumWidth(120)
        self.batch_load_btn.clicked.connect(self._batch_load_file)

        self.batch_paste_btn = QPushButton("Paste JSONL")
        self.batch_paste_btn.setMinimumWidth(100)
        self.batch_paste_btn.clicked.connect(self._batch_paste)

        self.batch_clear_btn = QPushButton("Clear")
        self.batch_clear_btn.setMinimumWidth(70)
        self.batch_clear_btn.clicked.connect(self._batch_clear)

        self.batch_run_btn = QPushButton("▶  Run Batch")
        self.batch_run_btn.setObjectName("btn_primary")
        self.batch_run_btn.setMinimumWidth(110)
        self.batch_run_btn.clicked.connect(self._batch_run)

        self.batch_stop_btn = QPushButton("■  Stop")
        self.batch_stop_btn.setObjectName("btn_cancel")
        self.batch_stop_btn.setMinimumWidth(80)
        self.batch_stop_btn.setEnabled(False)
        self.batch_stop_btn.clicked.connect(self._batch_stop)

        self.batch_export_btn = QPushButton("⬇  Export…")
        self.batch_export_btn.setMinimumWidth(90)
        self.batch_export_btn.setEnabled(False)
        self.batch_export_btn.clicked.connect(self._batch_export)

        self.batch_status_lbl = QLabel("")
        self.batch_status_lbl.setObjectName("label_muted")

        in_tb.addWidget(self.batch_load_btn)
        in_tb.addWidget(self.batch_paste_btn)
        in_tb.addWidget(self.batch_clear_btn)
        in_tb.addWidget(Separator(vertical=True))
        in_tb.addWidget(self.batch_run_btn)
        in_tb.addWidget(self.batch_stop_btn)
        in_tb.addWidget(Separator(vertical=True))
        in_tb.addWidget(self.batch_export_btn)
        in_tb.addStretch(1)
        in_tb.addWidget(self.batch_status_lbl)
        root.addLayout(in_tb)

        # ── Progress bar ──────────────────────────────────────────────
        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, 100)
        self.batch_progress.setValue(0)
        self.batch_progress.setFormat("Idle")
        root.addWidget(self.batch_progress)

        # ── Splitter: results table | prompt editor ───────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(1)

        # Results table
        self.batch_table = QTableWidget()
        self.batch_table.setColumnCount(len(COLUMNS))
        self.batch_table.setHorizontalHeaderLabels(
            [name for name, _, _ in COLUMNS])
        self.batch_table.setAlternatingRowColors(True)
        self.batch_table.setShowGrid(False)
        self.batch_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.batch_table.verticalHeader().setDefaultSectionSize(34)
        self.batch_table.verticalHeader().hide()
        self.batch_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hh = self.batch_table.horizontalHeader()
        for i, (_, width, stretch) in enumerate(COLUMNS):
            if stretch:
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                self.batch_table.setColumnWidth(i, width)
        self.batch_table.itemSelectionChanged.connect(
            self._batch_on_row_selected)
        splitter.addWidget(self.batch_table)

        # Bottom pane: prompt editor + full response
        bottom = QWidget()
        bottom.setMinimumHeight(160)
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(0, 8, 0, 0)
        bl.setSpacing(8)

        prompt_pane = QWidget()
        pl = QVBoxLayout(prompt_pane)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        pl.addWidget(SectionLabel("Prompt editor  (editable before run)"))
        self.batch_prompt_edit = QTextEdit()
        self.batch_prompt_edit.setPlaceholderText(
            "Type or paste a prompt here, then click '+ Add Row' to add it to the queue…")
        self.batch_prompt_edit.setStyleSheet(
            "QTextEdit { background:#080c13; border:1px solid #1e2533; "
            "border-radius:6px; color:#e2e8f0; font-size:12px; padding:8px; }")
        add_row_btn = QPushButton("+ Add Row")
        add_row_btn.setObjectName("btn_primary")
        add_row_btn.clicked.connect(self._batch_add_from_editor)
        pl.addWidget(self.batch_prompt_edit, 1)
        pl.addWidget(add_row_btn, 0, Qt.AlignmentFlag.AlignRight)
        bl.addWidget(prompt_pane, 1)

        response_pane = QWidget()
        rl = QVBoxLayout(response_pane)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        rl.addWidget(SectionLabel("Response  (selected row)"))
        self.batch_response_view = QTextEdit()
        self.batch_response_view.setReadOnly(True)
        self.batch_response_view.setStyleSheet(
            "QTextEdit { background:#080c13; border:1px solid #1e2533; "
            "border-radius:6px; color:#6ee7b7; font-size:12px; padding:8px; "
            "font-family: 'Cascadia Code', 'Consolas', monospace; }")
        rl.addWidget(self.batch_response_view, 1)
        bl.addWidget(response_pane, 1)

        splitter.addWidget(bottom)
        splitter.setSizes([420, 200])
        root.addWidget(splitter, 1)

        # Internal state
        self._batch_rows: list[dict] = []           # input rows
        self._batch_results: list[BatchRunResult] = []
        self._batch_worker = None

        return container

    # ── Model population ──────────────────────────────────────────────

    def _batch_populate_models(self):
        current = self.batch_model_combo.currentText()
        self.batch_model_combo.blockSignals(True)
        self.batch_model_combo.clear()
        for row in self.installed_model.rows():
            self.batch_model_combo.addItem(row.name)
        if current:
            idx = self.batch_model_combo.findText(current)
            if idx >= 0:
                self.batch_model_combo.setCurrentIndex(idx)
        self.batch_model_combo.blockSignals(False)

    # ── Input loading ─────────────────────────────────────────────────

    def _batch_load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Prompts",
            str(Path.home()),
            "All supported (*.jsonl *.json *.csv *.txt);;"
            "JSONL (*.jsonl *.json);;CSV (*.csv);;Text (*.txt)"
        )
        if not path:
            return
        try:
            rows = self._batch_parse_file(Path(path))
            self._batch_set_rows(rows)
            self.batch_status_lbl.setText(
                f"Loaded {len(rows)} row(s) from {Path(path).name}")
            log.info("BatchMixin: loaded %d rows from %s", len(rows), path)
        except Exception as e:
            self._err("Load Failed", str(e))
            log.exception("BatchMixin: load failed: %s", e)

    def _batch_paste(self):
        """Open a dialog to paste JSONL or plain text directly."""
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Paste Prompts")
        dlg.setModal(True)
        dlg.resize(560, 400)
        dlg.setStyleSheet(
            "QDialog { background:#111827; border:1px solid #1e2533; }")
        l = QVBoxLayout(dlg)
        l.setContentsMargins(16, 16, 16, 12)
        l.setSpacing(10)
        hint = QLabel(
            "Paste JSONL  ({\"prompt\": \"...\"} per line), CSV with a 'prompt' column, "
            "or plain text (one prompt per line):")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#64748b; font-size:12px;")
        l.addWidget(hint)
        text_edit = QTextEdit()
        text_edit.setStyleSheet(
            "QTextEdit { background:#080c13; border:1px solid #1e2533; "
            "color:#e2e8f0; font-size:12px; padding:8px; "
            "font-family:'Cascadia Code','Consolas',monospace; }")
        l.addWidget(text_edit, 1)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        l.addWidget(btns)
        if dlg.exec() == dlg.DialogCode.Accepted:
            raw = text_edit.toPlainText().strip()
            if raw:
                try:
                    rows = self._batch_parse_text(raw)
                    self._batch_set_rows(rows)
                    self.batch_status_lbl.setText(
                        f"Pasted {len(rows)} row(s)")
                except Exception as e:
                    self._err("Parse Failed", str(e))

    def _batch_parse_file(self, path: Path) -> list[dict]:
        ext = path.suffix.lower()
        text = path.read_text(encoding="utf-8", errors="replace")
        if ext in (".jsonl", ".json"):
            return self._batch_parse_jsonl(text)
        elif ext == ".csv":
            return self._batch_parse_csv(text)
        else:
            return self._batch_parse_text(text)

    def _batch_parse_jsonl(self, text: str) -> list[dict]:
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, str):
                    rows.append({"prompt": obj})
                elif isinstance(obj, dict):
                    prompt = obj.get("prompt") or obj.get("input") or ""
                    if prompt:
                        rows.append({
                            "prompt": str(prompt).strip(),
                            "system": str(obj.get("system", "")).strip(),
                        })
            except json.JSONDecodeError:
                if line:
                    rows.append({"prompt": line})
        return rows

    def _batch_parse_csv(self, text: str) -> list[dict]:
        rows = []
        reader = csv.DictReader(io.StringIO(text))
        # Try common column name variants
        prompt_keys = ("prompt", "Prompt", "PROMPT", "input", "Input",
                       "question", "Question", "text", "Text")
        system_keys = ("system", "System", "SYSTEM", "system_prompt")
        for row_dict in reader:
            prompt = next(
                (row_dict[k].strip() for k in prompt_keys if k in row_dict),
                None)
            if not prompt:
                # Fall back to first column
                first_val = next(iter(row_dict.values()), "").strip()
                if first_val:
                    prompt = first_val
            if prompt:
                system = next(
                    (row_dict[k].strip() for k in system_keys if k in row_dict),
                    "")
                rows.append({"prompt": prompt, "system": system})
        return rows

    def _batch_parse_text(self, text: str) -> list[dict]:
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                # Try JSON first, fall back to plain text
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and "prompt" in obj:
                        rows.append({
                            "prompt": str(obj["prompt"]).strip(),
                            "system": str(obj.get("system", "")).strip(),
                        })
                        continue
                except json.JSONDecodeError:
                    pass
                rows.append({"prompt": line})
        return rows

    # ── Table management ──────────────────────────────────────────────

    def _batch_set_rows(self, rows: list[dict]):
        self._batch_rows = rows
        self._batch_results = []
        self.batch_table.setRowCount(0)
        self.batch_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            prompt = row.get("prompt", "")
            self.batch_table.setItem(i, COL_IDX["Row"],    _item(str(i + 1)))
            self.batch_table.setItem(i, COL_IDX["Status"], _item("pending", STATUS_STYLES["pending"]))
            self.batch_table.setItem(i, COL_IDX["Prompt"], _item(prompt))
            for col_name in ("Response", "Tok/s", "Tokens", "ms", "Error"):
                self.batch_table.setItem(i, COL_IDX[col_name], _item(""))
        self.batch_progress.setValue(0)
        self.batch_progress.setFormat(f"0 / {len(rows)}")
        self.batch_export_btn.setEnabled(False)
        self.batch_status_lbl.setText(f"{len(rows)} row(s) ready")

    def _batch_add_from_editor(self):
        text = self.batch_prompt_edit.toPlainText().strip()
        if not text:
            return
        new_row = {"prompt": text}
        self._batch_rows.append(new_row)
        i = len(self._batch_rows) - 1
        self.batch_table.setRowCount(i + 1)
        self.batch_table.setItem(i, COL_IDX["Row"],    _item(str(i + 1)))
        self.batch_table.setItem(i, COL_IDX["Status"], _item("pending", STATUS_STYLES["pending"]))
        self.batch_table.setItem(i, COL_IDX["Prompt"], _item(text))
        for col_name in ("Response", "Tok/s", "Tokens", "ms", "Error"):
            self.batch_table.setItem(i, COL_IDX[col_name], _item(""))
        self.batch_prompt_edit.clear()
        self.batch_status_lbl.setText(f"{len(self._batch_rows)} row(s) ready")

    def _batch_clear(self):
        if self._batch_worker is not None:
            self._warn("Running", "Stop the batch run before clearing.")
            return
        self._batch_rows = []
        self._batch_results = []
        self.batch_table.setRowCount(0)
        self.batch_progress.setValue(0)
        self.batch_progress.setFormat("Idle")
        self.batch_export_btn.setEnabled(False)
        self.batch_response_view.clear()
        self.batch_status_lbl.setText("")

    def _batch_on_row_selected(self):
        rows = self.batch_table.selectedItems()
        if not rows:
            return
        row_idx = self.batch_table.currentRow()
        # Show full response for that row
        result = next(
            (r for r in self._batch_results if r.row_idx == row_idx), None)
        if result:
            self.batch_response_view.setPlainText(result.response or result.error)
        else:
            # Show the prompt in the editor pane
            prompt_item = self.batch_table.item(row_idx, COL_IDX["Prompt"])
            if prompt_item:
                self.batch_prompt_edit.setPlainText(prompt_item.text())

    # ── Run ───────────────────────────────────────────────────────────

    def _batch_run(self):
        if not self._batch_rows:
            self._warn("No Prompts", "Load or add prompts before running.")
            return
        model = self.batch_model_combo.currentText().strip()
        if not model:
            self._warn("No Model", "Select a model from the dropdown.")
            return
        if self._batch_worker is not None:
            self._warn("Already Running", "Stop the current run first.")
            return

        try:
            temp = float(self.batch_temp.text().strip())
        except ValueError:
            temp = None
        try:
            ctx = int(self.batch_ctx.text().strip())
        except ValueError:
            ctx = None

        system = self.batch_system.text().strip()
        self._batch_results = []

        # Reset table status column
        for i in range(self.batch_table.rowCount()):
            self.batch_table.setItem(
                i, COL_IDX["Status"],
                _item("pending", STATUS_STYLES["pending"]))
            for col_name in ("Response", "Tok/s", "Tokens", "ms", "Error"):
                self.batch_table.setItem(i, COL_IDX[col_name], _item(""))

        self.batch_progress.setValue(0)
        self.batch_progress.setFormat(f"0 / {len(self._batch_rows)}")
        self.batch_run_btn.setEnabled(False)
        self.batch_stop_btn.setEnabled(True)
        self.batch_export_btn.setEnabled(False)
        self._set_status(
            f"Batch run started — {len(self._batch_rows)} prompts on {model}")
        log.info("BatchMixin: run  model=%s  rows=%d", model, len(self._batch_rows))

        worker = BatchRunWorker(
            self.client, model,
            rows=list(self._batch_rows),
            system=system,
            temperature=temp,
            num_ctx=ctx,
        )
        worker.row_done.connect(self._batch_on_row_done)
        worker.finished.connect(self._batch_on_finished)
        worker.failed.connect(self._batch_on_failed)

        self._batch_worker = worker
        worker.finished.connect(lambda _: setattr(self, "_batch_worker", None))
        worker.failed.connect(lambda _: setattr(self, "_batch_worker", None))
        self._start_worker(worker)

    def _batch_stop(self):
        if self._batch_worker:
            self._batch_worker.stop()
        self.batch_stop_btn.setEnabled(False)
        self.batch_progress.setFormat("Stopped")
        self._set_status("Batch run stopped.")

    # ── Worker callbacks ──────────────────────────────────────────────

    def _batch_on_row_done(self, res: BatchRunResult):
        self._batch_results.append(res)
        i = res.row_idx
        if i >= self.batch_table.rowCount():
            return

        style = STATUS_STYLES.get(res.status, "")
        self.batch_table.setItem(
            i, COL_IDX["Status"], _item(res.status, style))

        if res.status == "running":
            return

        # Fill result columns
        self.batch_table.setItem(
            i, COL_IDX["Response"],
            _item((res.response[:120] + "…")
                  if len(res.response) > 120 else res.response))
        self.batch_table.setItem(
            i, COL_IDX["Tok/s"],
            _item(f"{res.tokens_per_sec:.1f}" if res.tokens_per_sec else ""))
        self.batch_table.setItem(
            i, COL_IDX["Tokens"],
            _item(str(res.eval_tokens) if res.eval_tokens else ""))
        self.batch_table.setItem(
            i, COL_IDX["ms"],
            _item(f"{res.total_ms:.0f}" if res.total_ms else ""))
        self.batch_table.setItem(
            i, COL_IDX["Error"],
            _item(res.error[:80] if res.error else ""))

        # Scroll to keep current row visible
        self.batch_table.scrollToItem(
            self.batch_table.item(i, 0),
            QTableWidget.ScrollHint.EnsureVisible)

        # Progress
        done = sum(
            1 for r in self._batch_results
            if r.status in ("done", "error"))
        total = len(self._batch_rows)
        pct = int(done / total * 100) if total else 0
        self.batch_progress.setValue(pct)
        self.batch_progress.setFormat(f"{done} / {total}")

        self.batch_status_lbl.setText(
            f"Row {i + 1}/{total} — {res.status}")

    def _batch_on_finished(self, results: list):
        self.batch_run_btn.setEnabled(True)
        self.batch_stop_btn.setEnabled(False)
        self.batch_export_btn.setEnabled(bool(self._batch_results))
        self.batch_progress.setValue(100)

        done  = sum(1 for r in self._batch_results if r.status == "done")
        errs  = sum(1 for r in self._batch_results if r.status == "error")
        avg_tps = (
            sum(r.tokens_per_sec for r in self._batch_results if r.tokens_per_sec)
            / max(1, sum(1 for r in self._batch_results if r.tokens_per_sec))
        )
        summary = (f"Batch complete — {done} succeeded, {errs} failed"
                   + (f", avg {avg_tps:.1f} tok/s" if avg_tps else ""))
        self.batch_progress.setFormat(summary)
        self._set_status(summary)
        log.info("BatchMixin: finished  done=%d  errors=%d  avg_tps=%.1f",
                 done, errs, avg_tps)

    def _batch_on_failed(self, msg: str):
        self.batch_run_btn.setEnabled(True)
        self.batch_stop_btn.setEnabled(False)
        self.batch_progress.setFormat("Failed")
        self._err("Batch Failed", msg)
        self._set_status("Batch run failed.")
        log.error("BatchMixin: worker failed: %s", msg)

    # ── Export ────────────────────────────────────────────────────────

    def _batch_export(self):
        if not self._batch_results:
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_slug = self.batch_model_combo.currentText().replace(":", "_").replace("/", "_")
        default_name = f"batch_{model_slug}_{ts}"

        path, filt = QFileDialog.getSaveFileName(
            self, "Export Results",
            str(Path.home() / default_name),
            "JSONL (*.jsonl);;CSV (*.csv)"
        )
        if not path:
            return

        try:
            if path.endswith(".csv") or "CSV" in filt:
                self._batch_export_csv(Path(path))
            else:
                if not path.endswith(".jsonl"):
                    path += ".jsonl"
                self._batch_export_jsonl(Path(path))
            self._set_status(f"Exported {len(self._batch_results)} rows to {Path(path).name}")
            log.info("BatchMixin: exported to %s", path)
        except Exception as e:
            self._err("Export Failed", str(e))
            log.exception("BatchMixin: export failed: %s", e)

    def _batch_export_jsonl(self, path: Path):
        lines = []
        for r in sorted(self._batch_results, key=lambda x: x.row_idx):
            lines.append(json.dumps({
                "row":           r.row_idx + 1,
                "model":         r.model,
                "prompt":        r.prompt,
                "system":        r.system,
                "response":      r.response,
                "status":        r.status,
                "error":         r.error,
                "prompt_tokens": r.prompt_tokens,
                "eval_tokens":   r.eval_tokens,
                "tokens_per_sec": round(r.tokens_per_sec, 2),
                "total_ms":      round(r.total_ms, 1),
            }, ensure_ascii=False))
        path.write_text("\n".join(lines), encoding="utf-8")

    def _batch_export_csv(self, path: Path):
        fieldnames = [
            "row", "model", "prompt", "system", "response",
            "status", "error", "prompt_tokens", "eval_tokens",
            "tokens_per_sec", "total_ms",
        ]
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for r in sorted(self._batch_results, key=lambda x: x.row_idx):
                writer.writerow({
                    "row":           r.row_idx + 1,
                    "model":         r.model,
                    "prompt":        r.prompt,
                    "system":        r.system,
                    "response":      r.response,
                    "status":        r.status,
                    "error":         r.error,
                    "prompt_tokens": r.prompt_tokens,
                    "eval_tokens":   r.eval_tokens,
                    "tokens_per_sec": round(r.tokens_per_sec, 2),
                    "total_ms":      round(r.total_ms, 1),
                })
