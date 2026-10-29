"""Package only distributable files; never copy local application data."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".deps")]
from app.core import VERSION


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default="KeyTrail.exe")
    args = parser.parse_args()
    if Path(args.exe).name != args.exe:
        raise ValueError("Supply an EXE filename within dist")
    exe = ROOT / "dist" / args.exe
    manifest = json.loads((ROOT / "dist/build-manifest.json").read_text())
    assert sha256(exe) == manifest["sha256"], "Build manifest mismatch"
    archive = ROOT / "dist" / f"KeyTrail-Windows-x64-v{VERSION}.zip"
    quick_start = """KeyTrail / 钥迹

1. Extract all files into a writable folder and open KeyTrail.exe.
2. New installations default to English; change language at the top right.
3. Enter your own Bitcoin receiving address in Results, then choose Ranges.
   New installations have no prefilled wallet. Search never starts on launch.
4. Stop & save preserves the last confirmed checkpoint. Use Quit to exit.
5. For multiple computers, import a DIFFERENT numbered plan on each one.

Automatic payout is off until explicitly authorized for the current session.
No seed phrase is needed. No real-BTC payout is promised or used in testing.
The embedded window requires Microsoft Edge WebView2 Runtime and .NET Framework.
Drag the blank top header area to move; double-click it to maximize/restore.
Scrollbars are hidden, but wheel, touchpad and keyboard scrolling still work.
Keep the legacy Puzzle71Desk-data folder when upgrading an existing install.
Read README.md for compatibility, private-result backup and broadcast risks.
"""
    with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=9) as z:
        z.write(exe, "KeyTrail.exe")
        for name in ("README.md", "THIRD_PARTY.md", f"verification/v{VERSION}.md"):
            z.write(ROOT / name, name)
        for path in sorted((ROOT / "resources/licenses").iterdir()):
            if path.is_file():
                z.write(path, str(path.relative_to(ROOT)).replace("\\", "/"))
        z.writestr("QUICK-START.txt", quick_start)
        z.writestr("SHA256SUMS.txt", sha256(exe) + "  KeyTrail.exe\n")
    sums = ROOT / "dist" / f"SHA256SUMS-v{VERSION}.txt"
    sums.write_text(sha256(exe) + "  KeyTrail.exe\n" +
                    sha256(archive) + "  " + archive.name + "\n", encoding="utf8")
    print(json.dumps({"version": VERSION, "archive": archive.name,
                      "bytes": archive.stat().st_size,
                      "sha256": sha256(archive), "executable_sha256": sha256(exe)}))


if __name__ == "__main__":
    main()
