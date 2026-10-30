"""Build an independent, portable Windows x64 executable from pinned local deps."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import argparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".deps"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="KeyTrail")
    build_name = parser.parse_args().name
    if not build_name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._" for c in build_name):
        raise ValueError("Invalid build filename")
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (256, 256))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, 255, 255), radius=48, fill="#252b2e")
    draw.line((66, 49, 66, 207), fill="#d2a675", width=22)
    draw.line((194, 49, 116, 128, 194, 207), fill="#faf8f3", width=22, joint="curve")
    image.save(ROOT / "web/icon.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    licenses = ROOT / "resources/licenses"
    licenses.mkdir(exist_ok=True)
    for name in ("embit", "pywinpty", "pyinstaller", "pywebview", "pythonnet", "clr_loader", "bottle", "proxy_tools", "cffi", "pycparser", "typing_extensions"):
        distribution = importlib.metadata.distribution(name)
        for entry in distribution.files or []:
            if "LICENSE" in entry.name.upper() or "COPYING" in entry.name.upper():
                source=Path(distribution.locate_file(entry))
                if source.is_file():
                    shutil.copyfile(source, licenses/(name+"-"+entry.name))
    from PyInstaller.__main__ import run
    arguments=["--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", build_name, "--icon", str(ROOT/"web/icon.ico"),
        "--paths", str(ROOT/".deps"), "--collect-all", "embit", "--collect-all", "winpty",
        "--distpath", str(ROOT/"dist"), "--workpath", str(ROOT/".build"),
        "--specpath", str(ROOT/".build"), "--additional-hooks-dir", str(ROOT/"hooks")]
    arguments += ["--collect-data", "webview", "--collect-binaries", "webview"]
    for module in ("pythonnet", "clr_loader"):
        arguments += ["--collect-all", module]
    arguments += ["--hidden-import", "clr", "--hidden-import", "webview.platforms.winforms", "--hidden-import", "webview.platforms.edgechromium"]
    for folder in ("web", "resources", "vendor"):
        arguments += ["--add-data", str(ROOT/folder)+";"+folder]
    for module in ("torch", "numpy", "matplotlib", "tkinter", "PIL", "scipy", "pandas", "pytest", "cryptography"):
        arguments += ["--exclude-module", module]
    run(arguments+[str(ROOT/"main.py")])
    executable=ROOT/("dist/"+build_name+".exe")
    report={"file":executable.name,"bytes":executable.stat().st_size,
        "sha256":hashlib.sha256(executable.read_bytes()).hexdigest(),
        "platform":"Windows x64", "automatic_broadcast":False}
    (ROOT/"dist/build-manifest.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report),flush=True)


if __name__=="__main__":
    main()
