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

