# KeyTrail on Linux

## Portable release (x86_64)

Two packages are available: `KeyTrail-Linux-x64-v1.4.0.tar.gz` (CPU fallback,
optional CUDA installation) and `KeyTrail-Linux-x64-v1.4.0-CUDA.tar.gz`
(native NVIDIA engine plus CUDA 12.6 runtime included). The CUDA package
targets compute capability 7.5+ with forward-compatible PTX. It was tested
on an RTX 4070 Laptop GPU, not every NVIDIA card. A compatible installed
NVIDIA driver is still required; no driver is bundled. CUDA mode also uses
the system C/C++ runtime libraries (Ubuntu package `libstdc++6`).

Target: Ubuntu 22.04 or newer, or another Linux distribution with glibc 2.35+.
Alpine/musl and ARM64 are not covered by this binary. Source mode requires
Python 3.10+; a browser is needed for the graphical interface.

```sh
sha256sum -c SHA256SUMS-Linux-v1.4.0.txt --ignore-missing
tar -xzf KeyTrail-Linux-x64-v1.4.0.tar.gz
cd KeyTrail-Linux-x64-v1.4.0
./KeyTrail
```

The executable opens the same five-language workbench in your default browser.
No WebView2, .NET, Wine, Python, or Node installation is needed for the portable
binary. Linux uses the browser's window controls; there is no Linux tray icon.
Closing a tab does **not** stop computation. Use **Quit** in the page, Ctrl+C,
or SIGTERM for a graceful stop with the last confirmed checkpoint saved.

New installations have an empty receiving address and never start a search or
authorize a payout on launch. The public puzzle target is unchanged.

## Source mode

On Ubuntu/Debian install `python3-venv` using your package manager first.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-linux.txt
bash scripts/run-linux.sh
# Or: .venv/bin/python main.py
```

CPU search works without NVIDIA hardware. Only a single CPU thread is used;
it is a fallback, not comparable to GPU throughput. CUDA detection failure is
reported honestly, with CPU fallback. No AMD/OpenCL support is claimed.

## Headless machine / SSH

```sh
./KeyTrail --no-browser --no-tray --port 8765
# Source equivalent:
# .venv/bin/python main.py --no-browser --no-tray --port 8765
```

On your desktop:

```sh
ssh -L 8765:127.0.0.1:8765 USER@LINUX_HOST
```

Open `http://127.0.0.1:8765` locally. Keep the local and remote port numbers
equal because the server validates its Host and Origin. The service remains
bound to `127.0.0.1`; do not expose it through a public reverse proxy. A fixed
port is optional; the default selects a free local port and prints its URL.

## Data and private results

Default data directory: `$XDG_DATA_HOME/KeyTrail` when XDG_DATA_HOME is absolute,
otherwise `~/.local/share/KeyTrail`. Override with `--data-dir /absolute/path`.
Use a dedicated directory on a local filesystem with working permissions and
flock, not a shared/NFS directory. Separate users keep separate data.

Linux uses a kernel-held exclusive lock to prevent concurrent backends writing
the same SQLite/checkpoint/result files. A second launch reopens the existing
local UI. Stale metadata does not bypass the kernel lock. SIGINT and SIGTERM
stop the engine before releasing the instance lock.

Linux results use **AES-256-GCM**, a new random nonce for each write, and a
32-byte random local `private/result-protection.key` created with mode `0600`.
The private directory is `0700`. The ciphertext is `puzzle71-result.aesgcm`.
Tampering, a missing/wrong key, unsafe key permissions, or a symlink key fails
closed: no plaintext fallback, automatic key replacement, or transaction send.

This key file is **not** an OS keyring or password-based disk encryption. The
same user/root, or someone holding both the key and ciphertext, can decrypt it.
Use an encrypted disk and back up the entire private directory in encrypted
offline storage. Losing the key loses access to the encrypted result. Never
commit/share this directory, a used data directory, or a signed transaction.

Windows DPAPI results still need the original Windows machine/user. Linux
detects copied Windows result files and stops for review rather than silently
starting another search. Config/plan files and checkpoint formats remain
compatible, but private result encryption is platform-specific. Export only
different numbered plan files when coordinating Windows and Linux machines.

## Optional native NVIDIA CUDA engine

The CPU-only portable package does not contain a Windows `.exe`/`.dll` or an
unverified Linux download. Build native CUDA from pinned BitCrack revision
`6bf8059ef075eb1622298395866b0bd02375e1d9` using an installed CUDA toolkit,
`git`, `make`, and `g++`:

```sh
# Run from either a source checkout or the extracted portable package.
python3 scripts/build-linux-cuda.py --compute-cap 75 --cuda-home /usr/local/cuda
# Source:
.venv/bin/python main.py --self-test-cuda --data-dir .test-output/cuda
# Portable:
./KeyTrail --self-test-cuda --data-dir .test-output/cuda
```

Choose a compute capability supported by your GPU/toolkit; for example 89 for
an RTX 4070. The default 75 also embeds forward-compatible PTX. A compatible
NVIDIA driver and CUDA runtime must be installed for an unbundled engine. The script does not install
drivers or modify the system toolkit. An optional `--output` installs the
engine at another explicitly chosen directory. Runtime lookup expects
`vendor/linux-x64/` beside the source root or portable executable.

To build a redistributable engine/runtime pair, install `patchelf` and binutils
in the build environment and add `--bundle-runtime`. It copies only cudart and
the toolkit license, rewrites the engine's runtime lookup to `$ORIGIN`, and
hashes both engine and runtime. It never redistributes the NVIDIA driver.
The packager's `--with-cuda` option includes those explicit allowlisted files.

The tool writes an SHA256 manifest alongside the built engine. Runtime checks
the file, revision and executable permissions before detection **and** launch.
The manifest detects accidental replacement; someone who can replace both it
and the engine has local code-execution privileges already. Trust your build.

The Linux engine uses a POSIX PTY, bounded stop/kill handling, the same inclusive
range interface, and only confirmed progress prefixes. Every GPU search first
reproduces the already-solved public puzzle #20. The fixture is not puzzle #71
and never pays out. Real GPU coverage must be recorded separately from mocked
terminal tests; see `verification/v1.4.0.md` for this build's actual evidence.

## Build, tests and packaging

Build on Linux, not Windows. PyInstaller is not a cross-compiler.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-linux-build.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/smoke-linux.py
.venv/bin/python main.py --diagnostics --self-test-payout --data-dir .test-output/offline
.venv/bin/python scripts/build-linux.py
./dist/KeyTrail --diagnostics --self-test-payout --data-dir .test-output/frozen
.venv/bin/python scripts/smoke-linux.py --binary dist/KeyTrail
.venv/bin/python scripts/privacy-check.py --exe dist/KeyTrail
.venv/bin/python scripts/package-linux.py
# If a verified --bundle-runtime engine was built:
.venv/bin/python scripts/package-linux.py --with-cuda
```

Build on Ubuntu 22.04 for the glibc 2.35 baseline. The GitHub Actions workflow
runs unit tests, native build, frozen smoke checks and allowlist packaging on
Ubuntu 22.04 and 24.04; the 24.04 artifact may require newer glibc. No workflow
run or mainnet payout is claimed merely by adding the workflow file.

Payout self-tests use synthetic outputs and offline callbacks only. Mainnet
broadcast still requires explicit session authorization or manual review;
the 200,000 sat fee cap and ambiguous-submission handling are unchanged.
