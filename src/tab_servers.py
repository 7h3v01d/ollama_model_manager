"""
Mixin — Server Manager tab.
Manage multiple Ollama server connections. Ping each server,
view model counts, add/remove/rename, and switch active server.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QTextEdit,
    QVBoxLayout, QWidget,
)

from logger import log
from utils import ServerRegistry, ServerEntry, DEFAULT_BASE_URL
from client import OllamaClient
from workers import Worker
from widgets import SectionLabel, Separator


# ── Server card widget ────────────────────────────────────────────────────

class ServerCard(QFrame):
    """Visual card for one server entry."""

    def __init__(self, entry: ServerEntry, is_active: bool, parent=None):
        super().__init__(parent)
        self.entry = entry
        self._is_active = is_active
        self._apply_style(is_active)
        self._build()

    def _apply_style(self, active: bool):
        if active:
            self.setStyleSheet(
                "QFrame { background-color:#0d1f14; border:1px solid #166534; "
                "border-radius:10px; }")
        else:
            self.setStyleSheet(
                "QFrame { background-color:#111827; border:1px solid #1e2533; "
                "border-radius:10px; }")

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        # Status dot
        self.status_dot = QLabel("●")
        self.status_dot.setFixedWidth(16)
        self.status_dot.setStyleSheet("color:#374151; font-size:16px;")
        layout.addWidget(self.status_dot)

        # Info column
        info = QVBoxLayout()
        info.setSpacing(3)

        name_row = QHBoxLayout()
        self.name_lbl = QLabel(self.entry.name)
        self.name_lbl.setStyleSheet(
            "color:#e2e8f0; font-size:14px; font-weight:700;")
        name_row.addWidget(self.name_lbl)
        if self._is_active:
            active_badge = QLabel("active")
            active_badge.setStyleSheet(
                "background:#052e16; border:1px solid #166534; border-radius:4px;"
                "color:#4ade80; font-size:11px; font-weight:700; padding:1px 7px;")
            name_row.addWidget(active_badge)
        name_row.addStretch(1)
        info.addLayout(name_row)

        self.url_lbl = QLabel(self.entry.url)
        self.url_lbl.setStyleSheet(
            "color:#4b5563; font-size:12px; font-family:monospace;")
        info.addWidget(self.url_lbl)

        if self.entry.notes:
            notes_lbl = QLabel(self.entry.notes)
            notes_lbl.setStyleSheet("color:#374151; font-size:11px; font-style:italic;")
            info.addWidget(notes_lbl)

        layout.addLayout(info, 1)

        # Metrics column
        self.metric_lbl = QLabel("—")
        self.metric_lbl.setFixedWidth(130)
        self.metric_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.metric_lbl.setStyleSheet("color:#374151; font-size:12px;")
        layout.addWidget(self.metric_lbl)

        # Latency
        self.latency_lbl = QLabel("")
        self.latency_lbl.setFixedWidth(70)
        self.latency_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.latency_lbl.setStyleSheet("color:#374151; font-size:11px;")
        layout.addWidget(self.latency_lbl)

        # Buttons
        btn_col = QHBoxLayout()
        btn_col.setSpacing(6)

        self.ping_btn = QPushButton("Ping")
        self.ping_btn.setFixedHeight(28)
        self.ping_btn.setFixedWidth(56)
        self.ping_btn.setStyleSheet("font-size:11px;")
        btn_col.addWidget(self.ping_btn)

        self.switch_btn = QPushButton("Switch")
        self.switch_btn.setFixedHeight(28)
        self.switch_btn.setFixedWidth(62)
        self.switch_btn.setObjectName("btn_primary")
        self.switch_btn.setStyleSheet("font-size:11px;")
        self.switch_btn.setVisible(not self._is_active)
        btn_col.addWidget(self.switch_btn)

        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setFixedHeight(28)
        self.edit_btn.setFixedWidth(50)
        self.edit_btn.setStyleSheet("font-size:11px;")
        btn_col.addWidget(self.edit_btn)

        self.remove_btn = QPushButton("✕")
        self.remove_btn.setFixedHeight(28)
        self.remove_btn.setFixedWidth(32)
        self.remove_btn.setObjectName("btn_danger")
        self.remove_btn.setStyleSheet("font-size:11px;")
        btn_col.addWidget(self.remove_btn)

        layout.addLayout(btn_col)

    # ── Public update methods ─────────────────────────────────────────

    def set_online(self, model_count: int, latency_ms: float):
        self.status_dot.setStyleSheet("color:#10b981; font-size:16px;")
        self.metric_lbl.setStyleSheet("color:#10b981; font-size:12px;")
        self.metric_lbl.setText(
            f"{model_count} model{'s' if model_count != 1 else ''}")
        self.latency_lbl.setStyleSheet("color:#64748b; font-size:11px;")
        self.latency_lbl.setText(f"{latency_ms:.0f} ms")

    def set_offline(self, reason: str = ""):
        self.status_dot.setStyleSheet("color:#ef4444; font-size:16px;")
        self.metric_lbl.setStyleSheet("color:#7f1d1d; font-size:12px;")
        self.metric_lbl.setText("unreachable")
        self.latency_lbl.setStyleSheet("color:#4b5563; font-size:11px;")
        self.latency_lbl.setText(reason[:14] if reason else "")

    def set_pinging(self):
        self.status_dot.setStyleSheet("color:#f59e0b; font-size:16px;")
        self.metric_lbl.setStyleSheet("color:#4b5563; font-size:12px;")
        self.metric_lbl.setText("pinging…")
        self.latency_lbl.setText("")


# ── Servers Mixin ─────────────────────────────────────────────────────────

class ServersMixin:

    def _tab_servers(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Toolbar
        tb = QHBoxLayout()
        self.srv_add_btn = QPushButton("+ Add Server")
        self.srv_add_btn.setObjectName("btn_primary")
        self.srv_add_btn.setMinimumWidth(120)
        self.srv_add_btn.clicked.connect(self._srv_add)

        self.srv_ping_all_btn = QPushButton("⟳  Ping All")
        self.srv_ping_all_btn.setMinimumWidth(100)
        self.srv_ping_all_btn.clicked.connect(self._srv_ping_all)

        self.srv_status_lbl = QLabel("")
        self.srv_status_lbl.setObjectName("label_muted")

        tb.addWidget(self.srv_add_btn)
        tb.addWidget(self.srv_ping_all_btn)
        tb.addStretch(1)
        tb.addWidget(self.srv_status_lbl)
        layout.addLayout(tb)

        layout.addWidget(Separator())

        # Scroll area for server cards
        self.srv_scroll = QScrollArea()
        self.srv_scroll.setWidgetResizable(True)
        self.srv_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.srv_cards_widget = QWidget()
        self.srv_cards_layout = QVBoxLayout(self.srv_cards_widget)
        self.srv_cards_layout.setContentsMargins(0, 0, 8, 0)
        self.srv_cards_layout.setSpacing(10)
        self.srv_cards_layout.addStretch(1)

        self.srv_scroll.setWidget(self.srv_cards_widget)
        layout.addWidget(self.srv_scroll)

        # Storage path info
        reg_path = QLabel(
            f"Config stored at: {self._server_registry._path}")
        reg_path.setStyleSheet("color:#1f2937; font-size:10px; font-family:monospace;")
        layout.addWidget(reg_path)

        self._srv_cards: list[ServerCard] = []
        self._srv_render()
        return container

    # ── Rendering ─────────────────────────────────────────────────────

    def _srv_render(self):
        """Rebuild all server cards from the registry."""
        # Clear existing cards (keep trailing stretch)
        while self.srv_cards_layout.count() > 1:
            item = self.srv_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._srv_cards = []

        active_url = self._server_registry.active().url
        for entry in self._server_registry.entries():
            is_active = entry.url.rstrip("/") == active_url.rstrip("/")
            card = ServerCard(entry, is_active)
            card.ping_btn.clicked.connect(
                lambda _, e=entry, c=card: self._srv_ping_one(e, c))
            card.switch_btn.clicked.connect(
                lambda _, e=entry: self._srv_switch(e.url))
            card.edit_btn.clicked.connect(
                lambda _, e=entry: self._srv_edit(e))
            card.remove_btn.clicked.connect(
                lambda _, e=entry: self._srv_remove(e.url))
            self.srv_cards_layout.insertWidget(
                self.srv_cards_layout.count() - 1, card)
            self._srv_cards.append(card)

        count = len(self._srv_cards)
        self.srv_status_lbl.setText(
            f"{count} server{'s' if count != 1 else ''} configured")

    # ── Ping ──────────────────────────────────────────────────────────

    def _srv_ping_one(self, entry: ServerEntry, card: ServerCard):
        card.set_pinging()
        log.info("ServersMixin: pinging %s", entry.url)
        worker = Worker()
        url = entry.url

        def run():
            try:
                t0 = time.perf_counter()
                client = OllamaClient(url)
                data = client.tags()
                latency = (time.perf_counter() - t0) * 1000
                model_count = len(data.get("models", []))
                worker.finished.emit({
                    "ok": True,
                    "latency_ms": latency,
                    "model_count": model_count,
                })
            except Exception as e:
                worker.finished.emit({"ok": False, "error": str(e)})

        worker.run = run
        worker.finished.connect(lambda p, c=card: self._srv_on_ping(p, c))
        self._start_worker(worker)

    def _srv_on_ping(self, payload: dict, card: ServerCard):
        if payload.get("ok"):
            card.set_online(payload["model_count"], payload["latency_ms"])
            log.info("ServersMixin: ping OK  latency=%.0f ms  models=%d",
                     payload["latency_ms"], payload["model_count"])
        else:
            card.set_offline(payload.get("error", ""))
            log.warning("ServersMixin: ping failed: %s", payload.get("error"))

    def _srv_ping_all(self):
        for card in self._srv_cards:
            self._srv_ping_one(card.entry, card)

    # ── Switch active server ──────────────────────────────────────────

    def _srv_switch(self, url: str):
        log.info("ServersMixin: switching to %s", url)
        self._server_registry.set_active(url)
        self.conn_bar.refresh()
        self.on_server_changed(url)
        self._srv_render()

    # ── Add / Edit / Remove ───────────────────────────────────────────

    def _srv_add(self):
        entry = ServerEntry(name="New Server", url="http://", notes="")
        if self._srv_show_edit_dialog(entry, is_new=True):
            self._server_registry.add(entry.name, entry.url, entry.notes)
            self.conn_bar.refresh()
            self._srv_render()

    def _srv_edit(self, entry: ServerEntry):
        # Work on a copy so cancel doesn't mutate
        copy = ServerEntry(name=entry.name, url=entry.url, notes=entry.notes)
        if self._srv_show_edit_dialog(copy, is_new=False):
            self._server_registry.rename(entry.url, copy.name, copy.notes)
            # If URL changed: remove old, add new
            if copy.url.rstrip("/") != entry.url.rstrip("/"):
                was_active = (entry.url.rstrip("/") ==
                              self._server_registry.active().url.rstrip("/"))
                self._server_registry.remove(entry.url)
                self._server_registry.add(copy.name, copy.url, copy.notes)
                if was_active:
                    self._server_registry.set_active(copy.url)
                    self.conn_bar.refresh()
                    self.on_server_changed(copy.url)
            self.conn_bar.refresh()
            self._srv_render()

    def _srv_show_edit_dialog(self, entry: ServerEntry, is_new: bool) -> bool:
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Server" if is_new else "Edit Server")
        dlg.setModal(True)
        dlg.resize(440, 190)
        dlg.setStyleSheet(
            "QDialog{background:#111827; border:1px solid #1e2533;}")
        fl = QFormLayout(dlg)
        fl.setContentsMargins(24, 20, 24, 16)
        fl.setSpacing(12)

        name_input = QLineEdit(entry.name)
        url_input  = QLineEdit(entry.url)
        url_input.setPlaceholderText("http://192.168.1.x:11434")
        notes_input = QLineEdit(entry.notes)
        notes_input.setPlaceholderText("Optional description")

        fl.addRow("Name:",        name_input)
        fl.addRow("URL:",         url_input)
        fl.addRow("Notes:",       notes_input)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        fl.addRow(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            entry.name  = name_input.text().strip() or "Server"
            entry.url   = url_input.text().strip()
            entry.notes = notes_input.text().strip()
            return bool(entry.url)
        return False

    def _srv_remove(self, url: str):
        if len(self._server_registry.entries()) <= 1:
            self._warn("Cannot Remove",
                       "At least one server must remain configured.")
            return
        entry = next(
            (e for e in self._server_registry.entries()
             if e.url.rstrip("/") == url.rstrip("/")), None)
        name = entry.name if entry else url
        if self._confirm(f"Remove server '{name}'?",
                         "This only removes it from the saved list — "
                         "it does not affect the Ollama process."):
            was_active = (url.rstrip("/") ==
                          self._server_registry.active().url.rstrip("/"))
            self._server_registry.remove(url)
            self.conn_bar.refresh()
            if was_active:
                new_url = self._server_registry.active().url
                self.on_server_changed(new_url)
            self._srv_render()

    def _confirm(self, title: str, text: str) -> bool:
        from PyQt6.QtWidgets import QMessageBox
        r = QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return r == QMessageBox.StandardButton.Yes
