"""
Mixin — Settings tab.

Sections:
  - Voice Gateway (TTS)  — URL, endpoint, payload key, test button, live log
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

import json
import requests as _requests

from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal, pyqtSlot, QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea,
    QTextEdit, QVBoxLayout, QWidget,
)

from logger import log
from widgets import SectionLabel, Separator


# ── Test worker ────────────────────────────────────────────────────────────

class VGTestWorker(QObject):
    log_line = pyqtSignal(str)   # status message
    done     = pyqtSignal(bool)  # success

    def __init__(self, url: str, method: str, payload_key: str, text: str):
        super().__init__()
        self.url         = url
        self.method      = method
        self.payload_key = payload_key
        self.text        = text

    @pyqtSlot()
    def run(self):
        self.log_line.emit(f"→ {self.method} {self.url}")
        payload = {self.payload_key: self.text}
        self.log_line.emit(f"  payload: {json.dumps(payload)}")
        try:
            fn = getattr(_requests, self.method.lower())
            r = fn(self.url, json=payload, timeout=10)
            self.log_line.emit(f"  status : {r.status_code} {r.reason}")
            if r.text:
                preview = r.text[:200]
                self.log_line.emit(f"  body   : {preview}")
            ok = r.status_code < 400
            self.log_line.emit("✓ Success" if ok else "✗ Server returned error")
            self.done.emit(ok)
        except Exception as e:
            self.log_line.emit(f"✗ {type(e).__name__}: {e}")
            self.done.emit(False)


# ── Settings Mixin ─────────────────────────────────────────────────────────

class SettingsMixin:

    def _tab_settings(self) -> QWidget:
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background:#0a0f18; border:none; }")

        inner = QWidget()
        inner.setStyleSheet("background:#0a0f18;")
        il = QVBoxLayout(inner)
        il.setContentsMargins(24, 20, 24, 20)
        il.setSpacing(20)

        # ── Voice Gateway section ─────────────────────────────────────
        vg_box = QGroupBox("Voice Gateway  (TTS)")
        vg_box.setStyleSheet("""
            QGroupBox {
                color:#94a3b8; font-size:12px; font-weight:700;
                border:1px solid #1e2533; border-radius:8px;
                margin-top:8px; padding-top:12px;
            }
            QGroupBox::title {
                subcontrol-origin:margin; left:12px; padding:0 6px;
            }
        """)
        vg_l = QVBoxLayout(vg_box)
        vg_l.setSpacing(10)

        # Enable toggle
        enable_row = QHBoxLayout()
        self.vg_enabled_chk = QCheckBox("Enable Voice Gateway TTS")
        self.vg_enabled_chk.setStyleSheet("color:#e2e8f0; font-size:12px;")
        self.vg_enabled_chk.setChecked(bool(self._settings.get("vg_enabled")))
        self.vg_enabled_chk.toggled.connect(self._settings_vg_toggled)
        enable_row.addWidget(self.vg_enabled_chk)
        enable_row.addStretch(1)

        self.vg_status_dot = QLabel("●")
        self.vg_status_dot.setStyleSheet("color:#374151; font-size:18px;")
        enable_row.addWidget(self.vg_status_dot)
        vg_l.addLayout(enable_row)

        desc = QLabel(
            "When enabled, completed assistant replies in the Chat tab are "
            "sent to your Voice Gateway for text-to-speech playback.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#4b5563; font-size:11px;")
        vg_l.addWidget(desc)

        vg_l.addWidget(self._settings_sep())

        # URL row
        url_row = QHBoxLayout()
        url_lbl = QLabel("Base URL")
        url_lbl.setFixedWidth(110)
        url_lbl.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
        self.vg_url_input = QLineEdit(self._settings.get("vg_url"))
        self.vg_url_input.setPlaceholderText("http://192.168.0.163:8050")
        self.vg_url_input.setStyleSheet(self._settings_input_style())
        url_row.addWidget(url_lbl)
        url_row.addWidget(self.vg_url_input, 1)
        vg_l.addLayout(url_row)

        # Endpoint row
        ep_row = QHBoxLayout()
        ep_lbl = QLabel("Endpoint")
        ep_lbl.setFixedWidth(110)
        ep_lbl.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
        self.vg_endpoint_input = QLineEdit(self._settings.get("vg_endpoint"))
        self.vg_endpoint_input.setPlaceholderText("/tts")
        self.vg_endpoint_input.setStyleSheet(self._settings_input_style())
        ep_row.addWidget(ep_lbl)
        ep_row.addWidget(self.vg_endpoint_input, 1)

        ep_hint = QLabel("Full URL preview:")
        ep_hint.setStyleSheet("color:#374151; font-size:10px;")
        self.vg_full_url_lbl = QLabel(self._settings.vg_full_url)
        self.vg_full_url_lbl.setStyleSheet(
            "color:#60a5fa; font-size:10px; font-family:monospace;")
        vg_l.addLayout(ep_row)

        full_row = QHBoxLayout()
        full_row.addSpacing(114)
        full_row.addWidget(ep_hint)
        full_row.addWidget(self.vg_full_url_lbl, 1)
        vg_l.addLayout(full_row)

        # Update preview on change
        self.vg_url_input.textChanged.connect(self._settings_update_url_preview)
        self.vg_endpoint_input.textChanged.connect(self._settings_update_url_preview)

        # Method row
        method_row = QHBoxLayout()
        method_lbl = QLabel("Method")
        method_lbl.setFixedWidth(110)
        method_lbl.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
        self.vg_method_combo = QComboBox()
        self.vg_method_combo.addItems(["POST", "GET"])
        self.vg_method_combo.setCurrentText(self._settings.get("vg_method"))
        self.vg_method_combo.setFixedWidth(100)
        method_row.addWidget(method_lbl)
        method_row.addWidget(self.vg_method_combo)
        method_row.addStretch(1)
        vg_l.addLayout(method_row)

        # Payload key row
        pk_row = QHBoxLayout()
        pk_lbl = QLabel("JSON text key")
        pk_lbl.setFixedWidth(110)
        pk_lbl.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
        self.vg_payload_key_input = QLineEdit(self._settings.get("vg_payload_key"))
        self.vg_payload_key_input.setPlaceholderText("text")
        self.vg_payload_key_input.setFixedWidth(160)
        self.vg_payload_key_input.setStyleSheet(self._settings_input_style())
        pk_hint = QLabel('The key in the JSON body that carries the text — e.g. "text", "input", "content"')
        pk_hint.setStyleSheet("color:#374151; font-size:10px;")
        pk_row.addWidget(pk_lbl)
        pk_row.addWidget(self.vg_payload_key_input)
        pk_row.addSpacing(10)
        pk_row.addWidget(pk_hint, 1)
        vg_l.addLayout(pk_row)

        # Example payload preview
        self.vg_payload_preview = QLabel()
        self.vg_payload_preview.setStyleSheet(
            "color:#4b6a9e; font-size:10px; font-family:monospace; "
            "background:#080c13; border:1px solid #1e2533; border-radius:4px; padding:6px 10px;")
        self.vg_payload_key_input.textChanged.connect(self._settings_update_payload_preview)
        vg_l.addWidget(self.vg_payload_preview)
        self._settings_update_payload_preview()

        vg_l.addWidget(self._settings_sep())

        # Test phrase + test button
        test_row = QHBoxLayout()
        test_lbl = QLabel("Test phrase")
        test_lbl.setFixedWidth(110)
        test_lbl.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
        self.vg_test_phrase_input = QLineEdit(self._settings.get("vg_test_phrase"))
        self.vg_test_phrase_input.setStyleSheet(self._settings_input_style())
        test_row.addWidget(test_lbl)
        test_row.addWidget(self.vg_test_phrase_input, 1)
        vg_l.addLayout(test_row)

        btn_row = QHBoxLayout()
        self.vg_save_btn = QPushButton("💾  Save")
        self.vg_save_btn.setMinimumWidth(90)
        self.vg_save_btn.setObjectName("btn_primary")
        self.vg_save_btn.clicked.connect(self._settings_save_vg)

        self.vg_test_btn = QPushButton("▶  Send Test")
        self.vg_test_btn.setMinimumWidth(110)
        self.vg_test_btn.clicked.connect(self._settings_test_vg)

        self.vg_clear_log_btn = QPushButton("Clear log")
        self.vg_clear_log_btn.setMinimumWidth(80)
        self.vg_clear_log_btn.setStyleSheet("font-size:11px;")
        self.vg_clear_log_btn.clicked.connect(lambda: self.vg_log.clear())

        btn_row.addWidget(self.vg_save_btn)
        btn_row.addWidget(self.vg_test_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.vg_clear_log_btn)
        vg_l.addLayout(btn_row)

        # Test log
        self.vg_log = QTextEdit()
        self.vg_log.setReadOnly(True)
        self.vg_log.setFixedHeight(130)
        self.vg_log.setPlaceholderText("Test output appears here…")
        self.vg_log.setStyleSheet(
            "QTextEdit { background:#080c13; border:1px solid #1e2533; border-radius:4px; "
            "color:#4b6a9e; font-size:11px; font-family:'Cascadia Code','Consolas',monospace; "
            "padding:6px; }")
        vg_l.addWidget(self.vg_log)

        # Chat auto-TTS
        auto_row = QHBoxLayout()
        self.vg_auto_chk = QCheckBox("Enable TTS by default when opening Chat tab")
        self.vg_auto_chk.setStyleSheet("color:#94a3b8; font-size:11px;")
        self.vg_auto_chk.setChecked(bool(self._settings.get("chat_tts_auto")))
        auto_row.addWidget(self.vg_auto_chk)
        auto_row.addStretch(1)
        vg_l.addLayout(auto_row)

        il.addWidget(vg_box)
        il.addStretch(1)

        scroll.setWidget(inner)
        root.addWidget(scroll)

        self._vg_test_thread: QThread | None = None
        return container

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _settings_input_style() -> str:
        return (
            "QLineEdit { background:#111827; border:1px solid #1f2937; border-radius:6px; "
            "color:#e2e8f0; font-size:12px; padding:5px 10px; }"
            "QLineEdit:focus { border-color:#3b82f6; }"
        )

    @staticmethod
    def _settings_sep() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#1e2533;")
        return sep

    def _settings_update_url_preview(self):
        base = self.vg_url_input.text().rstrip("/")
        ep   = self.vg_endpoint_input.text()
        if ep and not ep.startswith("/"):
            ep = "/" + ep
        self.vg_full_url_lbl.setText(base + ep)

    def _settings_update_payload_preview(self):
        key = self.vg_payload_key_input.text().strip() or "text"
        self.vg_payload_preview.setText(
            f'  POST body: {{"{key}": "<assistant reply>"}}'
        )

    def _settings_vg_toggled(self, checked: bool):
        self.vg_status_dot.setStyleSheet(
            f"color:{'#22c55e' if checked else '#374151'}; font-size:18px;")

    def _settings_save_vg(self):
        self._settings.set_many({
            "vg_enabled":     self.vg_enabled_chk.isChecked(),
            "vg_url":         self.vg_url_input.text().strip(),
            "vg_endpoint":    self.vg_endpoint_input.text().strip(),
            "vg_method":      self.vg_method_combo.currentText(),
            "vg_payload_key": self.vg_payload_key_input.text().strip() or "text",
            "vg_test_phrase": self.vg_test_phrase_input.text().strip(),
            "chat_tts_auto":  self.vg_auto_chk.isChecked(),
        })
        # Sync the chat tab checkbox if it exists
        if hasattr(self, "chat_tts_chk"):
            self.chat_tts_chk.setChecked(self.vg_enabled_chk.isChecked())
        self._set_status("Voice Gateway settings saved.")
        self.vg_log.append("✓ Settings saved.")
        log.info("SettingsMixin: VG settings saved")

    def _settings_test_vg(self):
        if self._vg_test_thread and self._vg_test_thread.isRunning():
            self.vg_log.append("⚠ Test already running…")
            return

        # Auto-save first
        self._settings_save_vg()

        url    = self._settings.vg_full_url
        method = self.vg_method_combo.currentText()
        key    = self.vg_payload_key_input.text().strip() or "text"
        phrase = self.vg_test_phrase_input.text().strip()

        self.vg_log.append(f"\n── Test {method} ──────────────────────────")
        self.vg_test_btn.setEnabled(False)

        worker = VGTestWorker(url, method, key, phrase)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log_line.connect(self.vg_log.append)
        worker.done.connect(self._settings_test_done)
        worker.done.connect(lambda _: thread.quit())
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: setattr(self, "_vg_test_thread", None))
        self._vg_test_thread = thread
        thread.start()

    def _settings_test_done(self, ok: bool):
        self.vg_test_btn.setEnabled(True)
        self.vg_status_dot.setStyleSheet(
            f"color:{'#22c55e' if ok else '#f87171'}; font-size:18px;")
        # Reset dot colour after 5 s
        QTimer.singleShot(5000, lambda: self.vg_status_dot.setStyleSheet(
            f"color:{'#22c55e' if self.vg_enabled_chk.isChecked() else '#374151'}; font-size:18px;"))
