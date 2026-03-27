"""
logger.py — Centralised logging for Ollama Manager Pro.

Writes to both the console and a rolling log file next to main.py.
Import and use anywhere:

    from logger import log
    log.debug("worker started")
    log.info("scan complete")
    log.warning("manifest not found: %s", path)
    log.error("analyse_disk failed: %s", exc)
"""
import logging
import sys
from datetime import datetime
from pathlib import Path


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("OllamaManagerPro")
    if logger.handlers:
        return logger          # already configured (e.g. hot-reload)

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(module)s:%(lineno)d  —  %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Console handler ───────────────────────────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # ── File handler ──────────────────────────────────────────────────
    log_dir = Path(__file__).parent
    log_path = log_dir / "ollama_manager.log"

    try:
        fh = logging.FileHandler(log_path, encoding="utf-8", mode="a")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.info("=" * 60)
        logger.info("Ollama Manager Pro — session started %s",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("Log file: %s", log_path)
    except Exception as e:
        logger.warning("Could not open log file %s: %s", log_path, e)

    return logger


log: logging.Logger = _build_logger()
