import json
import sys
import threading
from dataclasses import dataclass
from datetime import datetime

import requests
from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableView,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)


DEFAULT_BASE_URL = "http://localhost:11434"


def human_bytes(n: int | None) -> str:
    if n is None:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024.0 or u == units[-1]:
            return f"{f:.2f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= 1024.0
    return f"{f:.2f} TB"


def parse_time(s: str | None) -> str:
    if not s:
        return ""
    try:
        # Ollama often returns RFC3339-like timestamps; handle trailing Z.
        s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(s2)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except Exception:
        return s


class OllamaClient:
    def __init__(self, base_url: str):
        self.set_base_url(base_url)

    def set_base_url(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def tags(self) -> dict:
        r = self.session.get(self._url("/api/tags"), timeout=6)
        r.raise_for_status()
        return r.json()

    def show(self, model: str) -> dict:
        r = self.session.post(self._url("/api/show"), data=json.dumps({"model": model}), timeout=10)
        r.raise_for_status()
        return r.json()

    def delete(self, model: str) -> None:
        r = self.session.delete(self._url("/api/delete"), data=json.dumps({"model": model}), timeout=15)
        r.raise_for_status()


@dataclass
class ModelRow:
    name: str
    size: int | None
    modified_at: str | None
    raw: dict


class ModelsTableModel(QAbstractTableModel):
    HEADERS = ["Model", "Size", "Modified"]

    def __init__(self):
        super().__init__()
        self._rows: list[ModelRow] = []
        self._filtered: list[ModelRow] = []
        self._filter = ""

    def set_rows(self, rows: list[ModelRow]):
        self.beginResetModel()
        self._rows = rows
        self._apply_filter_locked()
        self.endResetModel()

    def set_filter(self, text: str):
        self.beginResetModel()
        self._filter = (text or "").strip().lower()
        self._apply_filter_locked()
        self.endResetModel()

    def rows(self) -> list[ModelRow]:
        return self._filtered

    def _apply_filter_locked(self):
        if not self._filter:
            self._filtered = list(self._rows)
        else:
            f = self._filter
            self._filtered = [r for r in self._rows if f in r.name.lower()]

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._filtered)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._filtered[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return row.name
            if col == 1:
                return human_bytes(row.size)
            if col == 2:
                return parse_time(row.modified_at)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == 1:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return str(section + 1)


class ConfirmDeleteDialog(QDialog):
    def __init__(self, model_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Confirm deletion")
        self.model_name = model_name
        self.setModal(True)
        self.resize(520, 160)

        layout = QVBoxLayout(self)

        warn = QLabel(
            f"<b>Delete model:</b> <code>{model_name}</code><br><br>"
            "This removes the model from your local Ollama store.<br>"
            "To confirm, type the exact model name below:"
        )
        warn.setWordWrap(True)
        layout.addWidget(warn)

        form = QFormLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText(model_name)
        form.addRow("Type model name:", self.input)
        layout.addLayout(form)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.cancel = QPushButton("Cancel")
        self.ok = QPushButton("Delete")
        self.ok.setEnabled(False)
        btns.addWidget(self.cancel)
        btns.addWidget(self.ok)
        layout.addLayout(btns)

        self.input.textChanged.connect(self._on_change)
        self.cancel.clicked.connect(self.reject)
        self.ok.clicked.connect(self.accept)

    def _on_change(self, text: str):
        self.ok.setEnabled(text.strip() == self.model_name)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ollama Model Manager (PyQt6)")
        self.resize(1050, 620)

        self.client = OllamaClient(DEFAULT_BASE_URL)
        self.model = ModelsTableModel()

        self._build_ui()
        self._set_status("Ready.")
        self.refresh_models()

    def _build_ui(self):
        # Toolbar
        tb = QToolBar("Main")
        self.addToolBar(tb)

        self.base_url = QLineEdit(DEFAULT_BASE_URL)
        self.base_url.setMaximumWidth(320)
        self.base_url.setPlaceholderText("http://localhost:11434")
        tb.addWidget(QLabel(" Base URL: "))
        tb.addWidget(self.base_url)

        act_connect = QAction("Connect", self)
        act_connect.triggered.connect(self.on_connect)
        tb.addAction(act_connect)

        act_refresh = QAction("Refresh", self)
        act_refresh.triggered.connect(self.refresh_models)
        tb.addAction(act_refresh)

        tb.addSeparator()

        self.filter = QLineEdit("")
        self.filter.setPlaceholderText("Filter models… (e.g. llama, mistral)")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.model.set_filter)
        tb.addWidget(QLabel(" Filter: "))
        tb.addWidget(self.filter)

        # Main layout
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: table
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.clicked.connect(self.on_row_selected)
        left_layout.addWidget(self.table)

        # Buttons row
        btn_row = QHBoxLayout()
        self.btn_delete = QPushButton("Delete selected…")
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_delete.setEnabled(False)

        self.btn_delete_filtered = QPushButton("Delete all filtered…")
        self.btn_delete_filtered.clicked.connect(self.delete_all_filtered)
        self.btn_delete_filtered.setEnabled(True)

        btn_row.addWidget(self.btn_delete)
        btn_row.addWidget(self.btn_delete_filtered)
        btn_row.addStretch(1)
        left_layout.addLayout(btn_row)

        # Right: details
        right = QWidget()
        right_layout = QVBoxLayout(right)

        right_layout.addWidget(QLabel("<b>Details</b>"))
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Select a model to see details.")
        right_layout.addWidget(self.details)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([680, 370])

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.addWidget(splitter)
        self.setCentralWidget(root)

        # Status bar
        self.setStatusBar(QStatusBar())

    def _set_status(self, msg: str):
        self.statusBar().showMessage(msg, 8000)

    def _run_bg(self, fn, on_ok=None, on_err=None):
        def worker():
            try:
                out = fn()
                if on_ok:
                    QApplication.instance().postEvent(self, _CallEvent(lambda: on_ok(out)))
            except Exception as e:
                if on_err:
                    QApplication.instance().postEvent(self, _CallEvent(lambda: on_err(e)))
                else:
                    QApplication.instance().postEvent(self, _CallEvent(lambda: self._show_error(str(e))))

        threading.Thread(target=worker, daemon=True).start()

    def event(self, e):
        if isinstance(e, _CallEvent):
            e.callable()
            return True
        return super().event(e)

    def _show_error(self, text: str):
        QMessageBox.critical(self, "Error", text)

    def on_connect(self):
        url = self.base_url.text().strip()
        if not url:
            self._show_error("Base URL cannot be empty.")
            return
        self.client.set_base_url(url)
        self._set_status(f"Connected to {url}. Refreshing…")
        self.refresh_models()

    def refresh_models(self):
        self.btn_delete.setEnabled(False)
        self.details.clear()
        self.details.setPlaceholderText("Select a model to see details.")
        self._set_status("Fetching models…")

        def work():
            data = self.client.tags()
            rows = []
            for m in data.get("models", []):
                rows.append(
                    ModelRow(
                        name=m.get("name", ""),
                        size=m.get("size"),
                        modified_at=m.get("modified_at") or m.get("modified"),
                        raw=m,
                    )
                )
            return rows

        def ok(rows):
            self.model.set_rows(rows)
            self._set_status(f"Loaded {len(rows)} model(s).")

        def err(e):
            self._show_error(
                "Could not reach Ollama.\n\n"
                "Make sure Ollama is running and the URL is correct.\n\n"
                f"Details: {e}"
            )
            self._set_status("Fetch failed.")

        self._run_bg(work, ok, err)

    def selected_models(self) -> list[str]:
        sels = self.table.selectionModel().selectedRows()
        names = []
        for idx in sels:
            row = self.model.rows()[idx.row()]
            if row.name:
                names.append(row.name)
        return names

    def on_row_selected(self, _index: QModelIndex):
        names = self.selected_models()
        self.btn_delete.setEnabled(len(names) > 0)
        if len(names) == 1:
            self.load_details(names[0])
        else:
            self.details.setPlainText(f"{len(names)} models selected.")

    def load_details(self, model_name: str):
        self.details.setPlainText(f"Loading details for {model_name}…")
        self._set_status(f"Loading details for {model_name}…")

        def work():
            # Best-effort. If /api/show fails, return cached minimal info.
            try:
                return {"source": "api/show", "data": self.client.show(model_name)}
            except Exception as e:
                cached = next((r.raw for r in self.model.rows() if r.name == model_name), {})
                return {"source": "cache", "error": str(e), "cached": cached}

        def ok(payload):
            self.details.setPlainText(json.dumps(payload, indent=2, ensure_ascii=False))
            self._set_status(f"Details loaded for {model_name}.")

        self._run_bg(work, ok, lambda e: self._show_error(str(e)))

    def delete_selected(self):
        names = self.selected_models()
        if not names:
            return

        if len(names) == 1:
            target = names[0]
            dlg = ConfirmDeleteDialog(target, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self._delete_models([target])
            return

        preview = "\n".join(f"• {n}" for n in names[:12])
        if len(names) > 12:
            preview += f"\n… and {len(names) - 12} more"

        if QMessageBox.question(
            self,
            "Confirm deletion",
            f"Delete {len(names)} selected models?\n\n{preview}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        # For multi-delete, still add one more guard:
        typed, ok = _simple_text_prompt(
            self,
            title="Type DELETE to confirm",
            label=f"Type DELETE to remove {len(names)} models:",
            placeholder="DELETE",
        )
        if not ok or typed.strip() != "DELETE":
            QMessageBox.information(self, "Cancelled", "Confirmation did not match.")
            return

        self._delete_models(names)

    def delete_all_filtered(self):
        f = (self.filter.text() or "").strip()
        if not f:
            QMessageBox.warning(
                self,
                "Refused",
                "For safety, bulk delete requires a non-empty filter.\n\n"
                "Type a filter (e.g. 'llama') so you're not deleting everything accidentally.",
            )
            return

        targets = [r.name for r in self.model.rows()]
        if not targets:
            QMessageBox.information(self, "Nothing to delete", "No models match the current filter.")
            return

        preview = "\n".join(f"• {n}" for n in targets[:12])
        if len(targets) > 12:
            preview += f"\n… and {len(targets) - 12} more"

        if QMessageBox.question(
            self,
            "Confirm bulk deletion",
            f"This will delete {len(targets)} model(s) matching filter: '{f}'.\n\n{preview}\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        typed, ok = _simple_text_prompt(
            self,
            title="Type DELETE to confirm",
            label=f"Type DELETE to delete {len(targets)} models:",
            placeholder="DELETE",
        )
        if not ok or typed.strip() != "DELETE":
            QMessageBox.information(self, "Cancelled", "Confirmation did not match.")
            return

        self._delete_models(targets)

    def _delete_models(self, names: list[str]):
        self._set_status(f"Deleting {len(names)} model(s)…")

        def work():
            failed = []
            for i, name in enumerate(names, start=1):
                try:
                    self.client.delete(name)
                except Exception as e:
                    failed.append((name, str(e)))
            return failed

        def ok(failed):
            if failed:
                msg = "\n".join([f"{n}: {err}" for n, err in failed[:10]])
                if len(failed) > 10:
                    msg += f"\n…and {len(failed) - 10} more"
                QMessageBox.warning(self, "Some deletions failed", msg)
            self.refresh_models()

        def err(e):
            self._show_error(str(e))
            self._set_status("Delete failed.")

        self._run_bg(work, ok, err)


# ----- Simple event helper to safely call UI updates from background threads -----

from PyQt6.QtCore import QEvent


class _CallEvent(QEvent):
    TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, callable_):
        super().__init__(self.TYPE)
        self.callable = callable_


def _simple_text_prompt(parent, title: str, label: str, placeholder: str):
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.resize(420, 140)

    layout = QVBoxLayout(dlg)
    lab = QLabel(label)
    lab.setWordWrap(True)
    layout.addWidget(lab)

    entry = QLineEdit()
    entry.setPlaceholderText(placeholder)
    layout.addWidget(entry)

    btns = QHBoxLayout()
    btns.addStretch(1)
    btn_cancel = QPushButton("Cancel")
    btn_ok = QPushButton("OK")
    btns.addWidget(btn_cancel)
    btns.addWidget(btn_ok)
    layout.addLayout(btns)

    btn_cancel.clicked.connect(dlg.reject)
    btn_ok.clicked.connect(dlg.accept)

    code = dlg.exec()
    return entry.text(), (code == QDialog.DialogCode.Accepted)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()