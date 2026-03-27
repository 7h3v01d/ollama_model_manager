"""
Mixin — Prompt Library tab.
Browse, create, edit, delete, tag-filter and use prompts and
system prompts. Integrates with the Chat tab via inject callbacks.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from logger import log
from prompt_library import PromptLibrary, PromptEntry
from widgets import SectionLabel, Separator


# ── Prompt card (used in picker dialog) ──────────────────────────────────

class PromptCard(QFrame):
    use_clicked    = pyqtSignal(object)   # PromptEntry
    edit_clicked   = pyqtSignal(object)
    delete_clicked = pyqtSignal(object)

    def __init__(self, entry: PromptEntry, compact: bool = False, parent=None):
        super().__init__(parent)
        self.entry = entry
        self._compact = compact
        KIND_COLORS = {"prompt": "#3b82f6", "system": "#10b981"}
        color = KIND_COLORS.get(entry.kind, "#64748b")
        self.setStyleSheet(
            f"QFrame {{ background-color:#111827; border:1px solid #1e2533; "
            f"border-left: 3px solid {color}; border-radius:8px; }}"
            f"QFrame:hover {{ border-color:#3b5580; border-left-color:{color}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        # Header row
        hdr = QHBoxLayout()
        title_lbl = QLabel(entry.title)
        title_lbl.setStyleSheet(
            "color:#e2e8f0; font-size:13px; font-weight:700; "
            "background:transparent; border:none;")
        hdr.addWidget(title_lbl, 1)

        kind_lbl = QLabel(entry.kind.upper())
        kind_lbl.setStyleSheet(
            f"color:{color}; font-size:10px; font-weight:700; "
            f"background:transparent; border:none; letter-spacing:0.5px;")
        hdr.addWidget(kind_lbl)

        if entry.use_count:
            use_lbl = QLabel(f"↑{entry.use_count}")
            use_lbl.setStyleSheet(
                "color:#374151; font-size:10px; background:transparent; border:none;")
            hdr.addWidget(use_lbl)
        layout.addLayout(hdr)

        # Content preview
        preview = entry.content[:120].replace("\n", " ")
        if len(entry.content) > 120:
            preview += "…"
        prev_lbl = QLabel(preview)
        prev_lbl.setWordWrap(True)
        prev_lbl.setStyleSheet(
            "color:#4b5563; font-size:11px; background:transparent; border:none;")
        layout.addWidget(prev_lbl)

        # Tags
        if entry.tags:
            tags_row = QHBoxLayout()
            tags_row.setSpacing(4)
            for tag in entry.tags[:6]:
                t = QLabel(tag)
                t.setStyleSheet(
                    "background:#1e2a3d; border:1px solid #2a3d5c; border-radius:3px;"
                    "color:#60a5fa; font-size:10px; padding:1px 6px;")
                tags_row.addWidget(t)
            tags_row.addStretch(1)
            layout.addLayout(tags_row)

        if not compact:
            # Action buttons
            btn_row = QHBoxLayout()
            btn_row.setSpacing(6)

            use_btn = QPushButton("↗  Use")
            use_btn.setObjectName("btn_primary")
            use_btn.setFixedHeight(26)
            use_btn.setStyleSheet("font-size:11px;")
            use_btn.clicked.connect(lambda: self.use_clicked.emit(self.entry))

            edit_btn = QPushButton("Edit")
            edit_btn.setFixedHeight(26)
            edit_btn.setStyleSheet("font-size:11px;")
            edit_btn.clicked.connect(lambda: self.edit_clicked.emit(self.entry))

            del_btn = QPushButton("✕")
            del_btn.setObjectName("btn_danger")
            del_btn.setFixedHeight(26)
            del_btn.setFixedWidth(30)
            del_btn.setStyleSheet("font-size:11px;")
            del_btn.clicked.connect(lambda: self.delete_clicked.emit(self.entry))

            btn_row.addWidget(use_btn)
            btn_row.addStretch(1)
            btn_row.addWidget(edit_btn)
            btn_row.addWidget(del_btn)
            layout.addLayout(btn_row)


# ── Picker dialog (invoked from Chat tab) ─────────────────────────────────

class PromptPickerDialog(QDialog):
    """
    Modal dialog for picking a prompt or system prompt from the library.
    Emits selected(entry) on use.
    """
    selected = pyqtSignal(object)   # PromptEntry

    def __init__(self, library: PromptLibrary, kind: str = "",
                 title: str = "Pick a Prompt", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(680, 560)
        self.setStyleSheet(
            "QDialog { background-color:#111827; border:1px solid #1e2533; }")
        self._library = library
        self._kind = kind

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # Search + filter row
        top = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search prompts…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._refresh)

        self._tag_combo = QComboBox()
        self._tag_combo.setMinimumWidth(120)
        self._tag_combo.addItem("All tags", "")
        for tag in library.all_tags():
            self._tag_combo.addItem(tag, tag)
        self._tag_combo.currentIndexChanged.connect(self._refresh)

        top.addWidget(self._search, 1)
        top.addWidget(self._tag_combo)
        layout.addLayout(top)

        layout.addWidget(Separator())

        # Scrollable card list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 8, 0)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch(1)
        self._scroll.setWidget(self._cards_widget)
        layout.addWidget(self._scroll, 1)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("btn_cancel")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

        self._refresh()

    def _refresh(self):
        query  = self._search.text().strip()
        tag    = self._tag_combo.currentData() or ""
        kind   = self._kind

        if query:
            entries = self._library.search(query, kind=kind)
        elif tag:
            entries = self._library.by_tag(tag)
            if kind:
                entries = [e for e in entries if e.kind == kind]
        else:
            entries = self._library.all(kind=kind)

        # Clear existing cards
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for entry in entries:
            card = PromptCard(entry, compact=True)
            card.setStyleSheet(
                card.styleSheet() + " QFrame { cursor: pointer; }")

            # Clicking anywhere on card uses it
            def _make_use(e):
                def _use():
                    self._library.record_use(e.id)
                    self.selected.emit(e)
                    self.accept()
                return _use

            card.mousePressEvent = lambda ev, e=entry, fn=_make_use(entry): fn()
            self._cards_layout.insertWidget(
                self._cards_layout.count() - 1, card)

        if not entries:
            lbl = QLabel("No prompts match." if query or tag
                         else "Library is empty.")
            lbl.setStyleSheet("color:#374151; font-size:13px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._cards_layout.insertWidget(0, lbl)


# ── Edit / Create dialog ──────────────────────────────────────────────────

class PromptEditDialog(QDialog):
    def __init__(self, entry: PromptEntry | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Prompt" if entry else "New Prompt")
        self.setModal(True)
        self.resize(600, 460)
        self.setStyleSheet(
            "QDialog { background-color:#111827; border:1px solid #1e2533; }")

        self._entry = entry or PromptEntry(title="", content="")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        fl = QFormLayout()
        fl.setSpacing(10)

        self.title_input = QLineEdit(self._entry.title)
        self.title_input.setPlaceholderText("Short descriptive title")

        self.kind_combo = QComboBox()
        self.kind_combo.addItem("Prompt  (injected into message input)", "prompt")
        self.kind_combo.addItem("System  (injected into system prompt)", "system")
        idx = 0 if self._entry.kind == "prompt" else 1
        self.kind_combo.setCurrentIndex(idx)

        self.tags_input = QLineEdit(self._entry.tags_str)
        self.tags_input.setPlaceholderText("comma separated e.g.  code, python, review")

        self.notes_input = QLineEdit(self._entry.notes)
        self.notes_input.setPlaceholderText("Optional private note")

        fl.addRow("Title:",  self.title_input)
        fl.addRow("Kind:",   self.kind_combo)
        fl.addRow("Tags:",   self.tags_input)
        fl.addRow("Notes:",  self.notes_input)
        layout.addLayout(fl)

        layout.addWidget(SectionLabel("Content"))
        self.content_input = QTextEdit(self._entry.content)
        self.content_input.setPlaceholderText("The prompt or system prompt text…")
        self.content_input.setStyleSheet(
            "QTextEdit { background-color:#080c13; border:1px solid #1e2533; "
            "border-radius:6px; color:#e2e8f0; font-size:13px; padding:10px; }")
        layout.addWidget(self.content_input, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_save(self):
        title   = self.title_input.text().strip()
        content = self.content_input.toPlainText().strip()
        if not title:
            QMessageBox.warning(self, "Missing Title", "Please enter a title.")
            return
        if not content:
            QMessageBox.warning(self, "Missing Content", "Please enter the prompt content.")
            return
        self._entry.title   = title
        self._entry.content = content
        self._entry.kind    = self.kind_combo.currentData()
        self._entry.tags    = PromptEntry._parse_tags(self.tags_input.text())
        self._entry.notes   = self.notes_input.text().strip()
        self.accept()

    def result_entry(self) -> PromptEntry:
        return self._entry


# ── Prompts Mixin ─────────────────────────────────────────────────────────

class PromptsMixin:

    def _tab_prompts(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── Toolbar ───────────────────────────────────────────────────
        tb = QHBoxLayout()

        self.plib_search = QLineEdit()
        self.plib_search.setPlaceholderText("Search titles, content, tags…")
        self.plib_search.setClearButtonEnabled(True)
        self.plib_search.setMaximumWidth(320)
        self.plib_search.textChanged.connect(self._plib_refresh)

        self.plib_kind_combo = QComboBox()
        self.plib_kind_combo.addItem("All",     "")
        self.plib_kind_combo.addItem("Prompts", "prompt")
        self.plib_kind_combo.addItem("System",  "system")
        self.plib_kind_combo.currentIndexChanged.connect(self._plib_refresh)

        self.plib_tag_combo = QComboBox()
        self.plib_tag_combo.setMinimumWidth(130)
        self.plib_tag_combo.addItem("All tags", "")
        self.plib_tag_combo.currentIndexChanged.connect(self._plib_refresh)

        self.plib_new_btn = QPushButton("+ New Prompt")
        self.plib_new_btn.setObjectName("btn_primary")
        self.plib_new_btn.setMinimumWidth(120)
        self.plib_new_btn.clicked.connect(self._plib_new)

        self.plib_count_lbl = QLabel("")
        self.plib_count_lbl.setObjectName("label_muted")

        tb.addWidget(self.plib_search)
        tb.addWidget(self.plib_kind_combo)
        tb.addWidget(self.plib_tag_combo)
        tb.addStretch(1)
        tb.addWidget(self.plib_count_lbl)
        tb.addWidget(self.plib_new_btn)
        layout.addLayout(tb)

        layout.addWidget(Separator())

        # ── Two-column card grid via splitter ─────────────────────────
        self.plib_scroll = QScrollArea()
        self.plib_scroll.setWidgetResizable(True)
        self.plib_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.plib_cards_widget = QWidget()
        self.plib_cards_layout = QVBoxLayout(self.plib_cards_widget)
        self.plib_cards_layout.setContentsMargins(0, 0, 8, 0)
        self.plib_cards_layout.setSpacing(8)
        self.plib_cards_layout.addStretch(1)

        self.plib_scroll.setWidget(self.plib_cards_widget)
        layout.addWidget(self.plib_scroll)

        db_lbl = QLabel(f"Database: {self._prompt_library.db_path()}")
        db_lbl.setStyleSheet(
            "color:#1f2937; font-size:10px; font-family:monospace;")
        layout.addWidget(db_lbl)

        self._plib_refresh_tags()
        self._plib_refresh()
        return container

    # ── Data refresh ──────────────────────────────────────────────────

    def _plib_refresh_tags(self):
        self.plib_tag_combo.blockSignals(True)
        current = self.plib_tag_combo.currentData()
        self.plib_tag_combo.clear()
        self.plib_tag_combo.addItem("All tags", "")
        for tag in self._prompt_library.all_tags():
            self.plib_tag_combo.addItem(tag, tag)
        # restore selection
        for i in range(self.plib_tag_combo.count()):
            if self.plib_tag_combo.itemData(i) == current:
                self.plib_tag_combo.setCurrentIndex(i)
                break
        self.plib_tag_combo.blockSignals(False)

    def _plib_refresh(self):
        query = self.plib_search.text().strip()
        kind  = self.plib_kind_combo.currentData() or ""
        tag   = self.plib_tag_combo.currentData() or ""

        if query:
            entries = self._prompt_library.search(query, kind=kind)
        elif tag:
            entries = self._prompt_library.by_tag(tag)
            if kind:
                entries = [e for e in entries if e.kind == kind]
        else:
            entries = self._prompt_library.all(kind=kind)

        # Clear old cards
        while self.plib_cards_layout.count() > 1:
            item = self.plib_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Two-column grid layout
        row_layout = None
        for i, entry in enumerate(entries):
            if i % 2 == 0:
                row_layout = QHBoxLayout()
                row_layout.setSpacing(8)
                self.plib_cards_layout.insertLayout(
                    self.plib_cards_layout.count() - 1, row_layout)

            card = PromptCard(entry, compact=False)
            card.use_clicked.connect(self._plib_use)
            card.edit_clicked.connect(self._plib_edit)
            card.delete_clicked.connect(self._plib_delete)
            if row_layout is not None:
                row_layout.addWidget(card, 1)

        # Pad last row if odd count
        if entries and len(entries) % 2 == 1 and row_layout is not None:
            row_layout.addWidget(QWidget(), 1)

        if not entries:
            lbl = QLabel(
                "No prompts found.  Click '+ New Prompt' to create one."
                if not query and not tag else "No matches.")
            lbl.setStyleSheet(
                "color:#374151; font-size:13px; padding:20px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.plib_cards_layout.insertWidget(0, lbl)

        n = len(entries)
        self.plib_count_lbl.setText(
            f"{n} prompt{'s' if n != 1 else ''}")
        log.debug("PromptsMixin: refreshed  count=%d", n)

    # ── Actions ───────────────────────────────────────────────────────

    def _plib_new(self):
        dlg = PromptEditDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            entry = dlg.result_entry()
            self._prompt_library.add(entry)
            log.info("PromptsMixin: added '%s'", entry.title)
            self._plib_refresh_tags()
            self._plib_refresh()

    def _plib_edit(self, entry: PromptEntry):
        # Re-fetch to get latest from DB
        fresh = self._prompt_library.get(entry.id)
        if not fresh:
            return
        dlg = PromptEditDialog(entry=fresh, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._prompt_library.update(dlg.result_entry())
            log.info("PromptsMixin: updated '%s'", fresh.title)
            self._plib_refresh_tags()
            self._plib_refresh()

    def _plib_delete(self, entry: PromptEntry):
        r = QMessageBox.question(
            self, "Delete Prompt",
            f"Delete '{entry.title}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            self._prompt_library.delete(entry.id)
            log.info("PromptsMixin: deleted id=%d '%s'", entry.id, entry.title)
            self._plib_refresh_tags()
            self._plib_refresh()

    def _plib_use(self, entry: PromptEntry):
        """Inject a prompt into the Chat tab and switch to it."""
        self._prompt_library.record_use(entry.id)
        if entry.kind == "system":
            if hasattr(self, "chat_system_input"):
                self.chat_system_input.setPlainText(entry.content)
        else:
            if hasattr(self, "chat_input"):
                self.chat_input.setPlainText(entry.content)
        # Switch to Chat tab (index 6)
        chat_idx = next(
            (i for i in range(self.tabs.count())
             if "Chat" in self.tabs.tabText(i)), None)
        if chat_idx is not None:
            self.tabs.setCurrentIndex(chat_idx)
        self._set_status(
            f"'{entry.title}' injected into "
            f"{'system prompt' if entry.kind == 'system' else 'message input'}.")
        log.info("PromptsMixin: used '%s' kind=%s", entry.title, entry.kind)
