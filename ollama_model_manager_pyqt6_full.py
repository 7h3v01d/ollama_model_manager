import json
import os
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests
from PyQt6.QtCore import (
    Qt,
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QThread,
    pyqtSignal,
    QTimer,
)
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableView,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QGroupBox,
)


# ---------------------------
# Utilities
# ---------------------------

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
        s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(s2)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except Exception:
        return s


def safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def default_models_dir_guess() -> Path:
    r"""
    Best-effort on Windows:
    - If OLLAMA_MODELS is set, that's the authoritative location.
    - Else assume %USERPROFILE%\.ollama\models (common default).
    """
    env = os.environ.get("OLLAMA_MODELS")
    if env:
        return Path(env).expanduser().resolve()

    user = Path(os.environ.get("USERPROFILE", str(Path.home())))
    return (user / ".ollama" / "models").resolve()


def normalize_model_name(name: str) -> tuple[str, str]:
    """
    Normalize model name into (repo, tag).
    Examples:
      "llama3" -> ("llama3", "latest")
      "llama3:8b" -> ("llama3", "8b")
      "library/llama3:latest" -> ("library/llama3", "latest")
      "registry.ollama.ai/library/llama3:latest" -> ("library/llama3", "latest")
    """
    s = (name or "").strip()
    if not s:
        return ("", "latest")

    # Drop registry prefix if present
    prefix = "registry.ollama.ai/"
    if s.startswith(prefix):
        s = s[len(prefix):]

    if ":" in s:
        repo, tag = s.rsplit(":", 1)
        repo = repo.strip()
        tag = tag.strip() or "latest"
    else:
        repo, tag = s, "latest"

    return (repo, tag)


def blob_filename_from_digest(digest: str) -> str:
    """
    Converts "sha256:abcdef..." to "sha256-abcdef..."
    """
    d = (digest or "").strip()
    if ":" in d:
        algo, h = d.split(":", 1)
        return f"{algo}-{h}"
    return d.replace(":", "-")


