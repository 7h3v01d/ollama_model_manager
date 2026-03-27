"""
Mixin — Chat / Inference Panel tab.
Streams /api/chat responses with full conversation history,
system prompt editor, temperature control, and model selector.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from logger import log
from utils import human_bytes, parse_time
from workers import Worker, ChatWorker
from widgets import SectionLabel, Separator


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


# ── Chat Mixin ────────────────────────────────────────────────────────────

class ChatMixin:

    def _tab_chat(self) -> QWidget:
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

        self.chat_token_lbl = QLabel("")
        self.chat_token_lbl.setStyleSheet("color:#374151; font-size:11px;")
        tb.addStretch(1)
        tb.addWidget(self.chat_token_lbl)

        root.addWidget(toolbar)

        # ── Splitter: history | system prompt ─────────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(1)

        # Conversation history scroll area
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

        splitter.addWidget(history_container)

        # System prompt
        sys_container = QWidget()
        sys_container.setStyleSheet("background-color:#0e1117; border-top:1px solid #1e2533;")
        sys_container.setFixedHeight(90)
        sl = QVBoxLayout(sys_container)
        sl.setContentsMargins(16, 8, 16, 8)
        sl.setSpacing(4)
        sys_hdr = QHBoxLayout()
        sys_hdr.addWidget(SectionLabel("System Prompt"))
        self.chat_sys_clear_btn = QPushButton("Clear")
        self.chat_sys_clear_btn.setFixedWidth(60)
        self.chat_sys_clear_btn.setFixedHeight(22)
        self.chat_sys_clear_btn.setStyleSheet("font-size:11px;")
        self.chat_sys_clear_btn.clicked.connect(lambda: self.chat_system_input.clear())
        sys_hdr.addStretch(1)
        sys_hdr.addWidget(self.chat_sys_clear_btn)
        sl.addLayout(sys_hdr)
        self.chat_system_input = QTextEdit()
        self.chat_system_input.setPlaceholderText(
            "Optional system prompt — sets the model's persona and instructions…")
        self.chat_system_input.setStyleSheet(
            "QTextEdit { background-color:#080c13; border:none; color:#64748b; "
            "font-size:12px; padding:4px; }")
        sl.addWidget(self.chat_system_input)
        splitter.addWidget(sys_container)

        splitter.setSizes([600, 90])
        root.addWidget(splitter, 1)

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

        root.addWidget(input_bar)

        # Internal state
        self._chat_history: list[dict] = []   # {"role": ..., "content": ...}
        self._chat_worker = None
        self._chat_active_bubble: MessageBubble | None = None
        self._chat_total_tokens = 0

        return container

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
        self._chat_history = []
        self._chat_total_tokens = 0
        self.chat_token_lbl.setText("")
        # Clear bubbles (keep the stretch at index 0)
        while self.chat_bubbles_layout.count() > 1:
            item = self.chat_bubbles_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._set_status("New conversation started.")
        log.info("ChatMixin: new conversation")

    def _chat_add_bubble(self, role: str, content: str = "") -> MessageBubble:
        bubble = MessageBubble(role, content)
        # Insert before the trailing stretch
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

    # ── Send / Stop ───────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        """Ctrl+Enter sends the message from the input box."""
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

        # Add user message to history and UI
        self._chat_history.append({"role": "user", "content": user_text})
        self._chat_add_bubble("user", user_text)
        self.chat_input.clear()

        # Add empty assistant bubble to stream into
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

        # Capture completed assistant response into history
        if self._chat_active_bubble:
            content = self._chat_active_bubble.get_content()
            if content:
                self._chat_history.append({"role": "assistant", "content": content})

        # Update token count display
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
