import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
import collections
import platform
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QObject, QThread,
    pyqtSignal, QTimer, QSize,
)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QPalette, QPixmap, QPainter,
    QBrush, QPen, QLinearGradient, QSyntaxHighlighter, QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QProgressBar, QScrollArea, QSizePolicy, QSplitter, QSpacerItem,
    QStackedWidget, QStatusBar, QTabWidget, QTableView, QTextEdit,
    QToolBar, QVBoxLayout, QWidget, QAbstractItemView,
)

from utils import human_bytes, parse_time, iter_unique, blob_filename_from_digest, safe_mkdir, get_system_stats, normalize_model_name
from client import OllamaClient

#  TABLE MODELS
# ─────────────────────────────────────────────

@dataclass
class InstalledModelRow:
    name: str
    size: int | None
    modified_at: str | None
    raw: dict


class InstalledModelsTableModel(QAbstractTableModel):
    HEADERS = ["  Model", "Size", "Modified"]

    def __init__(self):
        super().__init__()
        self._rows: list[InstalledModelRow] = []
        self._filtered: list[InstalledModelRow] = []
        self._filter = ""

    def set_rows(self, rows: list[InstalledModelRow]):
        self.beginResetModel()
        self._rows = sorted(rows, key=lambda r: r.name.lower())
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
                return f"  {row.name}"
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
    HEADERS = ["  Model", "Size", "Processor", "Context", "Expires"]

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
            vals = [f"  {r.name}", r.size, r.processor, r.context, r.until]
            return vals[c]
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


# ─────────────────────────────────────────────
#  MANIFEST / BLOB HELPERS
# ─────────────────────────────────────────────

def find_manifest_file(models_dir: Path, model_name: str) -> Path | None:
    repo, tag = normalize_model_name(model_name)
    if not repo:
        return None
    manifests = models_dir / "manifests"
    if not manifests.exists():
        return None
    exact = manifests / "registry.ollama.ai" / repo / tag
    if exact.exists() and exact.is_file():
        return exact
    repo_parts = Path(repo).parts
    candidates = []
    for p in manifests.rglob(tag):
        if not p.is_file():
            continue
        parent_parts = p.parent.parts
        if len(parent_parts) >= len(repo_parts) and tuple(parent_parts[-len(repo_parts):]) == repo_parts:
            candidates.append(p)
    if candidates:
        candidates.sort(key=lambda x: len(str(x)))
        return candidates[0]
    for p in manifests.rglob(tag):
        if p.is_file():
            return p
    return None


def parse_manifest_for_digests(manifest_path: Path) -> tuple[list[str], dict]:
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
    meta: dict = {
        "missing_manifests": [],
        "missing_blobs": [],
        "found_manifests": {},
        "digest_count": 0,
        "file_count": 0,
    }
    blobs_dir = models_dir / "blobs"
    export_pairs: list[tuple[Path, Path]] = []
    all_digests: list[str] = []

    for m in model_names:
        mf = find_manifest_file(models_dir, m)
        if mf is None:
            meta["missing_manifests"].append(m)
            continue
        rel_mf = mf.relative_to(models_dir)
        export_pairs.append((mf, Path("models") / rel_mf))
        meta["found_manifests"][m] = str(rel_mf)
        digests, _raw = parse_manifest_for_digests(mf)
        all_digests.extend(digests)

    all_digests = iter_unique(all_digests)
    meta["digest_count"] = len(all_digests)

    for d in all_digests:
        blob_name = blob_filename_from_digest(d)
        src_blob = blobs_dir / blob_name
        if src_blob.exists() and src_blob.is_file():
            rel_blob = src_blob.relative_to(models_dir)
            export_pairs.append((src_blob, Path("models") / rel_blob))
        else:
            meta["missing_blobs"].append({"digest": d, "expected": str(Path("blobs") / blob_name)})

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


# ─────────────────────────────────────────────
#  BACKGROUND WORKERS
# ─────────────────────────────────────────────

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
                    self.progress.emit(done, total, f"Archiving {rel}")
            meta = {"mode": "export_full", "generated_at": datetime.now().isoformat(), "file_count": total}
            zf.writestr("models/_backup_meta.json", json.dumps(meta, indent=2))
        self.finished.emit({"zip": str(self.zip_path), "files": total, "mode": "export_full"})

    def _export_selected(self):
        models_dir = self.models_dir
        if not models_dir.exists():
            raise FileNotFoundError(f"Models directory not found: {models_dir}")
        if not self.model_names:
            raise ValueError("No selected models provided.")
        pairs, meta = compute_export_files_for_models(models_dir, self.model_names)
        total = len(pairs)
        done = 0
        safe_mkdir(self.zip_path.parent)
        with zipfile.ZipFile(self.zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for src, arc in pairs:
                zf.write(src, arcname=str(arc))
                done += 1
                if done % 10 == 0 or done == total:
                    self.progress.emit(done, total, f"Archiving {arc}")
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
                        self.progress.emit(done, total, f"Skipping {dst_p.name}")
                    continue
                shutil.copy2(src_p, dst_p)
                done += 1
                if done % 25 == 0 or done == total:
                    self.progress.emit(done, total, f"Installing {dst_p.name}")
            self.finished.emit({"imported_files": total, "models_dir": str(models_dir)})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def start_worker(worker: Worker) -> QThread:
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.failed.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread


def analyse_disk(models_dir: Path, installed_models: list) -> dict:
    """Return per-model blob sizes and orphaned blobs."""
    from logger import log
    log.info("analyse_disk: START  dir=%s  models=%d", models_dir, len(installed_models))
    blobs_dir = models_dir / "blobs"
    result: dict = {
        "per_model": {},
        "orphan_blobs": [],
        "total_size": 0,
        "blobs_dir_size": 0,
        "manifests_dir_size": 0,
        "models_dir": str(models_dir),
    }
    if not models_dir.exists():
        log.warning("analyse_disk: models_dir does not exist: %s", models_dir)
        return result

    all_blobs: dict = {}
    if blobs_dir.exists():
        for p in blobs_dir.iterdir():
            if p.is_file():
                all_blobs[p.name] = p
                result["blobs_dir_size"] += p.stat().st_size

    referenced: set = set()
    for model_name in installed_models:
        mf = find_manifest_file(models_dir, model_name)
        if mf is None:
            continue
        try:
            digests, _ = parse_manifest_for_digests(mf)
        except Exception:
            continue
        model_blobs, model_size = [], 0
        for d in digests:
            bn = blob_filename_from_digest(d)
            referenced.add(bn)
            bp = blobs_dir / bn
            if bp.exists():
                sz = bp.stat().st_size
                model_blobs.append(bp)
                model_size += sz
        result["per_model"][model_name] = {"blobs": model_blobs, "size": model_size}

    for bn, bp in all_blobs.items():
        if bn not in referenced:
            result["orphan_blobs"].append({"path": bp, "size": bp.stat().st_size})

    manifests_dir = models_dir / "manifests"
    if manifests_dir.exists():
        for p in manifests_dir.rglob("*"):
            if p.is_file():
                result["manifests_dir_size"] += p.stat().st_size

    result["total_size"] = result["blobs_dir_size"] + result["manifests_dir_size"]
    result["orphan_blobs"].sort(key=lambda x: x["size"], reverse=True)
    from logger import log
    log.info(
        "analyse_disk: DONE  models=%d  blobs_sz=%s  orphans=%d",
        len(result["per_model"]),
        result["blobs_dir_size"],
        len(result["orphan_blobs"]),
    )
    return result
