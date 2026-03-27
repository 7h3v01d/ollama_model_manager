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

from utils import human_bytes


# ─────────────────────────────────────────────
#  OLLAMA API CLIENT
# ─────────────────────────────────────────────

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

    def ping(self) -> bool:
        try:
            r = self.session.get(self._url("/"), timeout=3)
            return r.status_code < 500
        except Exception:
            return False

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

    def unload_model(self, model: str) -> None:
        """
        Evict a model from VRAM by sending keep_alive=0.
        Ollama unloads a model from memory when keep_alive reaches 0.
        """
        try:
            payload = {"model": model, "keep_alive": 0, "prompt": "", "stream": False}
            self.session.post(
                self._url("/api/generate"),
                data=json.dumps(payload),
                timeout=10,
            )
        except Exception:
            pass   # Best-effort — don't block the benchmark loop

    def generate_stream(self, model: str, prompt: str, system: str = ""):
        """Stream tokens from /api/generate for benchmarking."""
        payload = {"model": model, "prompt": prompt, "stream": True}
        if system:
            payload["system"] = system
        with self.session.post(
            self._url("/api/generate"),
            data=json.dumps(payload),
            timeout=120,
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

    def fetch_registry_models(self) -> list[dict]:
        """
        Fetch models from ollama.com using a clean requests.Session.
        Uses the same JSON search API the Ollama CLI calls.

        Key fixes vs previous version:
        - Uses a fresh session with NO Content-Type header (GET requests
          must not send Content-Type; it breaks CDN caching and some proxies)
        - Tries both "p" and "page" param names (Ollama has used both)
        - Hard cap of 20 pages to prevent infinite loops
        - Accepts any batch size < requested as end-of-results signal
        - Falls back to a single-page fetch if pagination fails
        """
        _session = requests.Session()
        _session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; OllamaManagerPro/4.0)",
        })
        models: list[dict] = []
        seen_slugs: set = set()
        MAX_PAGES = 20
        PER_PAGE = 50   # conservative — less likely to hit timeouts

        def _normalise(m: dict) -> dict | None:
            slug = (m.get("name") or "").strip()
            if not slug or slug in seen_slugs:
                return None
            seen_slugs.add(slug)
            pulls_raw = m.get("pulls", 0) or 0
            if isinstance(pulls_raw, (int, float)):
                if pulls_raw >= 1_000_000:
                    pulls = f"{pulls_raw / 1_000_000:.1f}M"
                elif pulls_raw >= 1_000:
                    pulls = f"{pulls_raw / 1_000:.0f}K"
                else:
                    pulls = str(int(pulls_raw))
            else:
                pulls = str(pulls_raw)
            return {
                "slug": slug,
                "name": m.get("title") or slug,
                "description": (m.get("description") or "").strip(),
                "pulls": pulls,
                "tags": str(m.get("tags", "")),
                "updated": m.get("updated_at", ""),
            }

        for page in range(1, MAX_PAGES + 1):
            try:
                r = _session.get(
                    "https://ollama.com/search",
                    params={"q": "", "p": page, "per_page": PER_PAGE},
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json()
            except Exception:
                # Try alternate param name on first page failure
                if page == 1:
                    try:
                        r = _session.get(
                            "https://ollama.com/search",
                            params={"q": "", "page": page, "per_page": PER_PAGE},
                            timeout=15,
                        )
                        r.raise_for_status()
                        data = r.json()
                    except Exception:
                        break
                else:
                    break

            # Accept any top-level list or dict with a "models" key
            if isinstance(data, list):
                batch = data
            else:
                batch = data.get("models") or data.get("results") or []

            for m in batch:
                entry = _normalise(m)
                if entry:
                    models.append(entry)

            # Stop when we get fewer results than requested (last page)
            if len(batch) < PER_PAGE:
                break

        return models

    def fetch_model_tags(self, slug: str) -> list[dict]:
        """
        Fetch available tags for a model from ollama.com.

        Strategy:
        1. Try the search JSON API with the model name — some responses
           include a tag_list field.
        2. Fall back to fetching the tags HTML page and extracting
           embedded __NEXT_DATA__ JSON (Next.js SSR payload).
        3. Last resort: regex-scrape href="/library/{slug}:{tag}" links.
        """
        _session = requests.Session()
        _session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; OllamaManagerPro/4.0)",
        })

        # ── Strategy 1: search API tag_list ─────────────────────────────
        try:
            r = _session.get(
                "https://ollama.com/search",
                params={"q": slug, "per_page": 1},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            models = data.get("models", [])
            if models and (models[0].get("name") or "").lower() == slug.lower():
                tag_list = models[0].get("tag_list") or models[0].get("tags_detail") or []
                if tag_list and isinstance(tag_list, list) and isinstance(tag_list[0], dict):
                    return [
                        {"tag": t.get("name") or t.get("tag", ""),
                         "size": t.get("size") or t.get("disk_size", "")}
                        for t in tag_list
                    ][:40]
        except Exception:
            pass

        # ── Strategy 2: __NEXT_DATA__ in HTML tags page ──────────────────
        try:
            r = _session.get(
                f"https://ollama.com/library/{slug}/tags",
                timeout=15,
            )
            r.raise_for_status()
            html = r.text
            m = re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                html, re.DOTALL
            )
            if m:
                ndata = json.loads(m.group(1))
                props = ndata.get("props", {}).get("pageProps", {})
                for key in ("model", "modelData", "data"):
                    node = props.get(key, {})
                    if isinstance(node, dict):
                        tag_list = node.get("tags") or node.get("tag_list") or []
                        if tag_list and isinstance(tag_list, list):
                            result = []
                            for t in tag_list:
                                if isinstance(t, dict):
                                    result.append({
                                        "tag": t.get("name") or t.get("tag", ""),
                                        "size": t.get("size") or t.get("disk_size", ""),
                                    })
                                elif isinstance(t, str):
                                    result.append({"tag": t, "size": ""})
                            if result:
                                return result[:40]
                # Generic: any list of dicts with name/tag keys
                for val in props.values():
                    if isinstance(val, list) and val and isinstance(val[0], dict):
                        if "name" in val[0] or "tag" in val[0]:
                            return [
                                {"tag": t.get("name") or t.get("tag", ""),
                                 "size": t.get("size", "")}
                                for t in val[:40]
                            ]

            # ── Strategy 3: href scrape ──────────────────────────────────
            tags = []
            seen: set = set()
            pattern = rf'href="/library/{re.escape(slug)}:([^"{{}}\s]{{1,60}})"' 
            for tm in re.finditer(pattern, html):
                tag = tm.group(1).strip()
                if tag and tag not in seen:
                    seen.add(tag)
                    tags.append({"tag": tag, "size": ""})
            return tags[:40]

        except Exception:
            return []

# ─────────────────────────────────────────────
