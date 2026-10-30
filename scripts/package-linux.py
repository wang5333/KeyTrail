"""Allowlist-only Linux release packaging. Never include private local state."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".deps")]
from app.core import VERSION


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default="KeyTrail")
    parser.add_argument("--with-cuda", action="store_true", help="Include the verified native engine and bundled runtime")
    args = parser.parse_args()
    if Path(args.binary).name != args.binary:
        parser.error("Supply a binary filename within dist")
    executable = ROOT / "dist" / args.binary
    manifest = json.loads((ROOT / "dist/build-manifest-linux.json").read_text(encoding="utf8"))
    if (manifest.get("file") != executable.name or manifest.get("version") != VERSION
            or manifest.get("platform") != "Linux x64" or digest(executable) != manifest.get("sha256")):
        raise ValueError("Linux build manifest mismatch")
    if executable.read_bytes()[:5] != b"\x7fELF\x02":
        raise ValueError("Expected a native 64-bit Linux ELF executable")
    prefix = f"KeyTrail-Linux-x64-v{VERSION}"
    cuda_folder = None
    cuda_manifest = None
    if args.with_cuda:
        from app.cuda import engine_path
        cuda_folder = engine_path().parent
        cuda_manifest = json.loads((cuda_folder / "engine-manifest.json").read_text(encoding="utf8"))
        if not cuda_manifest.get("runtime") or not (cuda_folder / "NVIDIA-CUDA-LICENSE.txt").is_file():
            raise ValueError("CUDA releases require --bundle-runtime plus the runtime license")
        prefix += "-CUDA"
    archive = ROOT / "dist" / (prefix + ".tar.gz")
    quick_start = """KeyTrail for Linux x86_64 (Ubuntu 22.04+ / glibc 2.35+)

1. Extract the archive. Run ./KeyTrail (no Python or Node required).
2. The local UI opens in your default browser. A running graphical desktop
   and browser are needed only for the UI, not for --no-browser operation.
3. Enter your own Bitcoin receiving address; import a distinct numbered plan.
4. Search never starts on launch. Automatic payout is off by default.
5. Quit in the page or Ctrl+C / SIGTERM stops and saves confirmed progress.
6. Data: $XDG_DATA_HOME/KeyTrail or ~/.local/share/KeyTrail.
   --data-dir overrides this. Do not use a shared/NFS data directory.

For a headless machine: ./KeyTrail --no-browser --no-tray --port 8765
From your desktop: ssh -L 8765:127.0.0.1:8765 USER@LINUX_HOST
Then open http://127.0.0.1:8765 on your desktop. No public bind is needed.

CPU works out of the box. Native NVIDIA CUDA is optional and built from
pinned source using scripts/build-linux-cuda.py; see LINUX.md for details.
Windows .exe/.dll engines do not run on Linux. No AMD/OpenCL claims are made.

Linux private results use AES-256-GCM with a local owner-only (0600) key.
This is not a system keyring. Back up the whole private directory encrypted.
Never distribute it. Windows DPAPI results need the original Windows account.
Read README.md and LINUX.md for limitations and public-broadcast risks.
No real BTC has been transferred as part of development or testing.
"""
    libc = manifest.get("build_libc", ["glibc", "2.35"])
    quick_start = quick_start.replace("Ubuntu 22.04+ / glibc 2.35+", "build " + " ".join(libc) + "; check distro compatibility")
    if args.with_cuda:
        quick_start = quick_start.replace("CPU works out of the box. Native NVIDIA CUDA is optional and built from",
            "Native CUDA engine/runtime included (compute 7.5+, forward PTX).\n"
            "A compatible NVIDIA driver is required; CPU fallback is available.\n"
            "For a custom CUDA rebuild, use pinned source from")
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        def add_file(path, name, mode=0o644):
            path = Path(path)
            if path.is_symlink() or not path.is_file():
                raise ValueError("Only regular distributable files are allowed")
            data = path.read_bytes()
            add_bytes(data, name, mode)

        def add_bytes(data, name, mode=0o644):
            info = tarfile.TarInfo(prefix + "/" + name)
            info.size, info.mode, info.mtime = len(data), mode, 0
            tar.addfile(info, io.BytesIO(data))

        add_file(executable, "KeyTrail", 0o755)
        for name in ("README.md", "LINUX.md", "THIRD_PARTY.md", "requirements-linux.txt",
                     f"verification/v{VERSION}.md", "scripts/build-linux-cuda.py"):
            add_file(ROOT / name, name)
        for base in (ROOT / "resources/licenses", ROOT / ".build/linux-licenses"):
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    name = ("resources/licenses/" if base.name == "licenses" else "resources/licenses/linux/")
                    add_file(path, name + path.relative_to(base).as_posix())
        add_bytes(quick_start.encode("utf8"), "QUICK-START.txt")
        add_bytes((digest(executable) + "  KeyTrail\n").encode(), "SHA256SUMS.txt")
        add_file(ROOT / "dist/build-manifest-linux.json", "build-manifest-linux.json")
        if cuda_folder:
            for name in ("cuBitCrack", "engine-manifest.json", "NVIDIA-CUDA-LICENSE.txt",
                         cuda_manifest["runtime"]["file"]):
                add_file(cuda_folder / name, "vendor/linux-x64/" + name,
                         0o755 if name == "cuBitCrack" else 0o644)
    sums = ROOT / "dist" / (f"SHA256SUMS-Linux-v{VERSION}" + ("-CUDA" if args.with_cuda else "") + ".txt")
    sums.write_text(digest(executable) + "  KeyTrail\n" + digest(archive) + "  " + archive.name + "\n", encoding="utf8")
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size,
                      "sha256": digest(archive)}))


if __name__ == "__main__":
    main()
