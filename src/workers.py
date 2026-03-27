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
    pyqtSignal, QTimer, QSize)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QPalette, QPixmap, QPainter,
    QBrush, QPen, QLinearGradient, QSyntaxHighlighter, QTextCharFormat)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QProgressBar, QScrollArea, QSizePolicy, QSplitter, QSpacerItem,
    QStackedWidget, QStatusBar, QTabWidget, QTableView, QTextEdit,
    QToolBar, QVBoxLayout, QWidget, QAbstractItemView)

from utils import (
    human_bytes, blob_filename_from_digest, safe_mkdir,
    iter_unique, get_system_stats)
from client import OllamaClient
from models import (
    analyse_disk,
    InstalledModelRow, RunningModelRow,
    find_manifest_file, parse_manifest_for_digests,
    compute_export_files_for_models)

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



# ─────────────────────────────────────────────
#  REGISTRY WORKER
# ─────────────────────────────────────────────

class RegistryFetchWorker(Worker):
    """Fetches model list + optionally tags for one model from ollama.com."""
    tags_ready = pyqtSignal(str, list)   # slug, tags

    def __init__(self, client: OllamaClient, fetch_tags_for: str = ""):
        super().__init__()
        self.client = client
        self.fetch_tags_for = fetch_tags_for

    def run(self):
        try:
            if self.fetch_tags_for:
                # Tags-only fetch — don't re-fetch the full model list
                try:
                    tags = self.client.fetch_model_tags(self.fetch_tags_for)
                    self.tags_ready.emit(self.fetch_tags_for, tags)
                except Exception as e:
                    self.tags_ready.emit(self.fetch_tags_for, [])
                self.finished.emit([])
            else:
                models = self.client.fetch_registry_models()
                if not models:
                    # Emit a non-fatal signal so the UI doesn't hang
                    self.failed.emit(
                        "Registry returned 0 models. "
                        "The ollama.com API may have changed, or your network "
                        "is blocking the request. Check your internet connection."
                    )
                    return
                self.finished.emit(models)
        except Exception as e:
            self.failed.emit(str(e))


# ─────────────────────────────────────────────
#  BENCHMARK WORKER
# ─────────────────────────────────────────────

@dataclass
class BenchResult:
    model: str
    prompt: str
    total_tokens: int = 0
    prompt_tokens: int = 0
    eval_tokens: int = 0
    total_ms: float = 0.0
    first_token_ms: float = 0.0
    tokens_per_sec: float = 0.0
    output: str = ""
    error: str = ""
    status: str = "pending"   # pending | running | done | error


class BenchmarkWorker(Worker):
    result_update = pyqtSignal(int, object)   # index, BenchResult

    def __init__(self, client: OllamaClient, runs: list[tuple[int, str, str]]):
        """
        runs: list of (index, model_name, prompt)
        """
        super().__init__()
        self.client = client
        self.runs = runs
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        results = []
        for idx, model, prompt in self.runs:
            if self._stop:
                break
            res = BenchResult(model=model, prompt=prompt, status="running")
            self.result_update.emit(idx, res)
            try:
                t_start = time.perf_counter()
                first_token_t = None
                output_parts = []
                last_obj = {}
                for obj in self.client.generate_stream(model, prompt):
                    if self._stop:
                        break
                    tok = obj.get("response", "")
                    if tok and first_token_t is None:
                        first_token_t = time.perf_counter()
                    output_parts.append(tok)
                    last_obj = obj
                    if obj.get("done"):
                        break
                t_end = time.perf_counter()
                elapsed_ms = (t_end - t_start) * 1000
                first_ms = ((first_token_t - t_start) * 1000) if first_token_t else 0.0

                res.output = "".join(output_parts).strip()
                res.total_ms = elapsed_ms
                res.first_token_ms = first_ms
                res.total_tokens = last_obj.get("eval_count", 0) + last_obj.get("prompt_eval_count", 0)
                res.eval_tokens = last_obj.get("eval_count", 0)
                res.prompt_tokens = last_obj.get("prompt_eval_count", 0)
                eval_dur_ns = last_obj.get("eval_duration", 0)
                if eval_dur_ns > 0 and res.eval_tokens > 0:
                    res.tokens_per_sec = res.eval_tokens / (eval_dur_ns / 1e9)
                elif elapsed_ms > 0 and res.eval_tokens > 0:
                    res.tokens_per_sec = res.eval_tokens / (elapsed_ms / 1000)
                res.status = "done"
            except Exception as e:
                res.error = str(e)
                res.status = "error"
            self.result_update.emit(idx, res)
            results.append(res)
            # Evict this model from VRAM before loading the next one.
            # Without this, all models accumulate in memory and later runs
            # get throttled or OOM-killed when VRAM is exhausted.
            self.client.unload_model(model)
        self.finished.emit(results)



# ─────────────────────────────────────────────
#  RESOURCE MONITOR WORKER
# ─────────────────────────────────────────────

class ResourceMonitorWorker(QObject):
    stats_ready = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        try:
            stats = get_system_stats()
            try:
                ps = self.client.ps()
                stats["running_models"] = ps.get("models", [])
            except Exception:
                stats["running_models"] = []
            self.stats_ready.emit(stats)
        except Exception as e:
            self.failed.emit(str(e))


# ─────────────────────────────────────────────
#  DISK ANALYSIS WORKER
# ─────────────────────────────────────────────

class DiskAnalysisWorker(Worker):
    def __init__(self, models_dir: Path, installed_models: list):
        super().__init__()
        self.models_dir = models_dir
        self.installed_models = installed_models

    def run(self):
        try:
            self.finished.emit((self.models_dir, self.installed_models))
        except Exception as e:
            self.failed.emit(str(e))


