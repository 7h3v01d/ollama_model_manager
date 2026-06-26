"""window.py — MainWindow shell + core model/connection logic."""
import json
import sys
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QModelIndex, QThread, QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSplitter,
    QStatusBar, QTabWidget, QTableView, QTextEdit, QVBoxLayout, QWidget,
)

from theme import STYLESHEET
from logger import log
from utils import human_bytes, parse_time, default_models_dir_guess, ServerRegistry
from client import OllamaClient
# DEFAULT_BASE_URL now sourced via ServerRegistry
from models import (
    InstalledModelRow, InstalledModelsTableModel,
    RunningModelRow, RunningModelsTableModel,
)
from workers import Worker, PullWorker, start_worker
from widgets import (
    SectionLabel, Separator, StatCard, MultiServerBar, DetailPanel,
    ConfirmDeleteDialog, prompt_text,
)
from tab_transfer  import TransferMixin
from tab_registry  import RegistryMixin
from tab_benchmark import BenchmarkMixin
from tab_monitor   import MonitorMixin
from tab_modelfile import ModelfileMixin
from tab_disk      import DiskMixin
from tab_chat      import ChatMixin
from tab_about     import AboutMixin
from tab_servers   import ServersMixin
from tab_prompts   import PromptsMixin, PromptPickerDialog
from tab_batch     import BatchMixin
from prompt_library import PromptLibrary
from migrate_settings import migrate_keystone_to_7h3v01d
from tab_settings  import SettingsMixin
from app_settings  import AppSettings


