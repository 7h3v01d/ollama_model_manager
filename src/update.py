"""
update.py — Replace all src files with the latest versions from the zip.

Run this from your src/ folder:
    python update.py ollama_manager_pro_v4.zip

Or drag the zip path as an argument if it's elsewhere.
"""
import sys
import zipfile
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        # Try to find the zip automatically next to this script
        candidates = list(Path(__file__).parent.glob("ollama_manager_pro*.zip"))
        if not candidates:
            print("Usage: python update.py <path_to_zip>")
            sys.exit(1)
        zip_path = sorted(candidates)[-1]
        print(f"Found zip: {zip_path}")
    else:
        zip_path = Path(sys.argv[1])

    dest = Path(__file__).parent  # same folder as this script

    with zipfile.ZipFile(zip_path) as zf:
        written = []
        for member in zf.namelist():
            fname = Path(member).name
            if not fname or fname == "update.py":
                continue
            content = zf.read(member)
            target = dest / fname
            target.write_bytes(content)
            written.append(fname)

    print(f"Updated {len(written)} files:")
    for f in sorted(written):
        print(f"  {f}")
    print("\nDone — run: python main.py")

if __name__ == "__main__":
    main()
