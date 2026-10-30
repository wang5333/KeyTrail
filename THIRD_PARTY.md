# Third-party components

| Component | Role | Included notices |
| --- | --- | --- |
| BitCrack / cuBitCrack | NVIDIA CUDA bounded-search engine | resources/licenses/BitCrack-MIT.txt |
| NVIDIA CUDA 10.1 runtime | Redistributed CUDA runtime | resources/licenses/NVIDIA-CUDA-10.1-EULA.html |
| embit 0.8.0 | Bitcoin data and signatures | resources/licenses/embit-LICENSE |
| pywinpty 2.0.15 | Windows process terminal | resources/licenses/pywinpty-LICENSE.txt |
| PyInstaller 6.12.0 | Portable executable packaging | resources/licenses/pyinstaller-COPYING.txt |

The first-party repository does not change upstream licensing. Python/PyInstaller and package notices are also preserved within the frozen executable as supplied by the dependencies. Pillow 11.3.0 is build-time-only and is excluded from the frozen program.

## Linux (v1.4.0)

The Linux build uses embit 0.8.0, PyInstaller 6.12.0 and cryptography 46.0.3
(Apache-2.0 OR BSD-3-Clause), with cffi/pycparser as required by the dependency
resolver. `scripts/build-linux.py` collects installed distribution license files
into the executable and portable archive. Python/OpenSSL notices shipped by
those runtimes remain applicable. No Windows PTY/WebView2/.NET binaries or CUDA
10.1 DLL are included in the Linux executable.

The optional native CUDA engine is built from BitCrack MIT revision
`6bf8059ef075eb1622298395866b0bd02375e1d9`, not from an unverified binary URL.
The CPU-only archive does not redistribute the Linux CUDA runtime; building
the engine requires an installed NVIDIA CUDA toolkit and compatible driver.
The separate `-CUDA.tar.gz` package includes the native BitCrack engine and
the redistributable CUDA 12.6 `libcudart.so.12`, with the toolkit license at
`vendor/linux-x64/NVIDIA-CUDA-LICENSE.txt` and hashes in the engine manifest.
It does not contain the CUDA compiler or NVIDIA driver.

Pinned binary SHA256 (also enforced by the engine at runtime):

```
0eecdbfabaaef36f497e944a6ceb468d01824f3ae6457b4ae4b3ac8e95eebb55  vendor/cuBitCrack.exe
111f216aef35f45086888c3f0a30bb9ab48e2b333daeddafd3a76be037a22a6e  vendor/cudart64_101.dll
```

## Native window (v1.3.0)

pywebview 6.2.1, pythonnet 3.2.1, clr_loader 0.3.1, bottle 0.13.4, proxy_tools 0.1.0, cffi 2.1.1, pycparser 3.12 and typing_extensions 4.16.0 provide the embedded window and dependencies. Available upstream license files are included under resources/licenses; pywebview also bundles Microsoft WebView2 loader/managed assemblies and their package notices. The Microsoft Edge WebView2 Runtime itself is a separately installed system prerequisite, not bundled in this ZIP.
