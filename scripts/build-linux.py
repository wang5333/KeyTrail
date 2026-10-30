"""Build a native Linux x64 binary. Run on Linux, never cross-build on Windows."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".deps")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="KeyTrail")
    args = parser.parse_args()
    if sys.platform != "linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        parser.error("Build on Linux x86_64 (Ubuntu 22.04+ recommended)")
    if not args.name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._" for c in args.name):
        parser.error("Invalid executable filename")
    from app.core import VERSION
    from PyInstaller.__main__ import run
    licenses = ROOT / ".build/linux-licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    for name in ("embit", "cryptography", "cffi", "pycparser", "pyinstaller"):
        distribution = importlib.metadata.distribution(name)
        for entry in distribution.files or []:
            if "LICENSE" in entry.name.upper() or "COPYING" in entry.name.upper():
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    shutil.copyfile(source, licenses / (name + "-" + entry.name))
    arguments = ["--noconfirm", "--clean", "--onefile", "--console",
        "--name", args.name, "--paths", str(ROOT / ".deps"),
        "--collect-all", "embit", "--collect-all", "cryptography",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / ".build/linux"),
        "--specpath", str(ROOT / ".build/linux"),
        "--add-data", str(ROOT / "web") + ":web",
        "--add-data", str(ROOT / "resources") + ":resources",
        "--add-data", str(licenses) + ":resources/licenses/linux"]
    for module in ("winpty", "webview", "pythonnet", "clr_loader", "clr", "torch",
                   "numpy", "matplotlib", "tkinter", "PIL", "scipy", "pandas", "pytest"):
        arguments += ["--exclude-module", module]
    # Windows binaries must never be copied into a Linux release.
    run(arguments + [str(ROOT / "main.py")])
    executable = ROOT / "dist" / args.name
    executable.chmod(0o755)
    manifest = {"file": executable.name, "version": VERSION,
        "bytes": executable.stat().st_size, "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "platform": "Linux x64", "build_libc": platform.libc_ver(),
        "python": platform.python_version(), "automatic_broadcast": False,
        "cuda": "optional external native engine; CPU fallback included"}
    (ROOT / "dist/build-manifest-linux.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
