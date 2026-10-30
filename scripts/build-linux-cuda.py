"""Build optional Linux CUDA support from a pinned BitCrack revision.

Requires Linux x86_64, Python 3.10+, git, make, g++, and an installed CUDA toolkit.
No prebuilt engine is downloaded, no driver is installed, and nothing searches
or broadcasts as part of the build. Follow with --self-test-cuda before use.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BITCRACK_COMMIT = "6bf8059ef075eb1622298395866b0bd02375e1d9"
UPSTREAM = "https://github.com/brichard19/BitCrack.git"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cuda-home", type=Path)
    parser.add_argument("--compute-cap", default="75", help="CUDA compute capability without dot, e.g. 75, 86, 89")
    parser.add_argument("--output", type=Path, default=ROOT / "vendor/linux-x64")
    parser.add_argument("--source", type=Path, help="Optional existing clean checkout at the exact pinned commit")
    parser.add_argument("--bundle-runtime", action="store_true", help="Include redistributable cudart and its license; requires patchelf/readelf")
    args = parser.parse_args()
    if sys.platform != "linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        parser.error("Run this native build on Linux x86_64")
    if not re.fullmatch(r"[0-9]{2,3}", args.compute_cap):
        parser.error("--compute-cap must be two or three digits")
    for name in ("git", "make", "g++"):
        if not shutil.which(name):
            parser.error("Missing build tool: " + name)
    if args.bundle_runtime:
        for name in ("patchelf", "readelf"):
            if not shutil.which(name):
                parser.error("Runtime bundling requires " + name)
    nvcc = shutil.which("nvcc")
    home = args.cuda_home or (Path(os.environ["CUDA_HOME"]) if os.environ.get("CUDA_HOME") else None)
    home = (home or (Path(nvcc).resolve().parents[1] if nvcc else Path("/usr/local/cuda"))).resolve()
    if not (home / "bin/nvcc").is_file():
        parser.error("CUDA Toolkit nvcc missing; install the toolkit or supply --cuda-home")
    # Upstream makefiles use unquoted shell variables; reject ambiguous paths.
    if not re.fullmatch(r"[A-Za-z0-9/_+.-]+", str(home)):
        parser.error("CUDA toolkit path must not contain spaces or shell metacharacters")
    source = args.source.resolve() if args.source else ROOT / ".build" / ("bitcrack-" + BITCRACK_COMMIT)
    if not re.fullmatch(r"[A-Za-z0-9/_+.-]+", str(source)):
        parser.error("Source build path must not contain spaces or shell metacharacters")
    if not source.exists():
        if args.source:
            parser.error("Supplied source checkout does not exist")
        source.mkdir(parents=True)
        subprocess.run(["git", "init", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "fetch", "--depth=1", UPSTREAM, BITCRACK_COMMIT], check=True)
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"], check=True)
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"], text=True)
    # Upstream's clean target deletes this tracked OpenCL input even on CUDA
    # builds. Repair only that known deletion in our own managed checkout.
    cl_input = source / "CLKeySearchDevice/bitcrack.cl"
    if (not args.source and actual == BITCRACK_COMMIT
            and dirty.strip() == "D CLKeySearchDevice/bitcrack.cl"):
        cl_input.write_bytes(subprocess.check_output(["git", "-C", str(source),
                            "show", "HEAD:CLKeySearchDevice/bitcrack.cl"]))
        dirty = ""
    if actual != BITCRACK_COMMIT or dirty:
        raise ValueError("BitCrack source must be clean and match the exact pinned upstream revision")
    cap = args.compute_cap
    nvflags = (f"-std=c++11 -gencode=arch=compute_{cap},code=sm_{cap} "
               f"-gencode=arch=compute_{cap},code=compute_{cap} -Xptxas=-v -Xcompiler=-O2,-std=c++11")
    libdir = home / "lib64" if (home / "lib64").is_dir() else home / "lib"
    if not (libdir / "libcudart.so").is_file():
        parser.error("CUDA runtime development library libcudart.so missing from toolkit")
    # Embed the toolkit runtime lookup path, not a Windows DLL. No driver library
    # is redistributed; users need a compatible installed NVIDIA driver.
    cxxflags = "-O2 -std=c++11 -Wl,-rpath," + str(libdir)
    env = dict(os.environ, PATH=str(home / "bin") + os.pathsep + os.environ.get("PATH", ""))
    # The pinned makefiles do not express complete incremental dependencies.
    # Clean first so a new compute capability cannot silently reuse old objects.
    subprocess.run(["make", "clean"], cwd=source, env=env, check=True)
    if not cl_input.exists():
        cl_input.write_bytes(subprocess.check_output(["git", "-C", str(source),
                            "show", "HEAD:CLKeySearchDevice/bitcrack.cl"]))
    subprocess.run(["make", "BUILD_CUDA=1", "BUILD_OPENCL=0", "CUDA_HOME=" + str(home),
                    "CUDA_LIB=" + str(libdir), "NVCC=" + str(home / "bin/nvcc"), "COMPUTE_CAP=" + cap,
                    "CXXFLAGS=" + cxxflags, "NVCCFLAGS=" + nvflags], cwd=source, env=env, check=True)
    binary = source / "bin/cuBitCrack"
    if binary.read_bytes()[:5] != b"\x7fELF\x02":
        raise ValueError("Build did not produce a native 64-bit ELF engine")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / "cuBitCrack.pending"
    shutil.copyfile(binary, temporary)
    temporary.chmod(0o755)
    runtime = None
    if args.bundle_runtime:
        license_path = next((p for p in (home / "LICENSE", home / "LICENSE.txt",
                            home / "EULA.txt", home / "doc/EULA.txt") if p.is_file()), None)
        if license_path is None:
            raise ValueError("CUDA license not found; provide the toolkit license before redistribution")
        runtime_source = (libdir / "libcudart.so").resolve()
        dynamic = subprocess.check_output(["readelf", "-d", str(runtime_source)], text=True)
        soname = re.search(r"\(SONAME\).*\[(libcudart\.so\.[0-9.]+)\]", dynamic)
        if not soname:
            raise ValueError("CUDA runtime SONAME not recognized")
        runtime_file = output / soname[1]
        shutil.copyfile(runtime_source, runtime_file)
        runtime_file.chmod(0o644)
        shutil.copyfile(license_path, output / "NVIDIA-CUDA-LICENSE.txt")
        subprocess.run(["patchelf", "--set-rpath", "$ORIGIN", str(temporary)], check=True)
        runtime = {"file": runtime_file.name,
                   "sha256": hashlib.sha256(runtime_file.read_bytes()).hexdigest()}
    temporary.replace(output / "cuBitCrack")
    manifest = {"schema": 1, "platform": "Linux x64", "upstream": UPSTREAM,
        "upstream_commit": BITCRACK_COMMIT, "compute_capability": cap,
        "sha256": hashlib.sha256((output / "cuBitCrack").read_bytes()).hexdigest()}
    if runtime:
        manifest["runtime"] = runtime
    temporary = output / "engine-manifest.pending"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf8")
    temporary.replace(output / "engine-manifest.json")
    print(json.dumps(manifest, indent=2))
    print("Installed native engine at " + str(output))
    print("Next: run KeyTrail --self-test-cuda before searching.")


if __name__ == "__main__":
    main()
