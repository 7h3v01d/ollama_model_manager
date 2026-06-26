"""
build.py — Ollama Manager Pro build helper.

Usage:
    python build.py              # one-folder build (recommended)
    python build.py --onefile    # single .exe (slower startup)
    python build.py --clean      # delete dist/ and build/ first
    python build.py --debug      # show console window (for debugging)

Requirements:
    pip install pyinstaller pyinstaller-hooks-contrib
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "ollama_manager.spec"


def check_deps():
    """Verify PyInstaller is available."""
    try:
        import PyInstaller  # noqa: F401
        print(f"  ✓ PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("  ✗ PyInstaller not found.")
        print("    Install with:  pip install pyinstaller pyinstaller-hooks-contrib")
        sys.exit(1)

    try:
        import PyQt6  # noqa: F401
        print(f"  ✓ PyQt6 found")
    except ImportError:
        print("  ✗ PyQt6 not found — install requirements first")
        sys.exit(1)

    try:
        import requests  # noqa: F401
        print(f"  ✓ requests {requests.__version__}")
    except ImportError:
        print("  ✗ requests not found — install requirements first")
        sys.exit(1)


def clean():
    for d in (DIST, BUILD):
        if d.exists():
            shutil.rmtree(d)
            print(f"  Removed {d}")


def patch_spec(onefile: bool, debug: bool):
    """Patch the spec file flags before building."""
    src = SPEC.read_text(encoding="utf-8")
    src = src.replace(
        "onefile = False" if not onefile else "onefile = True",
        f"onefile = {onefile}"
    )
    src = src.replace(
        "console=False,          # no console window",
        f"console={debug},"
    )
    SPEC.write_text(src, encoding="utf-8")


def build(onefile: bool, debug: bool):
    print(f"\nBuilding {'one-file' if onefile else 'one-folder'} "
          f"{'(debug console)' if debug else '(no console)'}…\n")

    patch_spec(onefile, debug)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(SPEC),
        "--noconfirm",
    ]
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print("\n✗ Build failed. Check the output above for errors.")
        sys.exit(result.returncode)

    # Find the output
    if onefile:
        exe = DIST / "OllamaManagerPro.exe"
        if exe.exists():
            size_mb = exe.stat().st_size / 1024 / 1024
            print(f"\n✓ Built: {exe}  ({size_mb:.1f} MB)")
        else:
            print(f"\n✓ Build complete. Check {DIST}/")
    else:
        folder = DIST / "OllamaManagerPro"
        exe    = folder / "OllamaManagerPro.exe"
        if exe.exists():
            total = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
            size_mb = total / 1024 / 1024
            print(f"\n✓ Built: {exe}")
            print(f"  Folder: {folder}  ({size_mb:.1f} MB total)")
            print(f"\n  Distribute the entire '{folder.name}' folder.")
            print(f"  Users run:  OllamaManagerPro.exe")
        else:
            print(f"\n✓ Build complete. Check {DIST}/")

    print("\nData files (stored in %APPDATA%/7h3v01d/ at runtime):")
    print("  ollama_manager_servers.json  — saved server list")
    print("  prompt_library.db            — prompt library SQLite database")
    print("\nLog file written next to the .exe:")
    print("  ollama_manager.log")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Ollama Manager Pro")
    parser.add_argument("--onefile", action="store_true",
                        help="Build a single .exe (slower startup)")
    parser.add_argument("--clean",   action="store_true",
                        help="Delete dist/ and build/ before building")
    parser.add_argument("--debug",   action="store_true",
                        help="Show console window (useful for debugging)")
    args = parser.parse_args()

    print("Ollama Manager Pro — Build Script\n")
    print("Checking dependencies…")
    check_deps()

    if args.clean:
        print("\nCleaning previous build…")
        clean()

    build(onefile=args.onefile, debug=args.debug)
