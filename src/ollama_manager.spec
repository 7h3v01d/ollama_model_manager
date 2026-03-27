# -*- mode: python ; coding: utf-8 -*-
#
# Ollama Manager Pro — PyInstaller spec file
#
# Build with:
#   pyinstaller ollama_manager.spec
#
# Or use the helper script:
#   python build.py
#
# Output: dist/OllamaManagerPro/OllamaManagerPro.exe  (one-folder)
#     or: dist/OllamaManagerPro.exe                   (one-file, set onefile=True below)

import sys
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────
# SPECPATH is PyInstaller's built-in variable — the directory containing
# this .spec file. Since the spec lives alongside main.py in src/, we
# just use SPECPATH directly. This works regardless of the working
# directory you run pyinstaller / build.py from.
SRC  = Path(SPECPATH)
MAIN = str(SRC / "main.py")

# ── Collect all our source modules ───────────────────────────────────────
# PyInstaller finds them via Analysis, but listing them explicitly makes
# the dependency graph explicit and avoids missed imports.
OUR_MODULES = [
    "client", "logger", "models", "prompt_library",
    "tab_about", "tab_batch", "tab_benchmark", "tab_chat",
    "tab_disk", "tab_modelfile", "tab_monitor", "tab_prompts",
    "tab_registry", "tab_servers", "tab_transfer",
    "theme", "utils", "widgets", "window", "workers",
]

block_cipher = None

a = Analysis(
    [MAIN],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        # No bundled assets needed — all resources are code-generated.
        # If you add icons or images later, include them here:
        # (str(SRC / "assets"), "assets"),
        # Example: (str(SRC / "assets" / "icon.ico"), "."),
    ],
    hiddenimports=[
        # Our own modules (ensures they're included even if not
        # statically detectable by PyInstaller's graph walk)
        *OUR_MODULES,

        # PyQt6 — include all used submodules explicitly
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.sip",

        # PyQt6 platform plugins (required on Windows)
        "PyQt6.Qt6.plugins.platforms",

        # requests internals that get missed by static analysis
        "requests",
        "requests.adapters",
        "requests.auth",
        "requests.cookies",
        "requests.exceptions",
        "requests.models",
        "requests.sessions",
        "requests.structures",
        "requests.utils",
        "urllib3",
        "urllib3.util",
        "urllib3.util.retry",
        "certifi",
        "charset_normalizer",
        "idna",

        # sqlite3 — stdlib but sometimes needs explicit inclusion on Windows
        "sqlite3",
        "_sqlite3",

        # psutil — optional, included so Monitor tab works if installed
        "psutil",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Explicitly exclude heavy packages we don't use
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "scipy",
        "IPython",
        "notebook",
        "PyQt5",
        "PySide2",
        "PySide6",
        "wx",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── Choose ONE-FOLDER (recommended) or ONE-FILE below ─────────────────
#
# ONE-FOLDER:  Fast startup. Easier to debug. Distribute the whole folder.
# ONE-FILE:    Single .exe — slower first launch (extracts to %TEMP% each run).
#              Set onefile = True to use this mode.

onefile = False

if onefile:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="OllamaManagerPro",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        # icon="assets/icon.ico",  # uncomment when you have an icon
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="OllamaManagerPro",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        console=False,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        # icon="assets/icon.ico",
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="OllamaManagerPro",
    )
