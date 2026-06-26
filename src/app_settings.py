"""
app_settings.py — Persistent app-level settings store for Ollama Manager Pro.

Storage: %APPDATA%/7h3v01d/app_settings.json  (Windows)
         ~/.config/7h3v01d/app_settings.json   (Linux/Mac)

Keys (with defaults):
    vg_enabled      bool   False
    vg_url          str    "http://192.168.0.163:8050"
    vg_endpoint     str    "/tts"
    vg_method       str    "POST"
    vg_payload_key  str    "text"         # JSON key that holds the text
    vg_test_phrase  str    "Voice gateway test. Hello from Ollama Manager."
    chat_tts_auto   bool   False          # TTS on by default in Chat tab
"""
from __future__ import annotations
import json
import os
import platform
from pathlib import Path
from logger import log

DEFAULTS: dict = {
    "vg_enabled":     False,
    "vg_url":         "http://192.168.0.163:8050",
    "vg_endpoint":    "/tts",
    "vg_method":      "POST",
    "vg_payload_key": "text",
    "vg_test_phrase": "Voice gateway test. Hello from Ollama Manager.",
    "chat_tts_auto":  False,
}


def _settings_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    p = base / "7h3v01d"
    p.mkdir(parents=True, exist_ok=True)
    return p / "app_settings.json"


class AppSettings:
    """Simple key-value store backed by a JSON file."""

    def __init__(self):
        self._path = _settings_path()
        self._data: dict = dict(DEFAULTS)
        self._load()

    def _load(self):
        try:
            if self._path.exists():
                stored = json.loads(self._path.read_text(encoding="utf-8"))
                self._data.update({k: v for k, v in stored.items() if k in DEFAULTS})
        except Exception as e:
            log.warning("AppSettings: load failed: %s", e)

    def _save(self):
        try:
            self._path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning("AppSettings: save failed: %s", e)

    def get(self, key: str):
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value):
        self._data[key] = value
        self._save()

    def set_many(self, updates: dict):
        self._data.update(updates)
        self._save()

    # Convenience properties
    @property
    def vg_full_url(self) -> str:
        base = self._data.get("vg_url", "").rstrip("/")
        ep   = self._data.get("vg_endpoint", "")
        if not ep.startswith("/"):
            ep = "/" + ep
        return base + ep
