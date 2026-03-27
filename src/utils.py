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


# ─────────────────────────────────────────────
#  UTILITIES
# ─────────────────────────────────────────────

DEFAULT_BASE_URL = "http://localhost:11434"


def human_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024.0 or u == units[-1]:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= 1024.0
    return f"{f:.1f} TB"


def parse_time(s: str | None) -> str:
    if not s:
        return "—"
    try:
        s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
        dt = datetime.fromisoformat(s2)
        return dt.astimezone().strftime("%Y-%m-%d  %H:%M")
    except Exception:
        return s


def safe_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def default_models_dir_guess() -> Path:
    env = os.environ.get("OLLAMA_MODELS")
    if env:
        return Path(env).expanduser().resolve()
    user = Path(os.environ.get("USERPROFILE", str(Path.home())))
    return (user / ".ollama" / "models").resolve()


def normalize_model_name(name: str) -> tuple[str, str]:
    s = (name or "").strip()
    if not s:
        return ("", "latest")
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



# ─────────────────────────────────────────────
#  SYSTEM RESOURCE HELPERS
# ─────────────────────────────────────────────

def _try_psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def get_system_stats() -> dict:
    """Poll CPU%, RAM, and GPU VRAM. Returns dict with None for unavailable."""
    stats: dict = {
        "cpu_pct": None,
        "ram_used": None,
        "ram_total": None,
        "gpu_vram_used": None,
        "gpu_vram_total": None,
        "gpu_util_pct": None,
        "gpu_name": None,
    }
    psutil = _try_psutil()
    if psutil:
        try:
            stats["cpu_pct"] = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            stats["ram_used"]  = vm.used
            stats["ram_total"] = vm.total
        except Exception:
            pass
    # NVIDIA
    try:
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=3
        ).decode().strip()
        parts = [p.strip() for p in out.split(",")]
        if len(parts) >= 4:
            stats["gpu_name"]       = parts[0]
            stats["gpu_vram_used"]  = int(parts[1]) * 1024 * 1024
            stats["gpu_vram_total"] = int(parts[2]) * 1024 * 1024
            stats["gpu_util_pct"]   = float(parts[3])
    except Exception:
        pass
    # ROCm fallback
    if stats["gpu_vram_used"] is None:
        try:
            out = subprocess.check_output(
                ["rocm-smi", "--showmeminfo", "vram", "--json"],
                stderr=subprocess.DEVNULL, timeout=3
            ).decode()
            data = json.loads(out)
            for card, info in data.items():
                if "card" in card.lower():
                    used  = int(info.get("VRAM Total Used Memory (B)", 0))
                    total = int(info.get("VRAM Total Memory (B)", 0))
                    if total > 0:
                        stats["gpu_vram_used"]  = used
                        stats["gpu_vram_total"] = total
                        stats["gpu_name"]       = card
                    break
        except Exception:
            pass
    return stats


# ─────────────────────────────────────────────
#  DISK ANALYSIS HELPER
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
#  SERVER REGISTRY  (persisted to disk)
# ─────────────────────────────────────────────

@dataclass
class ServerEntry:
    name:    str          # display label e.g. "Local", "Remote GPU"
    url:     str          # e.g. "http://localhost:11434"
    notes:   str = ""
    active:  bool = False # which one is the current active server

    def to_dict(self) -> dict:
        return {"name": self.name, "url": self.url,
                "notes": self.notes, "active": self.active}

    @staticmethod
    def from_dict(d: dict) -> "ServerEntry":
        return ServerEntry(
            name=d.get("name", ""),
            url=d.get("url", DEFAULT_BASE_URL),
            notes=d.get("notes", ""),
            active=d.get("active", False),
        )


class ServerRegistry:
    """
    Persists a list of Ollama server entries to a JSON file.
    Storage: %APPDATA%/KeystoneAI/ollama_manager_servers.json  (Windows)
             ~/.config/KeystoneAI/ollama_manager_servers.json   (Linux/Mac)
    """

    def __init__(self):
        self._path = self._default_path()
        self._entries: list[ServerEntry] = []
        self._load()

    @staticmethod
    def _default_path() -> Path:
        import platform
        if platform.system() == "Windows":
            base = Path(os.environ.get("APPDATA", Path.home()))
        else:
            base = Path.home() / ".config"
        p = base / "KeystoneAI"
        p.mkdir(parents=True, exist_ok=True)
        return p / "ollama_manager_servers.json"

    # ── Persistence ───────────────────────────────────────────────────

    def _load(self):
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._entries = [ServerEntry.from_dict(d) for d in data]
        except Exception:
            self._entries = []
        # Always ensure at least the default local server exists
        if not self._entries:
            self._entries = [ServerEntry(
                name="Local", url=DEFAULT_BASE_URL, active=True)]
        # Ensure exactly one active
        active = [e for e in self._entries if e.active]
        if not active:
            self._entries[0].active = True

    def _save(self):
        try:
            self._path.write_text(
                json.dumps([e.to_dict() for e in self._entries],
                           indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ── Public API ────────────────────────────────────────────────────

    def entries(self) -> list[ServerEntry]:
        return list(self._entries)

    def active(self) -> ServerEntry:
        for e in self._entries:
            if e.active:
                return e
        return self._entries[0]

    def set_active(self, url: str):
        for e in self._entries:
            e.active = (e.url.rstrip("/") == url.rstrip("/"))
        if not any(e.active for e in self._entries):
            self._entries[0].active = True
        self._save()

    def add(self, name: str, url: str, notes: str = "") -> ServerEntry:
        # Update if URL already exists
        for e in self._entries:
            if e.url.rstrip("/") == url.rstrip("/"):
                e.name  = name
                e.notes = notes
                self._save()
                return e
        entry = ServerEntry(name=name, url=url, notes=notes)
        self._entries.append(entry)
        self._save()
        return entry

    def remove(self, url: str):
        was_active = self.active().url.rstrip("/") == url.rstrip("/")
        self._entries = [e for e in self._entries
                         if e.url.rstrip("/") != url.rstrip("/")]
        if not self._entries:
            self._entries = [ServerEntry(name="Local",
                                         url=DEFAULT_BASE_URL, active=True)]
        if was_active:
            self._entries[0].active = True
        self._save()

    def rename(self, url: str, new_name: str, new_notes: str = ""):
        for e in self._entries:
            if e.url.rstrip("/") == url.rstrip("/"):
                e.name  = new_name
                e.notes = new_notes
                break
        self._save()

