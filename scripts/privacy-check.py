"""Verify blank defaults and optionally exclude a local address from source/EXE.

The optional config stays on disk locally. Its address is never logged.
"""
import argparse
import json
import marshal
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".deps")]
from app.core import PAYOUT, make_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-config", type=Path)
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    assert PAYOUT == "" and make_config()["payout"] == "", "Default must be empty"
    private = ""
    if args.local_config:
        private = json.loads(args.local_config.read_text(encoding="utf8"))["payout"]
        assert private, "The local exclusion fixture must contain an address"
    needles = [private.encode("utf8"), private.encode("utf-16le")] if private else []
    failures = []

    def check(name, data):
        if any(n in data for n in needles):
            failures.append(name)

    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                           capture_output=True, check=True).stdout.decode().split("\0")
    for name in filter(None, files):
        assert not any(x in name.split("/") for x in
                       ("evidence", "backups", "dist", ".local-data", ".deps")), name
        check(name, (ROOT / name).read_bytes())
    if args.exe:
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(args.exe.resolve()))
        pyz_name = next(name for name in archive.toc if name.lower().endswith(".pyz"))
        pyz = archive.open_embedded_archive(pyz_name)
        for name in pyz.toc:
            if name == "app" or name.startswith("app."):
                check("embedded:" + name, marshal.dumps(pyz.extract(name)))
        check("embedded:main", archive.extract("main"))
        for name in archive.toc:
            if name.replace("\\", "/").startswith(("web/", "resources/")):
                check("embedded:" + name, archive.extract(name))
        code = pyz.extract("app.core")
        scope = {"__file__": str(ROOT / "app/core.py"), "__name__": "embedded_core"}
        exec(code, scope)
        assert scope["PAYOUT"] == "", "Embedded default must be empty"
    assert not failures, "Private address found in: " + ", ".join(failures)
    print(json.dumps({"pass": True, "default_empty": True,
                      "local_address_exclusion_checked": bool(private),
                      "compiled_executable_checked": args.exe is not None}))


if __name__ == "__main__":
    main()
