"""Platform-specific, integrity-checked CUDA engine selection."""
import json
import os
from pathlib import Path
import platform
import re
import sys

from .core import ROOT, TOOL_HASH, RUNTIME_HASH, sha256

BITCRACK_COMMIT = "6bf8059ef075eb1622298395866b0bd02375e1d9"


def linux_vendor_directory():
    # Frozen releases use an external, explicitly installed native engine. It
    # can be built later without editing the self-contained Python executable.
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
    return base / "vendor/linux-x64"


def engine_path():
    if os.name == "nt":
        tool, runtime = ROOT / "vendor/cuBitCrack.exe", ROOT / "vendor/cudart64_101.dll"
        if not tool.is_file() or not runtime.is_file() or sha256(tool) != TOOL_HASH or sha256(runtime) != RUNTIME_HASH:
            raise RuntimeError("CUDA engine missing or integrity check failed")
        return tool
    if not sys.platform.startswith("linux") or platform.machine().lower() not in ("x86_64", "amd64"):
        raise RuntimeError("Native CUDA engine supports Linux x86_64; CPU mode is available")
    folder = linux_vendor_directory()
    tool, manifest_path = folder / "cuBitCrack", folder / "engine-manifest.json"
    if not tool.is_file() or not manifest_path.is_file():
        raise RuntimeError("Linux CUDA engine not installed; use CPU or run scripts/build-linux-cuda.py")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf8"))
        valid = (manifest.get("schema") == 1 and manifest.get("platform") == "Linux x64"
                 and manifest.get("upstream_commit") == BITCRACK_COMMIT
                 and manifest.get("sha256") == sha256(tool))
    except (OSError, ValueError, AttributeError) as error:
        raise RuntimeError("Linux CUDA engine manifest is invalid") from error
    if not valid or not os.access(tool, os.X_OK):
        raise RuntimeError("Linux CUDA engine hash, upstream revision, or executable permission mismatch")
    runtime = manifest.get("runtime")
    if runtime is not None:
        if (not isinstance(runtime, dict) or not isinstance(runtime.get("file"), str)
                or not re.fullmatch(r"libcudart\.so\.[0-9.]+", runtime["file"])):
            raise RuntimeError("Invalid bundled CUDA runtime filename")
        library = folder / runtime["file"]
        if not library.is_file() or runtime.get("sha256") != sha256(library):
            raise RuntimeError("Bundled CUDA runtime integrity check failed")
    return tool