class MainWindow(
    TransferMixin, RegistryMixin, BenchmarkMixin,
    MonitorMixin, ModelfileMixin, DiskMixin, ChatMixin,
    AboutMixin, ServersMixin, PromptsMixin, BatchMixin, SettingsMixin,
    QMainWindow,
):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ollama Manager Pro")
        self.resize(1280, 820)
        self.setMinimumSize(960, 640)

        # Migrate legacy KeystoneAI settings to 7h3v01d on first run
        _migrated = migrate_keystone_to_7h3v01d()
        if _migrated:
            log.info("Settings migrated from KeystoneAI: %s", _migrated)

        self._settings = AppSettings()
        self._server_registry  = ServerRegistry()
        self._prompt_library   = PromptLibrary()
        self.client           = OllamaClient(self._server_registry.active().url)
        self.installed_model = InstalledModelsTableModel()
        self.running_model   = RunningModelsTableModel()

        self._active_threads: set[QThread] = set()
        self._pull_worker: PullWorker | None = None
        self._pull_thread:  QThread | None = None

        self._registry_cache: list[dict] = []
        self._reg_worker  = None
        self._chat_worker = None
        self._bench_worker  = None
        self._bench_thread  = None
        self._monitor_timer:  QTimer | None = None
        self._monitor_worker  = None
        self._monitor_history: dict = {"cpu": [], "ram": [], "vram": []}
        self._disk_worker   = None
        self._disk_result:  dict = {}

        self._build_ui()

        self.running_timer = QTimer(self)
        self.running_timer.setInterval(5000)
        self.running_timer.timeout.connect(self.refresh_running)

        self._set_status("Ready — connect to an Ollama server to begin.")
        self.refresh_installed()
        self.refresh_running()

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self):
        header = self._build_header()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._tab_installed(), "  Installed Models  ")
        self.tabs.addTab(self._tab_running(),   "  Running  ")
        self.tabs.addTab(self._tab_servers(),   "  Servers  ")
        self.tabs.addTab(self._tab_transfer(),  "  Pull & Backup  ")
        self.tabs.addTab(self._tab_registry(),  "  Registry  ")
        self.tabs.addTab(self._tab_benchmark(), "  Benchmark  ")
        self.tabs.addTab(self._tab_chat(),      "  Chat  ")
        self.tabs.addTab(self._tab_prompts(),   "  Prompts  ")
        self.tabs.addTab(self._tab_batch(),     "  Batch  ")
        self.tabs.addTab(self._tab_monitor(),   "  Monitor  ")
        self.tabs.addTab(self._tab_modelfile(), "  Modelfile  ")
        self.tabs.addTab(self._tab_disk(),      "  Disk  ")
        self.tabs.addTab(self._tab_settings(),  "  ⚙ Settings  ")
        self.tabs.addTab(self._tab_about(),     "  About  ")

        self._status_bar = QStatusBar()
        self._status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self._status_bar)

        central = QWidget()
        ml = QVBoxLayout(central)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        ml.addWidget(header)
        ml.addWidget(Separator())
        ml.addWidget(self.tabs)
        self.setCentralWidget(central)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setStyleSheet("background-color: #080c13;")
        header.setFixedHeight(54)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(0)

        logo = QLabel("⬡ Ollama Manager")
        logo.setStyleSheet(
            "color:#60a5fa; font-size:15px; font-weight:800; letter-spacing:0.5px;")
        layout.addWidget(logo)
        layout.addWidget(Separator(vertical=True))
        layout.addSpacing(12)

        self.conn_bar = MultiServerBar(self._server_registry)
        self.conn_bar.server_changed.connect(self.on_server_changed)
        self.conn_bar.filter_input.textChanged.connect(
            self.installed_model.set_filter)
        layout.addWidget(self.conn_bar)
        return header

    def _tab_installed(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── Row 1: stat cards + Refresh ──────────────────────────────
        stats_row = QHBoxLayout()
        self.stat_total      = StatCard("Total")
        self.stat_total_size = StatCard("Total Size")
        stats_row.addWidget(self.stat_total)
        stats_row.addWidget(self.stat_total_size)
        stats_row.addStretch(1)

        self.btn_refresh_installed = QPushButton("↻  Refresh")
        self.btn_refresh_installed.setMinimumWidth(100)
        self.btn_refresh_installed.clicked.connect(self.refresh_installed)
        stats_row.addWidget(self.btn_refresh_installed)
        layout.addLayout(stats_row)

        # ── Row 2: selection-dependent action buttons ─────────────────
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.btn_export_selected = QPushButton("⬆  Export Selected…")
        self.btn_export_selected.setObjectName("btn_primary")
        self.btn_export_selected.setEnabled(False)
        self.btn_export_selected.setToolTip(
            "Backup selected models to a ZIP file (blobs + manifests)")
        self.btn_export_selected.clicked.connect(self.export_selected_zip)

        self.btn_copy_selected = QPushButton("⎘  Copy to Server…")
        self.btn_copy_selected.setEnabled(False)
        self.btn_copy_selected.setToolTip(
            "Copy selected models to another Ollama server via /api/copy")
        self.btn_copy_selected.clicked.connect(self._installed_copy_to_server)

        self.btn_delete_installed = QPushButton("✕  Delete Selected")
        self.btn_delete_installed.setObjectName("btn_danger")
        self.btn_delete_installed.setEnabled(False)
        self.btn_delete_installed.clicked.connect(self.delete_installed_selected)

        self.btn_delete_filtered = QPushButton("✕  Delete Filtered")
        self.btn_delete_filtered.setObjectName("btn_danger")
        self.btn_delete_filtered.clicked.connect(self.delete_all_filtered_installed)

        for btn in (self.btn_export_selected, self.btn_copy_selected,
                    self.btn_delete_installed, self.btn_delete_filtered):
            action_row.addWidget(btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)

        table_panel = QWidget()
        tl = QVBoxLayout(table_panel)
        tl.setContentsMargins(0, 0, 0, 0)

        self.table_installed = QTableView()
        self.table_installed.setModel(self.installed_model)
        self.table_installed.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows)
        self.table_installed.setSelectionMode(
            QTableView.SelectionMode.ExtendedSelection)
        self.table_installed.setAlternatingRowColors(True)
        self.table_installed.setShowGrid(False)
        self.table_installed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table_installed.verticalHeader().setDefaultSectionSize(40)
        self.table_installed.verticalHeader().hide()
        hh = self.table_installed.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table_installed.setColumnWidth(1, 90)
        self.table_installed.setColumnWidth(2, 160)
        self.table_installed.clicked.connect(self.on_installed_selected)
        self.table_installed.selectionModel().selectionChanged.connect(
            lambda *_: self.on_installed_selected(QModelIndex()))
        tl.addWidget(self.table_installed)

        self.detail_panel = DetailPanel()
        splitter.addWidget(table_panel)
        splitter.addWidget(self.detail_panel)
        splitter.setSizes([760, 480])
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        return container

    def _tab_running(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        ctrl = QHBoxLayout()
        self.chk_auto_refresh = QCheckBox("Auto-refresh every 5 s")
        self.chk_auto_refresh.stateChanged.connect(self.on_auto_refresh_running)
        btn_refresh = QPushButton("↻  Refresh Now")
        btn_refresh.clicked.connect(self.refresh_running)
        self.running_count_lbl = QLabel("No models running")
        self.running_count_lbl.setObjectName("label_muted")
        ctrl.addWidget(self.chk_auto_refresh)
        ctrl.addWidget(self.running_count_lbl)
        ctrl.addStretch(1)
        ctrl.addWidget(btn_refresh)
        layout.addLayout(ctrl)

        self.table_running = QTableView()
        self.table_running.setModel(self.running_model)
        self.table_running.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows)
        self.table_running.setAlternatingRowColors(True)
        self.table_running.setShowGrid(False)
        self.table_running.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table_running.verticalHeader().setDefaultSectionSize(40)
        self.table_running.verticalHeader().hide()
        hh = self.table_running.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 5):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table_running)

        layout.addWidget(SectionLabel("Raw /api/ps Response"))
        self.details_running = QTextEdit()
        self.details_running.setReadOnly(True)
        self.details_running.setPlaceholderText(
            "Raw /api/ps response appears here after refresh.")
        self.details_running.setMaximumHeight(200)
        layout.addWidget(self.details_running)
        return container

    # ── Shared helpers ─────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self._status_bar.showMessage(f"  {msg}", 10_000)

    def _err(self, title: str, text: str):
        dlg = QMessageBox(self)
        dlg.setWindowTitle(title); dlg.setText(text)
        dlg.setIcon(QMessageBox.Icon.Critical); dlg.exec()

    def _warn(self, title: str, text: str):
        dlg = QMessageBox(self)
        dlg.setWindowTitle(title); dlg.setText(text)
        dlg.setIcon(QMessageBox.Icon.Warning); dlg.exec()

    def _info(self, title: str, text: str):
        dlg = QMessageBox(self)
        dlg.setWindowTitle(title); dlg.setText(text)
        dlg.setIcon(QMessageBox.Icon.Information); dlg.exec()

    def _register_thread(self, thread: QThread):
        self._active_threads.add(thread)
        thread.finished.connect(lambda: self._active_threads.discard(thread))

    def _start_worker(self, worker: Worker) -> QThread:
        t = start_worker(worker)
        self._register_thread(t)
        # Keep a strong Python reference to the worker until its thread
        # finishes. Without this, CPython can GC the worker object between
        # _start_worker() returning and the QThread firing started — which
        # silently drops the thread.started -> worker.run connection.
        if not hasattr(self, "_worker_refs"):
            self._worker_refs: set = set()
        self._worker_refs.add(worker)
        t.finished.connect(lambda: self._worker_refs.discard(worker))
        return t

    # ── Connection ─────────────────────────────────────────────────────

    def on_server_changed(self, url: str):
        """Fires when the user picks a different server from the dropdown."""
        if not url:
            return
        self.client.set_base_url(url)
        entry = self._server_registry.active()
        self._set_status(f"Switched to {entry.name} ({url}) — connecting…")
        self.conn_bar.set_connected(False)
        # Clear stale data so old server's models don't show while loading
        self.installed_model.set_rows([])
        self.running_model.set_rows([])
        self.stat_total.set_value("—")
        self.stat_total_size.set_value("—")
        self.refresh_installed()
        self.refresh_running()

    def on_connect(self, url: str):
        """Legacy — kept so any remaining callers don't break."""
        self.on_server_changed(url)

    # ── Installed models ───────────────────────────────────────────────

    def refresh_installed(self):
        self.btn_delete_installed.setEnabled(False)
        self.btn_export_selected.setEnabled(False)
        self.btn_copy_selected.setEnabled(False)
        self.detail_panel.clear()
        self._set_status("Fetching installed models…")
        worker = Worker()

        def run():
            try:
                data = self.client.tags()
                rows = [
                    InstalledModelRow(
                        name=m.get("name", ""),
                        size=m.get("size"),
                        modified_at=m.get("modified_at") or m.get("modified"),
                        raw=m,
                    )
                    for m in data.get("models", [])
                ]
                worker.finished.emit(rows)
            except Exception as e:
                worker.failed.emit(str(e))

        worker.run = run

        def on_ok(rows):
            self.installed_model.set_rows(rows)
            total_size = sum(r.size or 0 for r in rows)
            self.stat_total.set_value(str(len(rows)))
            self.stat_total_size.set_value(human_bytes(total_size))
            self._set_status(f"Loaded {len(rows)} installed model(s).")
            self.conn_bar.set_connected(True)
            if hasattr(self, "mf_base_combo"):
                self._mf_populate_combo()
            if hasattr(self, "chat_model_combo"):
                self._chat_populate_models()
            if hasattr(self, "batch_model_combo"):
                self._batch_populate_models()

        def on_fail(msg):
            self.conn_bar.set_connected(False)
            self._err("Connection Failed",
                      f"Could not reach Ollama.\n\nDetails:\n{msg}")
            self._set_status("Failed to fetch models.")

        worker.finished.connect(on_ok)
        worker.failed.connect(on_fail)
        self._start_worker(worker)

    def installed_selected_names(self) -> list[str]:
        sels = self.table_installed.selectionModel().selectedRows()
        return [
            self.installed_model.rows()[i.row()].name
            for i in sels
            if i.row() < len(self.installed_model.rows())
            and self.installed_model.rows()[i.row()].name
        ]

    def on_installed_selected(self, _index: QModelIndex):
        names = self.installed_selected_names()
        has_sel = bool(names)
        self.btn_delete_installed.setEnabled(has_sel)
        self.btn_export_selected.setEnabled(has_sel)
        self.btn_copy_selected.setEnabled(has_sel)
        if len(names) == 1:
            row = next(
                (r for r in self.installed_model.rows() if r.name == names[0]),
                None)
            if row:
                self.detail_panel.show_loading(names[0])
                self._load_installed_details(row)
        elif len(names) > 1:
            self.detail_panel.show_multi(len(names))
        else:
            self.detail_panel.clear()

    def _load_installed_details(self, row):
        worker = Worker()

        def run():
            try:
                worker.finished.emit(self.client.show(row.name))
            except Exception:
                worker.finished.emit(None)

        worker.run = run
        worker.finished.connect(
            lambda detail: self.detail_panel.show_data(row, detail))
        self._start_worker(worker)

    def _installed_copy_to_server(self):
        """Copy selected models to another Ollama server via /api/copy."""
        import requests as _req
        from PyQt6.QtWidgets import QInputDialog, QMessageBox

        names = self.installed_selected_names()
        if not names:
            return

        # Build server choice list
        servers = self._server_registry.list()
        active_url = self._server_registry.active().url
        other_servers = [s for s in servers if s.url != active_url]
        choices = [f"{s.label}  ({s.url})" for s in other_servers]
        choices.append("Enter URL manually...")

        if len(choices) == 1:
            dest_url, ok = QInputDialog.getText(
                self, "Copy to Server",
                "Destination Ollama URL (e.g. http://192.168.0.100:11434):",
                text="http://")
            if not ok or not dest_url.strip():
                return
            dest_url = dest_url.strip().rstrip("/")
        else:
            choice, ok = QInputDialog.getItem(
                self, "Copy to Server",
                f"Copy {len(names)} model(s) to:", choices, 0, False)
            if not ok:
                return
            if choice == "Enter URL manually...":
                dest_url, ok2 = QInputDialog.getText(
                    self, "Copy to Server",
                    "Destination Ollama URL:", text="http://")
                if not ok2 or not dest_url.strip():
                    return
                dest_url = dest_url.strip().rstrip("/")
            else:
                dest_url = choice.split("(")[-1].rstrip(")")

        reply = QMessageBox.question(
            self, "Confirm Copy",
            f"Copy {len(names)} model(s) to:\n{dest_url}\n\n"
            + "\n".join(f"  * {n}" for n in names),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        errors = []
        for name in names:
            try:
                r = _req.post(
                    f"{dest_url}/api/copy",
                    json={"source": name, "destination": name},
                    timeout=60)
                if r.status_code not in (200, 201):
                    errors.append(f"{name}: HTTP {r.status_code}")
            except Exception as e:
                errors.append(f"{name}: {e}")

        if errors:
            self._err("Copy Errors",
                      f"{len(names)-len(errors)}/{len(names)} succeeded.\n\n"
                      + "\n".join(errors))
        else:
            self._set_status(f"Copied {len(names)} model(s) to {dest_url}")

    def delete_installed_selected(self):
        names = self.installed_selected_names()
        if not names:
            return
        if len(names) == 1:
            dlg = ConfirmDeleteDialog(names[0], self)
            if dlg.exec() != dlg.DialogCode.Accepted:
                return
        else:
            preview = "\n".join(f"• {n}" for n in names[:12])
            if len(names) > 12:
                preview += f"\n…and {len(names) - 12} more"
            if QMessageBox.question(
                self, "Confirm Deletion",
                f"Delete {len(names)} models?\n\n{preview}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
            typed, ok = prompt_text(
                self, "Confirm", f"Type DELETE to remove {len(names)} models:", "DELETE")
            if not ok or typed.strip() != "DELETE":
                return
        self._delete_models(names)

    def delete_all_filtered_installed(self):
        f = (self.conn_bar.filter_input.text() or "").strip()
        if not f:
            self._warn("Filter Required",
                       "Bulk delete requires a non-empty filter for safety.")
            return
        targets = [r.name for r in self.installed_model.rows()]
        if not targets:
            self._info("Nothing to Delete", "No models match the current filter.")
            return
        preview = "\n".join(f"• {n}" for n in targets[:12])
        if len(targets) > 12:
            preview += f"\n…and {len(targets) - 12} more"
        if QMessageBox.question(
            self, "Confirm Bulk Deletion",
            f"Delete {len(targets)} model(s) matching '{f}'?\n\n{preview}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        typed, ok = prompt_text(
            self, "Confirm", f"Type DELETE to remove {len(targets)} models:", "DELETE")
        if not ok or typed.strip() != "DELETE":
            return
        self._delete_models(targets)

    def _delete_models(self, names: list[str]):
        self._set_status(f"Deleting {len(names)} model(s)…")
        worker = Worker()

        def run():
            try:
                failed = []
                for name in names:
                    try:
                        self.client.delete(name)
                    except Exception as e:
                        failed.append((name, str(e)))
                worker.finished.emit(failed)
            except Exception as e:
                worker.failed.emit(str(e))

        worker.run = run
        worker.finished.connect(lambda failed: (
            self._warn("Some Deletions Failed",
                       "\n".join(f"{n}: {e}" for n, e in failed[:10]))
            if failed else None,
            self.refresh_installed(),
            self.refresh_running(),
        ))
        worker.failed.connect(lambda msg: self._err("Delete Failed", msg))
        self._start_worker(worker)

    # ── Running models ─────────────────────────────────────────────────

    def refresh_running(self):
        worker = Worker()

        def run():
            try:
                worker.finished.emit(self.client.ps())
            except Exception as e:
                worker.failed.emit(str(e))

        worker.run = run

        def on_ok(data: dict):
            rows = [
                RunningModelRow(
                    name=str(m.get("name", "")),
                    size=human_bytes(m.get("size")),
                    processor=str(m.get("processor", "—")),
                    context=str(m.get("context", "—")),
                    until=parse_time(m.get("expires_at") or m.get("until")),
                    raw=m,
                )
                for m in data.get("models", [])
            ]
            self.running_model.set_rows(rows)
            self.details_running.setPlainText(
                json.dumps(data, indent=2, ensure_ascii=False))
            n = len(rows)
            self.running_count_lbl.setText(
                f"{n} model{'s' if n != 1 else ''} loaded in memory")

        worker.finished.connect(on_ok)
        worker.failed.connect(lambda _: None)
        self._start_worker(worker)

    def on_auto_refresh_running(self, state: int):
        if state == Qt.CheckState.Checked.value:
            self.running_timer.start()
            self._set_status("Auto-refresh enabled (every 5 s).")
        else:
            self.running_timer.stop()
        if hasattr(self, '_prompt_library'):
            self._prompt_library.close()
            self._set_status("Auto-refresh disabled.")

    # ── Shutdown ────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self.running_timer.stop()
        if hasattr(self, '_prompt_library'):
            self._prompt_library.close()
        if self._monitor_timer is not None:
            self._monitor_timer.stop()
        if self._pull_worker is not None:
            self._pull_worker.stop()
        if self._bench_worker is not None:
            self._bench_worker.stop()
        deadline = time.time() + 3.0
        for t in list(self._active_threads):
            if t.isRunning():
                remaining = max(0.0, deadline - time.time())
                if remaining <= 0:
                    break
                t.wait(int(remaining * 1000))
        event.accept()
