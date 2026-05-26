#!/usr/bin/env python3
"""Build the BVOD Processor into a standalone distributable.

Run this once on each platform:
  python build.py          # builds for current OS

Prerequisites on the build machine (NOT needed on end-user machines):
  pip install pyinstaller
  ffmpeg installed:
    Mac     — brew install ffmpeg
    Windows — winget install Gyan.FFmpeg  (then restart terminal)

Output:
  dist/BVOD Processor/          ← Windows — zip and share
  dist/BVOD Processor.app       ← Mac    — zip and share
"""

import os
import sys
import shutil
import platform
import subprocess
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
APP_NAME   = "BVOD Processor"
BIN_DIR    = SCRIPT_DIR / "bin"


def abort(msg: str):
    print(f"\n❌  {msg}\n")
    sys.exit(1)


def find_or_abort(tool: str) -> Path:
    path = shutil.which(tool)
    if not path:
        if sys.platform == "win32":
            hint = "  winget install Gyan.FFmpeg\n  (then restart your terminal)"
        else:
            hint = "  brew install ffmpeg"
        abort(f"{tool} not found in PATH.\n\nInstall it first:\n{hint}")
    return Path(path)


def main():
    print(f"Building {APP_NAME}")
    print(f"Platform : {platform.system()} {platform.machine()}\n")

    # ── 1. Copy ffmpeg + ffprobe into bin/ ────────────────────────────
    BIN_DIR.mkdir(exist_ok=True)
    binaries = []
    for tool in ("ffmpeg", "ffprobe"):
        src = find_or_abort(tool)
        dst = BIN_DIR / src.name
        print(f"  Copying {src.name}  ({src})")
        shutil.copy2(src, dst)
        dst.chmod(0o755)
        binaries.append(dst)

    # ── 2. Build with PyInstaller ─────────────────────────────────────
    sep = ";" if sys.platform == "win32" else ":"
    add_binary_args = []
    for b in binaries:
        add_binary_args += [f"--add-binary={b}{sep}."]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",
        "--windowed",
        "--name", APP_NAME,
        "--collect-data", "customtkinter",
        "--collect-all",  "tkinterdnd2",
        *add_binary_args,
        "--noconfirm",
        "--clean",
        str(SCRIPT_DIR / "app.py"),
    ]

    print("\nRunning PyInstaller (this takes a minute)...\n")
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    if result.returncode != 0:
        abort("PyInstaller failed. See output above.")

    # ── 3. Copy assets next to the executable ────────────────────────
    if sys.platform == "darwin":
        # On Mac, --onedir --windowed creates a .app bundle.
        # Assets go inside the bundle's Resources folder.
        dist_assets = SCRIPT_DIR / "dist" / f"{APP_NAME}.app" / "Contents" / "MacOS"
    else:
        dist_assets = SCRIPT_DIR / "dist" / APP_NAME

    for asset in ("titlecard.png", "config.json"):
        src = SCRIPT_DIR / asset
        if src.exists():
            shutil.copy2(src, dist_assets / asset)
            print(f"  Copied {asset} → dist/")

    # ── 4. Done ───────────────────────────────────────────────────────
    if sys.platform == "darwin":
        dist_path = SCRIPT_DIR / "dist" / f"{APP_NAME}.app"
        print(f"\n✅  Done!\n")
        print(f"  App:      {dist_path}")
        print(f"  Share:    zip the .app and send it")
        print(f"  Run:      double-click {APP_NAME}.app in Finder")
        print(f"\n  NOTE: First launch on a new Mac may need:")
        print(f"    Right-click → Open  (to bypass Gatekeeper)")
    else:
        dist_path = SCRIPT_DIR / "dist" / APP_NAME
        print(f"\n✅  Done!\n")
        print(f"  Folder:   {dist_path}")
        print(f"  Share:    zip the '{APP_NAME}' folder and send it")
        print(f"  Run:      {APP_NAME}.exe inside the folder")

    print()


if __name__ == "__main__":
    main()
