# Third-party components

| Component | Role | Included notices |
| --- | --- | --- |
| BitCrack / cuBitCrack | NVIDIA CUDA bounded-search engine | resources/licenses/BitCrack-MIT.txt |
| NVIDIA CUDA 10.1 runtime | Redistributed CUDA runtime | resources/licenses/NVIDIA-CUDA-10.1-EULA.html |
| embit 0.8.0 | Bitcoin data and signatures | resources/licenses/embit-LICENSE |
| pywinpty 2.0.15 | Windows process terminal | resources/licenses/pywinpty-LICENSE.txt |
| PyInstaller 6.12.0 | Portable executable packaging | resources/licenses/pyinstaller-COPYING.txt |

The first-party repository does not change upstream licensing. Python/PyInstaller and package notices are also preserved within the frozen executable as supplied by the dependencies. Pillow 11.3.0 is build-time-only and is excluded from the frozen program.

Pinned binary SHA256 (also enforced by the engine at runtime):

```
0eecdbfabaaef36f497e944a6ceb468d01824f3ae6457b4ae4b3ac8e95eebb55  vendor/cuBitCrack.exe
111f216aef35f45086888c3f0a30bb9ab48e2b333daeddafd3a76be037a22a6e  vendor/cudart64_101.dll
```

## Native window (v1.3.0)

pywebview 6.2.1, pythonnet 3.2.1, clr_loader 0.3.1, bottle 0.13.4, proxy_tools 0.1.0, cffi 2.1.1, pycparser 3.12 and typing_extensions 4.16.0 provide the embedded window and dependencies. Available upstream license files are included under resources/licenses; pywebview also bundles Microsoft WebView2 loader/managed assemblies and their package notices. The Microsoft Edge WebView2 Runtime itself is a separately installed system prerequisite, not bundled in this ZIP.