def iter_unique(items: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


# ---------------------------
# Ollama API client
# ---------------------------

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
        r = self.session.get(self._url("/api/tags"), timeout=8)
        r.raise_for_status()
        return r.json()

    def show(self, model: str) -> dict:
        r = self.session.post(self._url("/api/show"), data=json.dumps({"model": model}), timeout=12)
        r.raise_for_status()
        return r.json()

    def delete(self, model: str) -> None:
        r = self.session.delete(self._url("/api/delete"), data=json.dumps({"model": model}), timeout=20)
        r.raise_for_status()

    def ps(self) -> dict:
        r = self.session.get(self._url("/api/ps"), timeout=8)
        r.raise_for_status()
        return r.json()

    def pull_stream(self, model: str, insecure: bool, stream: bool = True):
        payload = {"model": model, "stream": stream}
        if insecure:
            payload["insecure"] = True

        with self.session.post(
            self._url("/api/pull"),
            data=json.dumps(payload),
            timeout=60,
            stream=True,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    yield {"raw": line}


# ---------------------------
# Table models
# ---------------------------

@dataclass
class InstalledModelRow:
    name: str
    size: int | None
    modified_at: str | None
    raw: dict


class InstalledModelsTableModel(QAbstractTableModel):
    HEADERS = ["Model", "Size", "Modified"]

    def __init__(self):
        super().__init__()
        self._rows: list[InstalledModelRow] = []
        self._filtered: list[InstalledModelRow] = []
        self._filter = ""

    def set_rows(self, rows: list[InstalledModelRow]):
        self.beginResetModel()
        self._rows = rows
        self._apply_filter_locked()
        self.endResetModel()

    def set_filter(self, text: str):
        self.beginResetModel()
        self._filter = (text or "").strip().lower()
        self._apply_filter_locked()
        self.endResetModel()

    def rows(self) -> list[InstalledModelRow]:
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


@dataclass
class RunningModelRow:
    name: str
    size: str
    processor: str
    until: str
    context: str
    raw: dict


class RunningModelsTableModel(QAbstractTableModel):
    HEADERS = ["Name", "Size", "Processor", "Context", "Until"]

    def __init__(self):
        super().__init__()
        self._rows: list[RunningModelRow] = []

    def set_rows(self, rows: list[RunningModelRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> list[RunningModelRow]:
        return self._rows

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = self._rows[index.row()]
        c = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            return [r.name, r.size, r.processor, r.context, r.until][c]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if c in (1, 3):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return str(section + 1)


# ---------------------------
# Confirm dialogs
# ---------------------------

class ConfirmDeleteDialog(QDialog):
    def __init__(self, model_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Confirm deletion")
        self.model_name = model_name
        self.setModal(True)
        self.resize(540, 170)

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


def prompt_text(parent, title: str, label: str, placeholder: str):
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.resize(440, 150)

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


# ---------------------------
# Manifest + blob selection logic (SMART EXPORT)
# ---------------------------

def find_manifest_file(models_dir: Path, model_name: str) -> Path | None:
    """
    Attempts to locate the manifest file for a given model inside:
      models_dir / manifests / ...

    Common shape is:
      manifests/registry.ollama.ai/<namespace>/<repo>/<tag>

    But to be robust across naming variations, we search.
    """
    repo, tag = normalize_model_name(model_name)
    if not repo:
        return None

    manifests = models_dir / "manifests"
    if not manifests.exists():
        return None

    # Prefer exact expected location if it exists:
    exact = manifests / "registry.ollama.ai" / repo / tag
    if exact.exists() and exact.is_file():
        return exact

    # Otherwise search for tag files whose parent path ends with repo
    # Example: .../library/llama3/latest
    repo_parts = Path(repo).parts  # ("library", "llama3") or ("llama3",)
    candidates = []
    for p in manifests.rglob(tag):
        if not p.is_file():
            continue
        parent_parts = p.parent.parts
        # check if the end of parent matches repo_parts
        if len(parent_parts) >= len(repo_parts) and tuple(parent_parts[-len(repo_parts):]) == repo_parts:
            candidates.append(p)

    if candidates:
        # If multiple, take the shortest path (usually the canonical one)
        candidates.sort(key=lambda x: len(str(x)))
        return candidates[0]

    # Last resort: match by filename only (tag), no repo check
    for p in manifests.rglob(tag):
        if p.is_file():
            return p

    return None


def parse_manifest_for_digests(manifest_path: Path) -> tuple[list[str], dict]:
    """
    Reads a manifest JSON and extracts digests from:
      - config.digest
      - layers[].digest
    Returns (digests, raw_manifest_json).
    """
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    digests = []

    cfg = raw.get("config") or {}
    if isinstance(cfg, dict):
        d = cfg.get("digest")
        if isinstance(d, str) and d:
            digests.append(d)

    layers = raw.get("layers") or []
    if isinstance(layers, list):
        for layer in layers:
            if isinstance(layer, dict):
                d = layer.get("digest")
                if isinstance(d, str) and d:
                    digests.append(d)

    return (iter_unique(digests), raw)


def compute_export_files_for_models(models_dir: Path, model_names: list[str]) -> tuple[list[tuple[Path, Path]], dict]:
    """
    Returns a list of (source_path, zip_arc_path) pairs and a metadata dict.
    Arc paths are rooted under "models/" so import can extract directly.
    """
    models_dir = models_dir.resolve()
    manifests_dir = models_dir / "manifests"
    blobs_dir = models_dir / "blobs"

    export_pairs: list[tuple[Path, Path]] = []
    meta = {
        "selected_models": model_names,
        "found_manifests": {},
        "missing_manifests": [],
        "missing_blobs": [],
        "digest_count": 0,
        "file_count": 0,
        "generated_at": datetime.now().isoformat(),
    }

    # Always include a small metadata file
    # (written later, not sourced from disk)
    # We'll just store it in meta and write as a zip entry.

    all_digests: list[str] = []
    manifest_paths: list[Path] = []

    for m in model_names:
        mf = find_manifest_file(models_dir, m)
        if not mf:
            meta["missing_manifests"].append(m)
            continue

        # Include the manifest file itself
        rel_mf = mf.relative_to(models_dir)
        export_pairs.append((mf, Path("models") / rel_mf))
        manifest_paths.append(mf)
        meta["found_manifests"][m] = str(rel_mf)

        digests, _raw = parse_manifest_for_digests(mf)
        all_digests.extend(digests)

    all_digests = iter_unique(all_digests)
    meta["digest_count"] = len(all_digests)

    # Add blobs referenced by digests
    for d in all_digests:
        blob_name = blob_filename_from_digest(d)
        src_blob = blobs_dir / blob_name
        if src_blob.exists() and src_blob.is_file():
            rel_blob = src_blob.relative_to(models_dir)
            export_pairs.append((src_blob, Path("models") / rel_blob))
        else:
            meta["missing_blobs"].append({"digest": d, "expected": str(Path("blobs") / blob_name)})

    # Deduplicate file pairs by arcname (important)
    seen_arc = set()
    deduped = []
    for src, arc in export_pairs:
        a = str(arc)
        if a in seen_arc:
            continue
        seen_arc.add(a)
        deduped.append((src, arc))

    meta["file_count"] = len(deduped)
    return deduped, meta


# ---------------------------
# Background workers (QThread)
# ---------------------------

class Worker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class PullWorker(Worker):
    progress = pyqtSignal(dict)

    def __init__(self, client: OllamaClient, model: str, insecure: bool):
        super().__init__()
        self.client = client
        self.model = model
        self.insecure = insecure
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            last = None
            for obj in self.client.pull_stream(self.model, insecure=self.insecure, stream=True):
                if self._stop:
                    break
                last = obj
                self.progress.emit(obj)
            self.finished.emit(last)
        except Exception as e:
            self.failed.emit(str(e))


class ExportImportWorker(Worker):
    progress = pyqtSignal(int, int, str)

    def __init__(self, mode: str, models_dir: Path, zip_path: Path, model_names: list[str] | None = None):
        """
        mode:
          - "export_full"
          - "export_selected"
          - "import_zip"
        """
        super().__init__()
        self.mode = mode
        self.models_dir = models_dir
        self.zip_path = zip_path
        self.model_names = model_names or []

    def run(self):
        try:
            if self.mode == "export_full":
                self._export_full()
            elif self.mode == "export_selected":
                self._export_selected()
            elif self.mode == "import_zip":
                self._import_zip()
            else:
                raise ValueError(f"Unknown mode: {self.mode}")
        except Exception as e:
            self.failed.emit(str(e))

    def _export_full(self):
        models_dir = self.models_dir
        if not models_dir.exists():
            raise FileNotFoundError(f"Models directory not found: {models_dir}")

        files = [p for p in models_dir.rglob("*") if p.is_file()]
        total = len(files)
        done = 0

        safe_mkdir(self.zip_path.parent)
        with zipfile.ZipFile(self.zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in files:
                rel = p.relative_to(models_dir)
                zf.write(p, arcname=str(Path("models") / rel))
                done += 1
                if done % 25 == 0 or done == total:
                    self.progress.emit(done, total, f"Zipping {rel}")

            meta = {"mode": "export_full", "generated_at": datetime.now().isoformat(), "file_count": total}
            zf.writestr("models/_backup_meta.json", json.dumps(meta, indent=2))

        self.finished.emit({"zip": str(self.zip_path), "files": total, "mode": "export_full"})

    def _export_selected(self):
        models_dir = self.models_dir
        if not models_dir.exists():
            raise FileNotFoundError(f"Models directory not found: {models_dir}")
        if not self.model_names:
            raise ValueError("No selected models provided for export_selected.")

        pairs, meta = compute_export_files_for_models(models_dir, self.model_names)
        total = len(pairs)
        done = 0

        safe_mkdir(self.zip_path.parent)
        with zipfile.ZipFile(self.zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Write files
            for src, arc in pairs:
                zf.write(src, arcname=str(arc))
                done += 1
                if done % 10 == 0 or done == total:
                    self.progress.emit(done, total, f"Zipping {arc}")

            # Write metadata
            meta["mode"] = "export_selected"
            zf.writestr("models/_backup_meta.json", json.dumps(meta, indent=2))

        self.finished.emit({"zip": str(self.zip_path), "files": total, "mode": "export_selected", "meta": meta})

    def _import_zip(self):
        zip_file = self.zip_path
        models_dir = self.models_dir

        if not zip_file.exists():
            raise FileNotFoundError(f"Zip not found: {zip_file}")

        safe_mkdir(models_dir)

        tmp = Path.cwd() / f".ollama_import_tmp_{int(time.time())}"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        safe_mkdir(tmp)

        try:
            with zipfile.ZipFile(zip_file, "r") as zf:
                zf.extractall(tmp)

            extracted_models = tmp / "models"
            if not extracted_models.exists():
                extracted_models = tmp

            candidates = []
            for sub in ["manifests", "blobs"]:
                if (extracted_models / sub).exists():
                    candidates.append(extracted_models / sub)

            if not candidates:
                raise ValueError("Import zip does not contain 'models/manifests' or 'models/blobs'.")

            to_copy = []
            for c in candidates:
                for p in c.rglob("*"):
                    if p.is_file():
                        rel = p.relative_to(extracted_models)
                        to_copy.append((p, models_dir / rel))

            total = len(to_copy)
            done = 0

            for src_p, dst_p in to_copy:
                safe_mkdir(dst_p.parent)
                if dst_p.exists() and dst_p.stat().st_size == src_p.stat().st_size:
                    done += 1
                    if done % 50 == 0 or done == total:
                        self.progress.emit(done, total, f"Skipping existing {dst_p.name}")
                    continue

                shutil.copy2(src_p, dst_p)
                done += 1
                if done % 25 == 0 or done == total:
                    self.progress.emit(done, total, f"Copying {dst_p.name}")

            self.finished.emit({"imported_files": total, "models_dir": str(models_dir)})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def start_worker(worker: Worker) -> QThread:
    """
    Starts a QObject worker in a new QThread and returns the thread.
    IMPORTANT: caller must hold a strong reference to the returned QThread until it finishes.
    """
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)  # type: ignore
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.failed.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread


# ---------------------------
# Main window
# ---------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ollama Model Manager (PyQt6) — Installed / Running / Pull / Smart Backup")
        self.resize(1160, 740)

        self.client = OllamaClient(DEFAULT_BASE_URL)
        self.installed_model = InstalledModelsTableModel()
        self.running_model = RunningModelsTableModel()

        # Thread management: keep strong refs until done
        self._active_threads: set[QThread] = set()

        self._pull_worker: PullWorker | None = None
        self._pull_thread: QThread | None = None

        self._build_ui()

        # Running auto-refresh via QTimer (NO python threads)
        self.running_timer = QTimer(self)
        self.running_timer.setInterval(5000)
        self.running_timer.timeout.connect(self.refresh_running)

        self._set_status("Ready.")
        self.refresh_installed()
        self.refresh_running()

    # ----- UI -----

    def _build_ui(self):
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

        tb.addSeparator()

        act_refresh_installed = QAction("Refresh Installed", self)
        act_refresh_installed.triggered.connect(self.refresh_installed)
        tb.addAction(act_refresh_installed)

        act_refresh_running = QAction("Refresh Running", self)
        act_refresh_running.triggered.connect(self.refresh_running)
        tb.addAction(act_refresh_running)

        tb.addSeparator()

        self.filter = QLineEdit("")
        self.filter.setPlaceholderText("Filter installed models… (e.g. llama, mistral)")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.installed_model.set_filter)
        tb.addWidget(QLabel(" Filter: "))
        tb.addWidget(self.filter)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._tab_installed(), "Installed Models")
        self.tabs.addTab(self._tab_running(), "Running Models (/api/ps)")
        self.tabs.addTab(self._tab_transfer(), "Pull / Export / Import")

        self.setStatusBar(QStatusBar())

    def _tab_installed(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)

        self.table_installed = QTableView()
        self.table_installed.setModel(self.installed_model)
        self.table_installed.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_installed.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table_installed.setAlternatingRowColors(True)
        self.table_installed.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_installed.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_installed.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_installed.clicked.connect(self.on_installed_selected)
        left_layout.addWidget(self.table_installed)

        btn_row = QHBoxLayout()
        self.btn_delete_installed = QPushButton("Delete selected…")
        self.btn_delete_installed.clicked.connect(self.delete_installed_selected)
        self.btn_delete_installed.setEnabled(False)

        self.btn_delete_filtered = QPushButton("Delete all filtered…")
        self.btn_delete_filtered.clicked.connect(self.delete_all_filtered_installed)

        btn_row.addWidget(self.btn_delete_installed)
        btn_row.addWidget(self.btn_delete_filtered)
        btn_row.addStretch(1)
        left_layout.addLayout(btn_row)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("<b>Details</b>"))
        self.details_installed = QTextEdit()
        self.details_installed.setReadOnly(True)
        self.details_installed.setPlaceholderText("Select a model to see details.")
        right_layout.addWidget(self.details_installed)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([740, 420])

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(splitter)
        return container

    def _tab_running(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        top = QHBoxLayout()
        self.chk_auto_refresh = QCheckBox("Auto-refresh every 5s")
        self.chk_auto_refresh.stateChanged.connect(self.on_auto_refresh_running)
        btn = QPushButton("Refresh now")
        btn.clicked.connect(self.refresh_running)
        top.addWidget(self.chk_auto_refresh)
        top.addStretch(1)
        top.addWidget(btn)
        layout.addLayout(top)

        self.table_running = QTableView()
        self.table_running.setModel(self.running_model)
        self.table_running.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_running.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table_running.setAlternatingRowColors(True)
        self.table_running.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_running.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_running.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_running.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_running.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table_running)

        self.details_running = QTextEdit()
        self.details_running.setReadOnly(True)
        self.details_running.setPlaceholderText("Raw /api/ps response will appear here after refresh.")
        layout.addWidget(self.details_running)

        return container

    def _tab_transfer(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        # Pull section
        gb_pull = QGroupBox("Pull model (/api/pull) with progress")
        pull_layout = QVBoxLayout(gb_pull)

        row = QHBoxLayout()
        self.pull_model_name = QLineEdit()
        self.pull_model_name.setPlaceholderText("e.g. llama3.2, mistral, gemma3:latest")
        self.pull_insecure = QCheckBox("insecure")
        self.pull_btn = QPushButton("Pull")
        self.pull_btn.clicked.connect(self.on_pull)
        self.pull_cancel_btn = QPushButton("Cancel")
        self.pull_cancel_btn.clicked.connect(self.on_pull_cancel)
        self.pull_cancel_btn.setEnabled(False)

        row.addWidget(QLabel("Model:"))
        row.addWidget(self.pull_model_name, 1)
        row.addWidget(self.pull_insecure)
        row.addWidget(self.pull_btn)
        row.addWidget(self.pull_cancel_btn)
        pull_layout.addLayout(row)

        self.pull_progress = QProgressBar()
        self.pull_progress.setRange(0, 100)
        self.pull_progress.setValue(0)
        pull_layout.addWidget(self.pull_progress)

        self.pull_log = QTextEdit()
        self.pull_log.setReadOnly(True)
        self.pull_log.setPlaceholderText("Streaming pull output will appear here…")
        pull_layout.addWidget(self.pull_log)

        layout.addWidget(gb_pull)

        # Backup section
        gb_backup = QGroupBox("Export / Import backup (SMART: selected models only, or FULL store)")
        backup_layout = QVBoxLayout(gb_backup)

        form = QFormLayout()
        self.models_dir = QLineEdit(str(default_models_dir_guess()))
        self.models_dir.setPlaceholderText("Path to Ollama models directory (contains manifests/ and blobs/)")
        btn_browse_models_dir = QPushButton("Browse…")
        btn_browse_models_dir.clicked.connect(self.browse_models_dir)

        h = QHBoxLayout()
        h.addWidget(self.models_dir, 1)
        h.addWidget(btn_browse_models_dir)
        form.addRow("Models dir:", h)
        backup_layout.addLayout(form)

        row2 = QHBoxLayout()
        self.export_selected_btn = QPushButton("Export SELECTED models ZIP…")
        self.export_selected_btn.clicked.connect(self.export_selected_zip)

        self.export_full_btn = QPushButton("Export FULL store ZIP…")
        self.export_full_btn.clicked.connect(self.export_full_zip)

        self.import_btn = QPushButton("Import backup ZIP…")
        self.import_btn.clicked.connect(self.import_zip)

        row2.addWidget(self.export_selected_btn)
        row2.addWidget(self.export_full_btn)
        row2.addWidget(self.import_btn)
        row2.addStretch(1)
        backup_layout.addLayout(row2)

        self.file_progress = QProgressBar()
        self.file_progress.setRange(0, 100)
        self.file_progress.setValue(0)
        backup_layout.addWidget(self.file_progress)

        self.file_log = QTextEdit()
        self.file_log.setReadOnly(True)
        self.file_log.setPlaceholderText("Export/import progress will appear here…")
        backup_layout.addWidget(self.file_log)

        layout.addWidget(gb_backup)
        layout.addStretch(1)
        return container

    # ----- Common helpers -----

    def _set_status(self, msg: str):
        self.statusBar().showMessage(msg, 8000)

    def _err(self, title: str, text: str):
        QMessageBox.critical(self, title, text)

    def _warn(self, title: str, text: str):
        QMessageBox.warning(self, title, text)

    def _info(self, title: str, text: str):
        QMessageBox.information(self, title, text)

    def _register_thread(self, thread: QThread):
        self._active_threads.add(thread)
        thread.finished.connect(lambda: self._active_threads.discard(thread))

    def _start_worker(self, worker: Worker) -> QThread:
        t = start_worker(worker)
        self._register_thread(t)
        return t

    # ----- Connection -----

    def on_connect(self):
        url = self.base_url.text().strip()
        if not url:
            self._err("Error", "Base URL cannot be empty.")
            return
        self.client.set_base_url(url)
        self._set_status(f"Connected to {url}. Refreshing…")
        self.refresh_installed()
        self.refresh_running()

    # ----- Installed models -----

    def refresh_installed(self):
        self.btn_delete_installed.setEnabled(False)
        self.details_installed.clear()
        self.details_installed.setPlaceholderText("Select a model to see details.")
        self._set_status("Fetching installed models…")

        def work():
            data = self.client.tags()
            rows = []
            for m in data.get("models", []):
                rows.append(
                    InstalledModelRow(
                        name=m.get("name", ""),
                        size=m.get("size"),
                        modified_at=m.get("modified_at") or m.get("modified"),
                        raw=m,
                    )
                )
            return rows

        worker = Worker()

        def run():
            try:
                worker.finished.emit(work())
            except Exception as e:
                worker.failed.emit(str(e))

        worker.run = run  # type: ignore

        worker.finished.connect(lambda rows: (self.installed_model.set_rows(rows),
                                            self._set_status(f"Loaded {len(rows)} installed model(s).")))
        worker.failed.connect(lambda msg: (self._err("Connection error",
                                                    "Could not reach Ollama.\n\n"
                                                    "Make sure Ollama is running and the URL is correct.\n\n"
                                                    f"Details: {msg}"),
                                           self._set_status("Fetch installed models failed.")))
        self._start_worker(worker)

    def installed_selected_names(self) -> list[str]:
        sels = self.table_installed.selectionModel().selectedRows()
        names = []
        for idx in sels:
            r = self.installed_model.rows()[idx.row()]
            if r.name:
                names.append(r.name)
        return names

    def on_installed_selected(self, _index: QModelIndex):
        names = self.installed_selected_names()
        self.btn_delete_installed.setEnabled(len(names) > 0)

        if len(names) == 1:
            self.load_installed_details(names[0])
        else:
            self.details_installed.setPlainText(f"{len(names)} models selected.")

    def load_installed_details(self, model_name: str):
        self.details_installed.setPlainText(f"Loading details for {model_name}…")
        self._set_status(f"Loading details for {model_name}…")

        def work():
            try:
                return {"source": "api/show", "data": self.client.show(model_name)}
            except Exception as e:
                cached = next((r.raw for r in self.installed_model.rows() if r.name == model_name), {})
                return {"source": "cache", "error": str(e), "cached": cached}

        worker = Worker()

        def run():
            try:
                worker.finished.emit(work())
            except Exception as e:
                worker.failed.emit(str(e))

        worker.run = run  # type: ignore
        worker.finished.connect(lambda payload: (self.details_installed.setPlainText(json.dumps(payload, indent=2, ensure_ascii=False)),
                                                self._set_status(f"Details loaded for {model_name}.")))
        worker.failed.connect(lambda msg: self._err("Error", msg))
        self._start_worker(worker)

    def delete_installed_selected(self):
        names = self.installed_selected_names()
        if not names:
            return

        if len(names) == 1:
            target = names[0]
            dlg = ConfirmDeleteDialog(target, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self._delete_models(names)
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

        typed, ok = prompt_text(
            self,
            "Type DELETE to confirm",
            f"Type DELETE to remove {len(names)} models:",
            "DELETE",
        )
        if not ok or typed.strip() != "DELETE":
            self._info("Cancelled", "Confirmation did not match.")
            return

        self._delete_models(names)

    def delete_all_filtered_installed(self):
        f = (self.filter.text() or "").strip()
        if not f:
            self._warn(
                "Refused",
                "For safety, bulk delete requires a non-empty filter.\n\n"
                "Type a filter (e.g. 'llama') so you're not deleting everything accidentally.",
            )
            return

        targets = [r.name for r in self.installed_model.rows()]
        if not targets:
            self._info("Nothing to delete", "No models match the current filter.")
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

        typed, ok = prompt_text(
            self,
            "Type DELETE to confirm",
            f"Type DELETE to delete {len(targets)} models:",
            "DELETE",
        )
        if not ok or typed.strip() != "DELETE":
            self._info("Cancelled", "Confirmation did not match.")
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

        worker.run = run  # type: ignore

        def on_ok(failed):
            if failed:
                msg = "\n".join([f"{n}: {err}" for n, err in failed[:10]])
                if len(failed) > 10:
                    msg += f"\n…and {len(failed) - 10} more"
                self._warn("Some deletions failed", msg)
            self.refresh_installed()
            self.refresh_running()

        worker.finished.connect(on_ok)
        worker.failed.connect(lambda msg: self._err("Delete failed", msg))
        self._start_worker(worker)

    # ----- Running models (/api/ps) -----

    def refresh_running(self):
        self._set_status("Fetching running models…")

        worker = Worker()

        def run():
            try:
                data = self.client.ps()
                worker.finished.emit(data)
            except Exception as e:
                worker.failed.emit(str(e))

        worker.run = run  # type: ignore

        def on_ok(data: dict):
            rows = []
            for m in data.get("models", []):
                rows.append(
                    RunningModelRow(
                        name=str(m.get("name", "")),
                        size=str(m.get("size", "")),
                        processor=str(m.get("processor", "")),
                        context=str(m.get("context", "")),
                        until=str(m.get("until", "")),
                        raw=m,
                    )
                )
            self.running_model.set_rows(rows)
            self.details_running.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
            self._set_status(f"Loaded {len(rows)} running model(s).")

        worker.finished.connect(on_ok)
        worker.failed.connect(lambda msg: self._err("Error", msg))
        self._start_worker(worker)

    def on_auto_refresh_running(self, state: int):
        enabled = (state == Qt.CheckState.Checked.value)
        if enabled:
            self._set_status("Auto-refresh running models enabled (5s).")
            self.running_timer.start()
        else:
            self._set_status("Auto-refresh running models disabled.")
            self.running_timer.stop()

    # ----- Pull UI (/api/pull streaming) -----

    def on_pull(self):
        model = self.pull_model_name.text().strip()
        if not model:
            self._warn("Missing model", "Enter a model name to pull (e.g. llama3.2 or gemma3:latest).")
            return

        # prevent parallel pulls
        if self._pull_thread is not None and self._pull_thread.isRunning():
            self._warn("Pull in progress", "A pull is already running. Cancel it first.")
            return

        self.pull_log.clear()
        self.pull_progress.setValue(0)

        self.pull_btn.setEnabled(False)
        self.pull_cancel_btn.setEnabled(True)

        self._set_status(f"Pulling {model}…")

        worker = PullWorker(self.client, model=model, insecure=self.pull_insecure.isChecked())
        worker.progress.connect(self._on_pull_progress)
        worker.finished.connect(self._on_pull_finished)
        worker.failed.connect(self._on_pull_failed)

        self._pull_worker = worker
        self._pull_thread = self._start_worker(worker)

    def on_pull_cancel(self):
        if self._pull_worker:
            self._pull_worker.stop()
            self.pull_log.append("Cancellation requested. (Note: server may continue the download in background.)")
            self._set_status("Cancelling pull…")

    def _on_pull_progress(self, obj: dict):
        self.pull_log.append(json.dumps(obj, ensure_ascii=False))

        completed = obj.get("completed")
        total = obj.get("total")
        status = obj.get("status", "")

        if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and total > 0:
            pct = int((completed / total) * 100)
            self.pull_progress.setValue(max(0, min(100, pct)))
            self.pull_progress.setFormat(f"{pct}%  ({human_bytes(int(completed))} / {human_bytes(int(total))})")
        else:
            if status:
                self.pull_progress.setFormat(str(status))

        if status == "success":
            self.pull_progress.setValue(100)

    def _on_pull_finished(self, last_obj):
        self.pull_btn.setEnabled(True)
        self.pull_cancel_btn.setEnabled(False)

        self.pull_log.append("\n--- DONE ---")
        if last_obj is not None:
            self.pull_log.append(json.dumps(last_obj, indent=2, ensure_ascii=False))

        self._set_status("Pull finished.")
        self.refresh_installed()

        self._pull_worker = None
        self._pull_thread = None

    def _on_pull_failed(self, msg: str):
        self.pull_btn.setEnabled(True)
        self.pull_cancel_btn.setEnabled(False)
        self._err("Pull failed", msg)
        self._set_status("Pull failed.")
        self._pull_worker = None
        self._pull_thread = None

    # ----- Export / Import backups -----

    def browse_models_dir(self):
        d = QFileDialog.getExistingDirectory(
            self,
            "Select Ollama models directory",
            self.models_dir.text().strip() or str(Path.home()),
        )
        if d:
            self.models_dir.setText(d)

    def _get_models_dir(self) -> Path:
        return Path(self.models_dir.text().strip()).expanduser()

    def _default_zip_name(self, prefix: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str((Path.home() / f"{prefix}_{ts}.zip").resolve())

    def export_selected_zip(self):
        names = self.installed_selected_names()
        if not names:
            self._warn("No selection", "Select one or more models in the Installed Models tab first.")
            return

        models_dir = self._get_models_dir()
        if not models_dir.exists():
            self._err("Models dir not found", f"Directory does not exist:\n\n{models_dir}")
            return

        suggested = self._default_zip_name("ollama_selected_backup")
        out, _ = QFileDialog.getSaveFileName(self, "Save SELECTED backup ZIP", suggested, "Zip files (*.zip)")
        if not out:
            return

        # Quick preview warning: if we can't find a manifest, export may be incomplete.
        pairs, meta = compute_export_files_for_models(models_dir, names)
        if meta["missing_manifests"]:
            self._warn(
                "Some manifests not found",
                "Some selected models did not resolve to manifest files.\n"
                "Those models will NOT be exported.\n\n"
                + "\n".join(meta["missing_manifests"][:20])
            )

        if not pairs:
            self._err("Export failed", "No files were resolved for export (no manifests found).")
            return

        self.file_log.clear()
        self.file_progress.setValue(0)
        self._set_status("Exporting SELECTED backup zip…")

        worker = ExportImportWorker(
            mode="export_selected",
            models_dir=models_dir,
            zip_path=Path(out),
            model_names=names,
        )
        worker.progress.connect(self._on_file_progress)
        worker.finished.connect(self._on_export_done)
        worker.failed.connect(lambda msg: self._err("Export failed", msg))
        self._start_worker(worker)

    def export_full_zip(self):
        models_dir = self._get_models_dir()
        if not models_dir.exists():
            self._err("Models dir not found", f"Directory does not exist:\n\n{models_dir}")
            return

        suggested = self._default_zip_name("ollama_full_store_backup")
        out, _ = QFileDialog.getSaveFileName(self, "Save FULL store ZIP", suggested, "Zip files (*.zip)")
        if not out:
            return

        self.file_log.clear()
        self.file_progress.setValue(0)
        self._set_status("Exporting FULL store zip…")

        worker = ExportImportWorker(
            mode="export_full",
            models_dir=models_dir,
            zip_path=Path(out),
        )
        worker.progress.connect(self._on_file_progress)
        worker.finished.connect(self._on_export_done)
        worker.failed.connect(lambda msg: self._err("Export failed", msg))
        self._start_worker(worker)

    def import_zip(self):
        models_dir = self._get_models_dir()
        safe_mkdir(models_dir)

        z, _ = QFileDialog.getOpenFileName(self, "Select backup ZIP to import", str(Path.home()), "Zip files (*.zip)")
        if not z:
            return

        typed, ok = prompt_text(
            self,
            "Confirm import",
            "Import will merge manifests/blobs into your models directory.\n\nType IMPORT to proceed:",
            "IMPORT",
        )
        if not ok or typed.strip() != "IMPORT":
            self._info("Cancelled", "Import cancelled.")
            return

        self.file_log.clear()
        self.file_progress.setValue(0)
        self._set_status("Importing backup zip…")

        worker = ExportImportWorker(
            mode="import_zip",
            models_dir=models_dir,
            zip_path=Path(z),
        )
        worker.progress.connect(self._on_file_progress)
        worker.finished.connect(self._on_import_done)
        worker.failed.connect(lambda msg: self._err("Import failed", msg))
        self._start_worker(worker)

    def _on_file_progress(self, done: int, total: int, msg: str):
        if total > 0:
            pct = int((done / total) * 100)
            self.file_progress.setValue(max(0, min(100, pct)))
            self.file_progress.setFormat(f"{pct}% ({done}/{total})")
        self.file_log.append(msg)

    def _on_export_done(self, payload):
        self.file_progress.setValue(100)
        self.file_log.append(json.dumps(payload, indent=2, ensure_ascii=False))
        self._set_status("Export complete.")
        self._info("Export complete", f"Backup created:\n\n{payload.get('zip')}")

    def _on_import_done(self, payload):
        self.file_progress.setValue(100)
        self.file_log.append(json.dumps(payload, indent=2, ensure_ascii=False))
        self._set_status("Import complete.")
        self._info("Import complete", f"Imported into:\n\n{payload.get('models_dir')}\n\nRefreshing model list…")
        self.refresh_installed()

    # ----- Shutdown safety (fixes QThread destroyed while running) -----

    def closeEvent(self, event):
        # Stop running timer
        self.running_timer.stop()

        # Request pull cancellation if active
        if self._pull_worker is not None:
            self._pull_worker.stop()

        # Wait briefly for threads to finish
        # (prevents "QThread: Destroyed while thread is still running")
        deadline = time.time() + 3.0
        threads = list(self._active_threads)
        for t in threads:
            if t.isRunning():
                remaining = max(0.0, deadline - time.time())
                if remaining <= 0:
                    break
                t.wait(int(remaining * 1000))

        event.accept()


def main():
    app = QApplication(sys.argv)
    try:
        app.setStyle("Fusion")
    except Exception:
        pass

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()