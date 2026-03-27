# Packaging — Ollama Manager Pro

This document covers building a standalone Windows executable with PyInstaller.

---

## Prerequisites

Everything must be installed in the **same Python environment** you build from.
Using a clean virtual environment is strongly recommended.

```bat
:: Create and activate a clean venv
python -m venv .venv
.venv\Scripts\activate

:: Install app dependencies
pip install -r requirements.txt

:: Install build tools
pip install pyinstaller pyinstaller-hooks-contrib
```

Verify PyInstaller is available:

```bat
pyinstaller --version
```

---

## Quick build (recommended)

```bat
cd src
python build.py
```

The helper script checks dependencies, patches the spec, and runs PyInstaller.
Output lands in `src/dist/OllamaManagerPro/`.

### Options

| Flag | Effect |
|---|---|
| `python build.py` | One-folder build — fast startup, distribute the whole folder |
| `python build.py --onefile` | Single `.exe` — slower first launch, extracts to `%TEMP%` |
| `python build.py --clean` | Delete `dist/` and `build/` before building |
| `python build.py --debug` | Keep the console window — useful if the app crashes on startup |

---

## Manual build

```bat
cd src
pyinstaller ollama_manager.spec --noconfirm
```

---

## Output structure (one-folder mode)

```
dist/
└── OllamaManagerPro/
    ├── OllamaManagerPro.exe    ← launch this
    ├── PyQt6/                  ← Qt6 runtime + platform plugins
    ├── _internal/              ← Python runtime + all modules
    └── ...
```

Distribute the entire `OllamaManagerPro/` folder.
Users run `OllamaManagerPro.exe` — no Python installation required.

---

## Runtime file locations

These files are **not** bundled inside the `.exe` — they are created at
runtime in user-writable locations.

| File | Location | Purpose |
|---|---|---|
| `ollama_manager.log` | Next to `OllamaManagerPro.exe` | Debug log |
| `ollama_manager_servers.json` | `%APPDATA%\KeystoneAI\` | Saved server list |
| `prompt_library.db` | `%APPDATA%\KeystoneAI\` | Prompt library SQLite DB |

---

## Troubleshooting

### App crashes immediately on launch

Build with the debug flag to see the console output:

```bat
python build.py --debug
```

Then run `OllamaManagerPro.exe` from a Command Prompt window and read the
traceback. The most common cause is a missing hidden import — add it to the
`hiddenimports` list in `ollama_manager.spec`.

### `ModuleNotFoundError` for a third-party package

Add the package to `hiddenimports` in `ollama_manager.spec`:

```python
hiddenimports=[
    ...
    "the_missing_package",
    "the_missing_package.submodule",
],
```

Then rebuild.

### PyQt6 platform plugin error (`qt.qpa.plugin`)

This means the Qt platform plugins weren't collected. Ensure:

```python
hiddenimports=[
    ...
    "PyQt6.Qt6.plugins.platforms",
],
```

is in the spec, and that you're using `pyinstaller-hooks-contrib >= 2023.0`.

### `psutil` not found at runtime (Monitor tab shows n/a)

`psutil` is optional — if it's not installed in your build environment,
the Monitor tab degrades gracefully. To include it:

```bat
pip install psutil
python build.py --clean
```

### Antivirus flags the `.exe`

Common with PyInstaller builds. Options:
- Submit the file to your AV vendor for whitelisting
- Code-sign the `.exe` with a certificate (set `codesign_identity` in the spec)
- Use one-folder mode — AV tools are less aggressive with folders than single `.exe` files

---

## Adding an icon

1. Create or convert your icon to `.ico` format (256×256 recommended).
   The existing `PyPackager` tool can convert PNG → ICO.

2. Place it at `src/assets/icon.ico`.

3. Uncomment the icon line in `ollama_manager.spec`:

```python
exe = EXE(
    ...
    icon="assets/icon.ico",
)
```

4. Add it to `datas` so it's available at runtime if needed:

```python
datas=[
    ("assets", "assets"),
],
```

---

## Reducing build size

The one-folder build is typically 80–120 MB due to the Qt6 runtime.
To reduce it:

- Ensure the excludes list in the spec includes packages you don't use
- Run `upx --best` on the output binaries (UPX is enabled in the spec by default)
- Consider using `--onefile` — UPX compresses the archive more aggressively

---

## Version bumping

Before a release build, update the version string in `main.py`:

```python
app.setApplicationVersion("4.1.0")
```

And the `APP_VERSION` constant in `tab_about.py`:

```python
APP_VERSION = "4.1.0"
```
