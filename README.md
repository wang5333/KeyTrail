# KeyTrail · 钥迹

A portable Windows x64 workbench for the public **Bitcoin Puzzle #71**. Graphite header, warm-white workspace, live device throughput, explicit run state. English by default; English / 简体中文 / 繁體中文 / 日本語 / Español are available from the top-right selector.

## Download and run

Download the Windows x64 ZIP from **[Releases](https://github.com/wang5333/KeyTrail/releases)**, compare its SHA256 with `SHA256SUMS.txt`, extract it to a writable folder, and open `KeyTrail.exe`. Python and Node.js are not required. NVIDIA support requires a compatible installed driver. The frameless UI requires Microsoft Edge WebView2 Runtime and .NET Framework (normally already present on supported Windows installations). Builds are not code-signed.

Nothing starts searching on launch. Automatic payout is off until explicitly authorized for the current session.

## Features

- Fixed public challenge #71; an inclusive hexadecimal subrange, four-quarter presets, or resume from a saved checkpoint.
- Saved searches and **local unique keyspace coverage**. Overlapping local search prefixes count once; this is not an aggregate across all computers.
- Numbered, disjoint plans for computers on different networks. No central service or inbound network port is needed.
- NVIDIA CUDA on the first compatible GPU; single-thread CPU fallback. Actual throughput, temperature, run time and stop/save controls. Temperature protection at 82°C when telemetry is available.
- Public prize-output monitoring approximately every 30 seconds through Blockstream and mempool.space. Pending spends, confirmed spends, network errors and dust are distinguished. A secret found offline is not observable until a transaction appears.
- Approximate USD prize value, refreshed about every five minutes, with unavailable/stale states. It does not influence fees or computation.
- Editable Bitcoin **mainnet** receiving address, including Taproot and SegWit. No wallet seed phrase or receiving-wallet private key is requested.

## Use / 使用

1. On **Ranges / 区间**, choose the desired bounded range and number of computers. Export one plan and import a different `computer-NNN.json` on each machine.
2. On **Results / 结果**, verify or change the receiving address. New installations have no prefilled receiving address. Enter your own address locally; it is not embedded in the software or uploaded to this repository.
3. On **Search / 搜索**, check the device, assigned range and public-prize state, then choose a duration and start. Stop & save preserves the last confirmed prefix.
4. Closing the browser window leaves the backend running. Use the notification-area menu or the page's Quit button to exit.

Distribute the EXE and **different numbered plan files**, not an already-used data directory. Stop all machines before changing a multi-computer plan. These are static allocations, not a synchronized search pool; using the same plan number on two machines duplicates work.

## Payout behavior

A match is independently verified and saved with Windows current-user DPAPI. The original outputs, destination, amount and signatures are checked before building a transaction. DPAPI files depend on the original Windows user and machine; copying them is not a recovery procedure.

Default mode is manual. Automatic submission requires the full receiving address, a public-broadcast risk acknowledgment and an explicit Save. The grant is bound to that address and this process; restart, plan import or switching plans clears it. Existing saved results are not automatically sent on launch. The fee ceiling is **200,000 sat**.

A returned transaction ID is only **submitted**, not confirmed. An ambiguous response is saved and checked rather than blindly retried. Public submission exposes the puzzle public key and can invite competing spends; there is no private miner relay integration and no guarantee of claiming a prize or final mainnet receipt.

**Testing used offline fictional outputs and Bitcoin Core regtest coins only. No real BTC or puzzle prize was transferred during development.** Regtest tests cover Taproot, SegWit, legacy and a lost response after node acceptance. This is not a mainnet payout guarantee.

## Data and upgrades

The legacy internal ID and data-folder names are intentionally unchanged: `Puzzle71Desk-data` next to the EXE, with `%LOCALAPPDATA%/Puzzle71Desk` fallback. Source runs default to `.local-data`; `--data-dir` overrides it. Keep the existing folder when upgrading. Back up private results and data separately; never upload them to the repository.

v1.3.0 removes the system title bar and uses an embedded WebView2 window with integrated minimize/maximize/close controls. Drag the blank graphite header area to move it; double-click there to maximize/restore. Closing the window keeps the backend in the tray, as before. New installations have an empty receiving address and require one before search; existing local addresses, checkpoints and schema-1 plans remain compatible. Addresses are local configuration, never a compiled-in default. Already-saved language preferences remain; new installations default to English.

## Develop and build

Windows 10/11, Python 3.12 x64. The pinned CUDA engine and runtime are in `vendor/`, with licenses in `resources/licenses/`.

```powershell
python -m pip install --target .deps -r requirements-build.txt
$env:PYTHONPATH='.deps'
python -m unittest discover -s tests -v
python scripts/build.py
```

The output is `dist/KeyTrail.exe`. For a source-mode UI: `python main.py`. Final EXE checks:

```powershell
.\dist\KeyTrail.exe --diagnostics --data-dir .test-output/diagnostics
.\dist\KeyTrail.exe --self-test-cuda --data-dir .test-output/cuda
.\dist\KeyTrail.exe --self-test-payout --data-dir .test-output/payout
```

CUDA self-test reproduces solved puzzle #20, not #71. Payout self-test uses offline fictional outputs. The 43 unit tests exercise checkpoint recovery, range partitions, host/origin protections, receipt handling and explicit payout authorization. For isolated node integration testing, put Bitcoin Core 32.1 at `.test-tools/bitcoin-core-32.1/bitcoin-32.1/bin/bitcoind.exe` then run `python scripts/check-regtest.py`; it creates a regtest node with no peer connections and stops it afterward.

See [DESIGN.md](DESIGN.md), [PRODUCT.md](PRODUCT.md) and [verification/v1.3.0.md](verification/v1.3.0.md). Validation on this development machine is not a claim of testing every Windows computer or GPU.

## Upstream and notices

- [Original puzzle discussion](https://bitcointalk.org/index.php?topic=1306983.0)
- [BitCrack](https://github.com/brichard19/BitCrack) — MIT CUDA engine; exact file hashes are checked at runtime.
- [embit](https://github.com/diybitcoinhardware/embit) — Bitcoin serialization and signing.
- [Impeccable](https://github.com/pbakaus/impeccable) — design guidance read and applied to this workbench, not a claim of using its detector or copying its website.

Third-party license notices accompany the portable ZIP and are embedded in the EXE. CUDA runtime terms are included separately. No license grant for first-party code is declared in this private repository.
