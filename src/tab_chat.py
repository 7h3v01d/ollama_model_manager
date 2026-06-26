"""
Mixin — Chat / Inference Panel tab.
Streams /api/chat responses with full conversation history,
system prompt editor, temperature control, and model selector.

Upgrades (v4.1):
  - Persistent chat history via ChatHistoryDB (SQLite)
  - Session browser sidebar (rename, delete, resume any session)
  - Auto-title from first user message
  - Voice Gateway TTS (POST http://192.168.0.163:8050/tts) — toggleable
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

import time
import threading
import requests as _requests
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QThread, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QTextEdit, QVBoxLayout, QWidget, QMessageBox,
)

from logger import log
from utils import human_bytes, parse_time
from workers import Worker, ChatWorker
from widgets import SectionLabel, Separator
from prompt_library import PromptLibrary
from chat_history_db import ChatHistoryDB, ChatSession


# ── TTS worker ────────────────────────────────────────────────────────────

class TTSWorker(QObject):
    """Fire-and-forget POST to Voice Gateway in a background thread."""
    done   = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, text: str, url: str, method: str = "POST", payload_key: str = "text"):
        super().__init__()
        self.text        = text
        self.url         = url
        self.method      = method
        self.payload_key = payload_key

    @pyqtSlot()
    def run(self):
        try:
            fn = getattr(_requests, self.method.lower())
            r = fn(self.url, json={self.payload_key: self.text}, timeout=30)
            r.raise_for_status()
            self.done.emit()
        except Exception as e:
            self.failed.emit(str(e))


# ── Bubble widget ─────────────────────────────────────────────────────────

class MessageBubble(QFrame):
    """A single chat message rendered as a styled bubble."""

    ROLE_STYLES = {
        "user": {
            "frame":  "background:#1e2a3d; border:1px solid #2a3d5c; border-radius:8px;",
            "label":  "color:#60a5fa; font-size:11px; font-weight:700; letter-spacing:0.5px;",
            "body":   "color:#e2e8f0; font-size:13px; background:transparent; border:none; padding:0;",
        },
        "assistant": {
            "frame":  "background:#111827; border:1px solid #1e2533; border-radius:8px;",
            "label":  "color:#10b981; font-size:11px; font-weight:700; letter-spacing:0.5px;",
            "body":   "color:#cbd5e1; font-size:13px; background:transparent; border:none; padding:0; "
                      "font-family: 'Cascadia Code', 'Consolas', monospace;",
        },
        "system": {
            "frame":  "background:#0d1117; border:1px solid #1e2533; border-radius:6px;",
            "label":  "color:#64748b; font-size:11px; font-weight:700; letter-spacing:0.5px;",
            "body":   "color:#4b5563; font-size:12px; background:transparent; border:none; padding:0; font-style:italic;",
        },
    }

    def __init__(self, role: str, content: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        styles = self.ROLE_STYLES.get(role, self.ROLE_STYLES["assistant"])
        self.setStyleSheet(f"QFrame {{ {styles['frame']} }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        label_text = {"user": "YOU", "assistant": "ASSISTANT", "system": "SYSTEM"}.get(role, role.upper())
        label = QLabel(label_text)
        label.setStyleSheet(styles["label"])
        layout.addWidget(label)

        self.body = QTextEdit()
        self.body.setReadOnly(True)
        self.body.setStyleSheet(styles["body"])
        self.body.setPlainText(content)
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.document().contentsChanged.connect(self._resize_to_content)
        layout.addWidget(self.body)
        self._resize_to_content()

    def append_text(self, text: str):
        cursor = self.body.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._resize_to_content()

    def get_content(self) -> str:
        return self.body.toPlainText()

    def _resize_to_content(self):
        doc_height = int(self.body.document().size().height())
        self.body.setFixedHeight(max(doc_height + 4, 24))


# ── Session list item ─────────────────────────────────────────────────────

class SessionItem(QListWidgetItem):
    def __init__(self, session: ChatSession):
        super().__init__()
        self.session_id = session.id
        self._update(session)

    def _update(self, session: ChatSession):
        date = session.updated_at[:10] if session.updated_at else ""
        self.setText(f"{session.title}\n{session.model}  ·  {date}  ·  {session.message_count} msgs")
        self.setToolTip(f"Session #{session.id} — {session.model}")


# ── Chat Mixin ────────────────────────────────────────────────────────────

class ChatMixin:

    def _tab_chat(self) -> QWidget:
        # Initialise DB
        self._chat_db = ChatHistoryDB()
        self._chat_session_id: int | None = None

        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top toolbar ───────────────────────────────────────────────
        toolbar = QWidget()
        toolbar.setStyleSheet("background-color:#080c13; border-bottom:1px solid #1e2533;")
        toolbar.setFixedHeight(48)
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(16, 0, 16, 0)
        tb.setSpacing(12)

        model_lbl = QLabel("MODEL")
        model_lbl.setStyleSheet("color:#4b5563; font-size:11px; font-weight:700; letter-spacing:0.5px;")
        tb.addWidget(model_lbl)

        self.chat_model_combo = QComboBox()
        self.chat_model_combo.setMinimumWidth(220)
        self.chat_model_combo.setEditable(False)
        self.chat_model_combo.setPlaceholderText("Connect to Ollama first…")
        tb.addWidget(self.chat_model_combo)

        tb.addWidget(Separator(vertical=True))

        temp_lbl = QLabel("TEMP")
        temp_lbl.setStyleSheet("color:#4b5563; font-size:11px; font-weight:700; letter-spacing:0.5px;")
        self.chat_temp_input = QLineEdit("0.7")
        self.chat_temp_input.setFixedWidth(50)
        self.chat_temp_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(temp_lbl)
        tb.addWidget(self.chat_temp_input)

        ctx_lbl = QLabel("CTX")
        ctx_lbl.setStyleSheet("color:#4b5563; font-size:11px; font-weight:700; letter-spacing:0.5px;")
        self.chat_ctx_input = QLineEdit("4096")
        self.chat_ctx_input.setFixedWidth(60)
        self.chat_ctx_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(ctx_lbl)
        tb.addWidget(self.chat_ctx_input)

        tb.addWidget(Separator(vertical=True))

        self.chat_new_btn = QPushButton("+ New")
        self.chat_new_btn.setMinimumWidth(70)
        self.chat_new_btn.clicked.connect(self._chat_new_conversation)
        tb.addWidget(self.chat_new_btn)

        self.chat_lib_prompt_btn = QPushButton("📚 Prompt")
        self.chat_lib_prompt_btn.setMinimumWidth(90)
        self.chat_lib_prompt_btn.setToolTip("Pick a prompt from the library")
        self.chat_lib_prompt_btn.clicked.connect(
            lambda: self._chat_open_picker("prompt"))
        tb.addWidget(self.chat_lib_prompt_btn)

        self.chat_lib_sys_btn = QPushButton("📚 System")
        self.chat_lib_sys_btn.setMinimumWidth(90)
        self.chat_lib_sys_btn.setToolTip("Pick a system prompt from the library")
        self.chat_lib_sys_btn.clicked.connect(
            lambda: self._chat_open_picker("system"))
        tb.addWidget(self.chat_lib_sys_btn)

        self.chat_export_btn = QPushButton("⬇  Export")
        self.chat_export_btn.setMinimumWidth(85)
        self.chat_export_btn.setToolTip("Export conversation to Markdown or JSON")
        self.chat_export_btn.setEnabled(False)
        self.chat_export_btn.clicked.connect(self._chat_export)
        tb.addWidget(self.chat_export_btn)

        # TTS toggle — default from settings
        self.chat_tts_chk = QCheckBox("🔊 TTS")
        self.chat_tts_chk.setToolTip(
            "Send assistant replies to Voice Gateway (configure in ⚙ Settings)")
        self.chat_tts_chk.setStyleSheet("color:#94a3b8; font-size:11px;")
        if hasattr(self, "_settings") and self._settings.get("chat_tts_auto"):
            self.chat_tts_chk.setChecked(True)
        tb.addWidget(self.chat_tts_chk)

        self.chat_token_lbl = QLabel("")
        self.chat_token_lbl.setStyleSheet("color:#374151; font-size:11px;")
        tb.addStretch(1)
        tb.addWidget(self.chat_token_lbl)

        root.addWidget(toolbar)

        # ── Main area: sidebar + conversation ─────────────────────────
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.setHandleWidth(1)

        # ── Session sidebar ───────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setStyleSheet("background-color:#080c13; border-right:1px solid #1e2533;")
        sidebar.setMinimumWidth(180)
        sidebar.setMaximumWidth(280)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(8, 10, 8, 8)
        sl.setSpacing(6)

        sid_hdr = QLabel("HISTORY")
        sid_hdr.setStyleSheet("color:#374151; font-size:10px; font-weight:700; letter-spacing:0.8px;")
        sl.addWidget(sid_hdr)

        self.chat_session_list = QListWidget()
        self.chat_session_list.setStyleSheet("""
            QListWidget {
                background:#080c13; border:none; color:#94a3b8; font-size:11px;
            }
            QListWidget::item {
                padding:6px 4px; border-radius:4px; border-bottom:1px solid #111827;
            }
            QListWidget::item:selected {
                background:#1e2a3d; color:#e2e8f0;
            }
            QListWidget::item:hover {
                background:#111827;
            }
        """)
        self.chat_session_list.itemClicked.connect(self._chat_load_session_from_item)
        sl.addWidget(self.chat_session_list, 1)

        sid_btns = QHBoxLayout()
        self.chat_rename_btn = QPushButton("✏")
        self.chat_rename_btn.setFixedWidth(32)
        self.chat_rename_btn.setToolTip("Rename session")
        self.chat_rename_btn.clicked.connect(self._chat_rename_session)
        self.chat_del_session_btn = QPushButton("🗑")
        self.chat_del_session_btn.setFixedWidth(32)
        self.chat_del_session_btn.setToolTip("Delete session")
        self.chat_del_session_btn.clicked.connect(self._chat_delete_session)
        sid_btns.addWidget(self.chat_rename_btn)
        sid_btns.addWidget(self.chat_del_session_btn)
        sid_btns.addStretch(1)
        sl.addLayout(sid_btns)

        main_splitter.addWidget(sidebar)

        # ── Right pane: splitter (history | system prompt) ────────────
        right_pane = QWidget()
        rp = QVBoxLayout(right_pane)
        rp.setContentsMargins(0, 0, 0, 0)
        rp.setSpacing(0)

        conv_splitter = QSplitter(Qt.Orientation.Vertical)
        conv_splitter.setHandleWidth(1)

        history_container = QWidget()
        history_container.setStyleSheet("background-color:#0a0f18;")
        history_layout = QVBoxLayout(history_container)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(0)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_scroll.setStyleSheet("QScrollArea { background-color:#0a0f18; border:none; }")

        self.chat_bubbles_widget = QWidget()
        self.chat_bubbles_widget.setStyleSheet("background-color:#0a0f18;")
        self.chat_bubbles_layout = QVBoxLayout(self.chat_bubbles_widget)
        self.chat_bubbles_layout.setContentsMargins(16, 16, 16, 16)
        self.chat_bubbles_layout.setSpacing(10)
        self.chat_bubbles_layout.addStretch(1)

        self.chat_scroll.setWidget(self.chat_bubbles_widget)
        history_layout.addWidget(self.chat_scroll)
        conv_splitter.addWidget(history_container)

        sys_container = QWidget()
        sys_container.setStyleSheet("background-color:#0e1117; border-top:1px solid #1e2533;")
        sys_container.setFixedHeight(90)
        sys_l = QVBoxLayout(sys_container)
        sys_l.setContentsMargins(16, 8, 16, 8)
        sys_l.setSpacing(4)
        sys_hdr = QHBoxLayout()
        sys_hdr.addWidget(SectionLabel("System Prompt"))
        self.chat_sys_clear_btn = QPushButton("Clear")
        self.chat_sys_clear_btn.setFixedWidth(60)
        self.chat_sys_clear_btn.setFixedHeight(22)
        self.chat_sys_clear_btn.setStyleSheet("font-size:11px;")
        self.chat_sys_clear_btn.clicked.connect(lambda: self.chat_system_input.clear())
        sys_hdr.addStretch(1)
        sys_hdr.addWidget(self.chat_sys_clear_btn)
        sys_l.addLayout(sys_hdr)
        self.chat_system_input = QTextEdit()
        self.chat_system_input.setPlaceholderText(
            "Optional system prompt — sets the model's persona and instructions…")
        self.chat_system_input.setStyleSheet(
            "QTextEdit { background-color:#080c13; border:none; color:#64748b; "
            "font-size:12px; padding:4px; }")
        sys_l.addWidget(self.chat_system_input)
        conv_splitter.addWidget(sys_container)
        conv_splitter.setSizes([600, 90])

        rp.addWidget(conv_splitter, 1)

        # ── Input bar ─────────────────────────────────────────────────
        input_bar = QWidget()
        input_bar.setStyleSheet("background-color:#080c13; border-top:1px solid #1e2533;")
        input_bar.setMinimumHeight(64)
        il = QHBoxLayout(input_bar)
        il.setContentsMargins(16, 10, 16, 10)
        il.setSpacing(10)

        self.chat_input = QTextEdit()
        self.chat_input.setPlaceholderText("Type a message… (Ctrl+Enter to send)")
        self.chat_input.setStyleSheet(
            "QTextEdit { background-color:#111827; border:1px solid #1f2937; "
            "border-radius:8px; color:#e2e8f0; font-size:13px; padding:8px 12px; }"
            "QTextEdit:focus { border-color:#3b82f6; }"
        )
        self.chat_input.setMaximumHeight(120)
        self.chat_input.setMinimumHeight(44)
        self.chat_input.installEventFilter(self)
        il.addWidget(self.chat_input, 1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        self.chat_send_btn = QPushButton("Send")
        self.chat_send_btn.setObjectName("btn_primary")
        self.chat_send_btn.setMinimumWidth(80)
        self.chat_send_btn.setMinimumHeight(36)
        self.chat_send_btn.clicked.connect(self._chat_send)
        self.chat_stop_btn = QPushButton("Stop")
        self.chat_stop_btn.setObjectName("btn_cancel")
        self.chat_stop_btn.setMinimumWidth(80)
        self.chat_stop_btn.setEnabled(False)
        self.chat_stop_btn.clicked.connect(self._chat_stop)
        btn_col.addWidget(self.chat_send_btn)
        btn_col.addWidget(self.chat_stop_btn)
        il.addLayout(btn_col)
        rp.addWidget(input_bar)

        main_splitter.addWidget(right_pane)
        main_splitter.setSizes([200, 900])
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        root.addWidget(main_splitter, 1)

        # Internal state
        self._chat_history: list[dict] = []
        self._chat_worker = None
        self._chat_active_bubble: MessageBubble | None = None
        self._chat_total_tokens = 0
        self._chat_tts_thread: QThread | None = None

        # Load session list on startup
        QTimer.singleShot(0, self._chat_refresh_session_list)

        return container

    # ── Session management ────────────────────────────────────────────────

    def _chat_refresh_session_list(self):
        self.chat_session_list.clear()
        sessions = self._chat_db.list_sessions(limit=100)
        for s in sessions:
            item = SessionItem(s)
            self.chat_session_list.addItem(item)
            # Highlight current session
            if self._chat_session_id and s.id == self._chat_session_id:
                self.chat_session_list.setCurrentItem(item)

    def _chat_load_session_from_item(self, item: QListWidgetItem):
        if not isinstance(item, SessionItem):
            return
        self._chat_load_session(item.session_id)

    def _chat_load_session(self, session_id: int):
        session = self._chat_db.get_session(session_id)
        if not session:
            return
        self._chat_session_id = session_id

        # Restore model combo
        idx = self.chat_model_combo.findText(session.model)
        if idx >= 0:
            self.chat_model_combo.setCurrentIndex(idx)

        # Restore system prompt
        self.chat_system_input.setPlainText(session.system_prompt)

        # Clear bubbles
        self._chat_clear_bubbles()
        self._chat_history = []
        self._chat_total_tokens = 0
        self.chat_token_lbl.setText("")

        # Reload messages
        messages = self._chat_db.get_messages(session_id)
        for msg in messages:
            self._chat_add_bubble(msg.role, msg.content)
            self._chat_history.append({"role": msg.role, "content": msg.content})

        self.chat_export_btn.setEnabled(bool(self._chat_history))
        self._set_status(f"Loaded session: {session.title}")
        log.info("ChatMixin: loaded session %d (%s)", session_id, session.title)

    def _chat_rename_session(self):
        item = self.chat_session_list.currentItem()
        if not isinstance(item, SessionItem):
            return
        new_title, ok = QInputDialog.getText(
            self, "Rename Session", "New title:", text=item.text().split("\n")[0])
        if ok and new_title.strip():
            self._chat_db.update_session_title(item.session_id, new_title.strip())
            self._chat_refresh_session_list()

    def _chat_delete_session(self):
        item = self.chat_session_list.currentItem()
        if not isinstance(item, SessionItem):
            return
        title = item.text().split("\n")[0]
        reply = QMessageBox.question(
            self, "Delete Session",
            f"Delete session '{title}'? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._chat_db.delete_session(item.session_id)
        if self._chat_session_id == item.session_id:
            self._chat_new_conversation()
        else:
            self._chat_refresh_session_list()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _chat_populate_models(self):
        current = self.chat_model_combo.currentText()
        self.chat_model_combo.blockSignals(True)
        self.chat_model_combo.clear()
        for row in self.installed_model.rows():
            self.chat_model_combo.addItem(row.name)
        if current:
            idx = self.chat_model_combo.findText(current)
            if idx >= 0:
                self.chat_model_combo.setCurrentIndex(idx)
        self.chat_model_combo.blockSignals(False)

    def _chat_new_conversation(self):
        self._chat_session_id = None
        self._chat_history = []
        self._chat_total_tokens = 0
        self.chat_token_lbl.setText("")
        self.chat_export_btn.setEnabled(False)
        self._chat_clear_bubbles()
        self._chat_refresh_session_list()
        self._set_status("New conversation started.")
        log.info("ChatMixin: new conversation")

    def _chat_clear_bubbles(self):
        while self.chat_bubbles_layout.count() > 1:
            item = self.chat_bubbles_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

    def _chat_add_bubble(self, role: str, content: str = "") -> MessageBubble:
        bubble = MessageBubble(role, content)
        self.chat_bubbles_layout.insertWidget(
            self.chat_bubbles_layout.count(), bubble)
        QTimer.singleShot(50, self._chat_scroll_to_bottom)
        return bubble

    def _chat_scroll_to_bottom(self):
        sb = self.chat_scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _chat_get_params(self) -> tuple[float | None, int | None]:
        try:
            temp = float(self.chat_temp_input.text().strip())
        except ValueError:
            temp = None
        try:
            ctx = int(self.chat_ctx_input.text().strip())
        except ValueError:
            ctx = None
        return temp, ctx

    def _chat_auto_title(self, user_text: str) -> str:
        """Generate a short title from the first ~50 chars of the first message."""
        title = user_text.strip().replace("\n", " ")[:50]
        if len(user_text.strip()) > 50:
            title += "…"
        return title or "New Chat"

    # ── TTS ───────────────────────────────────────────────────────────────

    def _chat_speak(self, text: str):
        """Send text to Voice Gateway TTS in a fire-and-forget thread."""
        if not self.chat_tts_chk.isChecked():
            return
        if not self._settings.get("vg_enabled"):
            log.warning("ChatMixin: TTS checkbox on but VG disabled in Settings")
            return
        if self._chat_tts_thread and self._chat_tts_thread.isRunning():
            return  # previous TTS still going — skip rather than queue

        url     = self._settings.vg_full_url
        method  = self._settings.get("vg_method") or "POST"
        pk      = self._settings.get("vg_payload_key") or "text"

        worker = TTSWorker(text, url=url, method=method, payload_key=pk)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        worker.failed.connect(lambda e: log.warning("TTS failed: %s", e))
        worker.failed.connect(thread.quit)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: setattr(self, "_chat_tts_thread", None))
        self._chat_tts_thread = thread
        thread.start()
        log.info("ChatMixin: TTS dispatched to %s (%d chars)", url, len(text))

    # ── Export ────────────────────────────────────────────────────────────

    def _chat_export(self):
        if not self._chat_history:
            return
        from PyQt6.QtWidgets import QFileDialog
        from datetime import datetime as _dt
        import json as _json

        ts    = _dt.now().strftime("%Y%m%d_%H%M%S")
        model = self.chat_model_combo.currentText().replace(":", "_").replace("/", "_")
        default = str(Path.home() / f"chat_{model}_{ts}")

        path, filt = QFileDialog.getSaveFileName(
            self, "Export Conversation", default,
            "Markdown (*.md);;JSON (*.json);;Plain text (*.txt)"
        )
        if not path:
            return

        try:
            if "JSON" in filt or path.endswith(".json"):
                if not path.endswith(".json"):
                    path += ".json"
                payload = {
                    "model":     self.chat_model_combo.currentText(),
                    "exported":  _dt.now().isoformat(),
                    "system":    self.chat_system_input.toPlainText().strip(),
                    "messages":  self._chat_history,
                }
                Path(path).write_text(
                    _json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

            elif "Plain" in filt or path.endswith(".txt"):
                if not path.endswith(".txt"):
                    path += ".txt"
                lines = []
                system = self.chat_system_input.toPlainText().strip()
                if system:
                    lines += [f"[System]\n{system}", ""]
                for msg in self._chat_history:
                    lines += [f"[{msg['role'].upper()}]", msg["content"], ""]
                Path(path).write_text("\n".join(lines), encoding="utf-8")

            else:
                if not path.endswith(".md"):
                    path += ".md"
                lines = []
                model_name = self.chat_model_combo.currentText()
                lines.append(
                    f"# Conversation — {model_name}  \n"
                    f"*Exported {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}*\n"
                )
                system = self.chat_system_input.toPlainText().strip()
                if system:
                    lines.append(
                        f"---\n\n**System prompt:**\n\n"
                        f"> {system.replace(chr(10), chr(10)+'> ')}\n"
                    )
                lines.append("---\n")
                ROLE_LABELS = {
                    "user":      "**You**",
                    "assistant": f"**{model_name}**",
                    "system":    "_System_",
                }
                for msg in self._chat_history:
                    role  = msg["role"]
                    label = ROLE_LABELS.get(role, f"**{role}**")
                    body  = msg["content"].strip()
                    if role == "assistant":
                        body = f"```\n{body}\n```"
                    lines.append(f"{label}\n\n{body}\n")
                    lines.append("---\n")
                Path(path).write_text("\n".join(lines), encoding="utf-8")

            self._set_status(f"Conversation exported to {Path(path).name}")
            log.info("ChatMixin: exported to %s", path)

        except Exception as e:
            self._err("Export Failed", str(e))
            log.exception("ChatMixin: export failed: %s", e)

    def _chat_open_picker(self, kind: str):
        from tab_prompts import PromptPickerDialog
        title = ("Pick a System Prompt" if kind == "system" else "Pick a Prompt")
        dlg = PromptPickerDialog(
            self._prompt_library, kind=kind, title=title, parent=self)
        def _on_selected(entry):
            if entry.kind == "system":
                self.chat_system_input.setPlainText(entry.content)
            else:
                self.chat_input.setPlainText(entry.content)
            self._set_status(
                f"'{entry.title}' loaded into "
                f"{'system prompt' if entry.kind == 'system' else 'message input'}.")
        dlg.selected.connect(_on_selected)
        dlg.exec()

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent
        if (obj is self.chat_input
                and event.type() == QEvent.Type.KeyPress
                and isinstance(event, QKeyEvent)):
            if (event.key() == Qt.Key.Key_Return
                    and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._chat_send()
                return True
        return super().eventFilter(obj, event)

    # ── Send / Stop ───────────────────────────────────────────────────────

    def _chat_send(self):
        user_text = self.chat_input.toPlainText().strip()
        if not user_text:
            return

        model = self.chat_model_combo.currentText().strip()
        if not model:
            self._warn("No Model", "Select a model from the dropdown first.")
            return

        if self._chat_worker is not None:
            self._warn("Busy", "A response is already streaming. Stop it first.")
            return

        system = self.chat_system_input.toPlainText().strip()
        temp, ctx = self._chat_get_params()

        # Create DB session on first message
        if self._chat_session_id is None:
            title = self._chat_auto_title(user_text)
            self._chat_session_id = self._chat_db.create_session(
                model=model, system_prompt=system, title=title)
            self._chat_refresh_session_list()

        # Persist and display user message
        self._chat_db.add_message(self._chat_session_id, "user", user_text)
        self._chat_history.append({"role": "user", "content": user_text})
        self._chat_add_bubble("user", user_text)
        self.chat_input.clear()

        # Empty assistant bubble to stream into
        self._chat_active_bubble = self._chat_add_bubble("assistant", "")

        self.chat_send_btn.setEnabled(False)
        self.chat_stop_btn.setEnabled(True)
        self._set_status(f"Streaming from {model}…")
        log.info("ChatMixin: send  model=%s  history_len=%d", model, len(self._chat_history))

        worker = ChatWorker(
            self.client, model,
            messages=list(self._chat_history),
            system=system,
            temperature=temp,
            num_ctx=ctx,
        )
        worker.token.connect(self._chat_on_token)
        worker.finished.connect(self._chat_on_finished)
        worker.failed.connect(self._chat_on_failed)

        self._chat_worker = worker
        worker.finished.connect(lambda _: setattr(self, "_chat_worker", None))
        worker.failed.connect(lambda _: setattr(self, "_chat_worker", None))
        self._start_worker(worker)

    def _chat_stop(self):
        if self._chat_worker:
            self._chat_worker.stop()
        self.chat_stop_btn.setEnabled(False)
        self._set_status("Response stopped.")
        log.info("ChatMixin: stop requested")

    # ── Worker callbacks ──────────────────────────────────────────────────

    def _chat_on_token(self, text: str):
        if self._chat_active_bubble:
            self._chat_active_bubble.append_text(text)
            self._chat_scroll_to_bottom()

    def _chat_on_finished(self, last_obj: dict):
        self.chat_send_btn.setEnabled(True)
        self.chat_stop_btn.setEnabled(False)
        self.chat_export_btn.setEnabled(bool(self._chat_history))

        if self._chat_active_bubble:
            content = self._chat_active_bubble.get_content()
            if content:
                self._chat_history.append({"role": "assistant", "content": content})
                # Persist to DB
                if self._chat_session_id is not None:
                    self._chat_db.add_message(
                        self._chat_session_id, "assistant", content)
                    self._chat_refresh_session_list()
                # Speak via Voice Gateway if TTS enabled
                self._chat_speak(content)

        eval_count   = last_obj.get("eval_count", 0) or 0
        prompt_count = last_obj.get("prompt_eval_count", 0) or 0
        self._chat_total_tokens += eval_count + prompt_count
        eval_dur_ns  = last_obj.get("eval_duration", 0) or 1
        tps = eval_count / (eval_dur_ns / 1e9) if eval_count and eval_dur_ns else 0

        self.chat_token_lbl.setText(
            f"{self._chat_total_tokens:,} tokens  ·  {tps:.1f} tok/s"
            if tps else f"{self._chat_total_tokens:,} tokens"
        )
        self._set_status("Response complete.")
        self._chat_active_bubble = None
        log.info("ChatMixin: finished  eval=%d  tps=%.1f", eval_count, tps)

    def _chat_on_failed(self, msg: str):
        self.chat_send_btn.setEnabled(True)
        self.chat_stop_btn.setEnabled(False)
        if self._chat_active_bubble:
            self._chat_active_bubble.append_text(f"\n[Error: {msg}]")
        self._err("Chat Failed", msg)
        self._set_status("Chat error.")
        self._chat_active_bubble = None
        log.error("ChatMixin: failed: %s", msg)
