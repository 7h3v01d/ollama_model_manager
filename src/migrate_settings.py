"""
migrate_settings.py — One-shot migration from legacy KeystoneAI paths to 7h3v01d.

Run automatically on startup via MainWindow.__init__ (see window.py patch).
Safe to call multiple times — idempotent.

Files migrated:
  %APPDATA%/KeystoneAI/ollama_manager_servers.json → 7h3v01d/
  %APPDATA%/KeystoneAI/prompt_library.db           → 7h3v01d/
  %APPDATA%/KeystoneAI/chat_history.db             → 7h3v01d/  (if it exists)
"""
from __future__ import annotations
import os
import platform
import shutil
from pathlib import Path

from logger import log

FILES_TO_MIGRATE = [
    "ollama_manager_servers.json",
    "prompt_library.db",
    "chat_history.db",
]


def _get_base() -> Path:
    if platform.system() == "Windows":
        return Path(os.environ.get("APPDATA", Path.home()))
    return Path.home() / ".config"


def migrate_keystone_to_7h3v01d() -> list[str]:
    """
    Copy any files found under KeystoneAI/ to 7h3v01d/ if the destination
    doesn't already exist. Returns a list of filenames that were migrated.
    """
    base   = _get_base()
    old_dir = base / "KeystoneAI"
    new_dir = base / "7h3v01d"

    if not old_dir.exists():
        return []

    new_dir.mkdir(parents=True, exist_ok=True)
    migrated = []

    for fname in FILES_TO_MIGRATE:
        src = old_dir / fname
        dst = new_dir / fname
        if src.exists() and not dst.exists():
            try:
                shutil.copy2(str(src), str(dst))
                migrated.append(fname)
                log.info("migrate_settings: copied %s → %s", src, dst)
            except Exception as e:
                log.warning("migrate_settings: failed to copy %s: %s", fname, e)

    return migrated
