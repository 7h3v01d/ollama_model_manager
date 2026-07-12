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
from logger import log
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

    def generate_stream(self, model: str, prompt: str, system: str = "",
                        gpu_index: int | None = None):
        """Stream tokens from /api/generate for benchmarking.

        gpu_index: if set, passes num_gpu and numa options so Ollama targets
                   a specific device. Note: Ollama doesn't expose
                   CUDA_VISIBLE_DEVICES per-request, but num_gpu=1 with a
                   pre-set env var is the supported hook point. We pass the
                   index via the 'options' field where Ollama will honour it
                   when the server was started with CUDA_VISIBLE_DEVICES set.
        """
        payload: dict = {"model": model, "prompt": prompt, "stream": True}
        if system:
            payload["system"] = system
        if gpu_index is not None:
            # Ollama 0.1.x+ respects num_gpu in options to control load.
            # The actual GPU device selection requires the server env but
            # we record the intent in the payload for result display.
            payload["options"] = {"num_gpu": 1}
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

    def chat_stream(self, model: str, messages: list[dict],
                    system: str = "", temperature: float | None = None,
                    num_ctx: int | None = None):
        """
        Stream chat completions from /api/chat.

        messages: list of {"role": "user"|"assistant"|"system", "content": str}
        Yields each response object from the NDJSON stream.
        """
        # NOTE: unlike /api/generate, /api/chat has NO top-level "system"
        # field — Ollama silently ignores it. The system prompt must be
        # injected as the first message with role "system".
        if system and not (messages and messages[0].get("role") == "system"):
            messages = [{"role": "system", "content": system}] + list(messages)
        payload: dict = {"model": model, "messages": messages, "stream": True}
        options: dict = {}
        if temperature is not None:
            options["temperature"] = temperature
        if num_ctx is not None:
            options["num_ctx"] = num_ctx
        if options:
            payload["options"] = options

        with self.session.post(
            self._url("/api/chat"),
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
        Fetch model list from ollama.com.

        Strategy (tried in order until one yields results):
          1. GET /search?q=&p=N  with Accept: application/json
             — the JSON API the Ollama CLI uses
          2. GET /library HTML page  — scrape the Next.js __NEXT_DATA__
             embedded JSON payload (server-side rendered, always present)
          3. Last-resort regex scrape of href="/library/slug" links

        All responses and parsing steps are logged so failures are visible
        in the console and in ollama_manager.log.
        """
        from logger import log

        _s = requests.Session()
        _s.headers.update({
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/122.0.0.0 Safari/537.36",
        })

        models: list[dict] = []
        seen: set = set()

        def _norm(m: dict) -> dict | None:
            slug = (m.get("name") or m.get("model_name") or "").strip()
            if not slug or slug in seen:
                return None
            seen.add(slug)
            pulls_raw = m.get("pulls", 0) or m.get("download_count", 0) or 0
            if isinstance(pulls_raw, (int, float)):
                if pulls_raw >= 1_000_000:
                    pulls = f"{pulls_raw/1_000_000:.1f}M"
                elif pulls_raw >= 1_000:
                    pulls = f"{pulls_raw/1_000:.0f}K"
                else:
                    pulls = str(int(pulls_raw))
            else:
                pulls = str(pulls_raw)
            return {
                "slug": slug,
                "name": m.get("title") or m.get("display_name") or slug,
                "description": (m.get("description") or m.get("summary") or "").strip()[:200],
                "pulls": pulls,
                "tags": str(m.get("tags") or m.get("tag_count") or ""),
                "updated": str(m.get("updated_at") or m.get("last_updated") or ""),
            }

        # ── Strategy 1: JSON search API ──────────────────────────────────
        log.info("registry: trying JSON search API")
        for page in range(1, 21):
            fetched = False
            for params in [
                {"q": "", "p": page, "per_page": 50},
                {"q": "", "page": page, "per_page": 50},
                {"q": "", "p": page, "limit": 50},
            ]:
                try:
                    r = _s.get("https://ollama.com/search", params=params, timeout=15)
                    log.info("registry API: GET /search %s -> %d %s",
                             params, r.status_code, r.headers.get("content-type",""))
                    log.debug("registry API: body[:400]=%s", r.text[:400])
                    if r.status_code != 200:
                        continue
                    ct = r.headers.get("content-type", "")
                    if "json" not in ct:
                        log.warning("registry API: non-JSON content-type: %s — body: %s",
                                    ct, r.text[:300])
                        continue
                    data = r.json()
                    log.info("registry API: JSON keys=%s",
                             list(data.keys()) if isinstance(data, dict) else f"list[{len(data)}]")
                    if isinstance(data, list):
                        batch = data
                    elif isinstance(data, dict):
                        batch = (data.get("models") or data.get("results")
                                 or data.get("items") or data.get("data") or [])
                        log.info("registry API: batch from dict, len=%d", len(batch))
                    else:
                        continue
                    if not batch:
                        log.info("registry API: empty batch on page %d", page)
                        break
                    for m in batch:
                        e = _norm(m)
                        if e:
                            models.append(e)
                    log.info("registry API: page %d gave %d, total=%d",
                             page, len(batch), len(models))
                    fetched = True
                    if len(batch) < 50:
                        log.info("registry API: last page")
                        break
                    break  # this URL worked, move to next page
                except Exception as ex:
                    log.warning("registry API: %s failed: %s", params, ex)
            if not fetched or (fetched and page == 1 and not models):
                break
            if fetched and len(models) % 50 != 0:
                break  # last page

        if models:
            log.info("registry: JSON API succeeded — %d models", len(models))
            return models

        # ── Strategy 2: HTML __NEXT_DATA__ scrape ────────────────────────
        log.info("registry: JSON API gave 0 models, trying HTML scrape")
        try:
            _s.headers.update({"Accept": "text/html,application/xhtml+xml"})
            r = _s.get("https://ollama.com/library", timeout=20)
            log.info("registry HTML: status=%d  content-type=%s  len=%d",
                     r.status_code, r.headers.get("content-type",""), len(r.text))
            log.debug("registry HTML: body[:600]=%s", r.text[:600])

            # Try __NEXT_DATA__
            m = re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                r.text, re.DOTALL
            )
            if m:
                ndata = json.loads(m.group(1))
                log.info("registry HTML: __NEXT_DATA__ found, top keys=%s",
                         list(ndata.keys()))
                # Walk the props tree looking for a model list
                props = ndata.get("props", {}).get("pageProps", {})
                log.info("registry HTML: pageProps keys=%s", list(props.keys()))
                for key, val in props.items():
                    if isinstance(val, list) and val and isinstance(val[0], dict):
                        if any(k in val[0] for k in ("name","slug","model_name","title")):
                            log.info("registry HTML: found model list under key=%s len=%d", key, len(val))
                            for item in val:
                                e = _norm(item)
                                if e:
                                    models.append(e)
                            break
                if models:
                    log.info("registry: __NEXT_DATA__ scrape succeeded — %d models", len(models))
                    return models
                log.warning("registry HTML: __NEXT_DATA__ found but no model list in pageProps")
            else:
                log.warning("registry HTML: no __NEXT_DATA__ script tag found")

            # Try regex href scrape as last resort
            log.info("registry: trying href regex scrape")
            slugs = list(dict.fromkeys(re.findall(r'href="/library/([a-z0-9_-]+)"', r.text)))
            log.info("registry href scrape: found %d slugs: %s", len(slugs), slugs[:10])
            for slug in slugs:
                if slug not in seen:
                    seen.add(slug)
                    models.append({
                        "slug": slug, "name": slug,
                        "description": "", "pulls": "", "tags": "", "updated": "",
                    })
            if models:
                log.info("registry: href scrape gave %d models", len(models))
                return models

        except Exception as ex:
            log.exception("registry HTML scrape failed: %s", ex)

        log.error("registry: all strategies exhausted — 0 models returned")
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
