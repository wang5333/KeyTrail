"""End-to-end startup/HTTP/instance/restart/signal checks; never starts a search."""
import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(url, path, token=None, body=None, **headers):
    if token:
        headers["X-Local-Token"] = token
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    with OPENER.open(urllib.request.Request(url + path, data=data, headers=headers), timeout=4) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run this integration check on Linux")
    command = [str(args.binary.resolve())] if args.binary else [sys.executable, str(ROOT / "main.py")]
    output = ROOT / ".test-output"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="linux-smoke-", dir=output) as temporary:
        directory = Path(temporary)
        data = directory / "data"
        log_path = directory / "process.log"
        base = command + ["--no-browser", "--no-tray", "--data-dir", str(data)]
        process = None

        def start():
            nonlocal process
            (data / "instance.json").unlink(missing_ok=True)
            stream = log_path.open("ab")
            try:
                process = subprocess.Popen(base, cwd=directory, stdout=stream, stderr=subprocess.STDOUT)
            finally:
                stream.close()
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Startup failed: " + log_path.read_text(errors="replace"))
                try:
                    instance = json.loads((data / "instance.json").read_text())
                    # A PyInstaller onefile launcher has a separate bootloader
                    # parent PID; the actual backend legitimately has a child PID.
                    if instance.get("app") == "puzzle71-desk":
                        url = instance["url"]
                        bootstrap = request(url, "/api/bootstrap")
                        return url, bootstrap
                except (OSError, ValueError, KeyError):
                    pass
                time.sleep(.1)
            raise TimeoutError("Local UI did not become ready: " + log_path.read_text(errors="replace"))

        try:
            url, bootstrap = start()
            state = bootstrap["state"]
            assert not state["running"] and state["payout"] == ""
            assert not state["claim"]["auto_armed"]
            assert url.startswith("http://127.0.0.1:")
            with OPENER.open(url + "/", timeout=4) as page:
                assert b"KeyTrail" in page.read()
            for path, headers in (("/api/state", {}), ("/api/bootstrap", {"Origin": "https://example.com"}),
                                  ("/api/bootstrap", {"Host": "evil.example"})):
                try:
                    request(url, path, **headers)
                except urllib.error.HTTPError as error:
                    assert error.code == 403
                else:
                    raise AssertionError("Local security boundary was not enforced")
            second = subprocess.run(base, capture_output=True, text=True, timeout=15)
            assert second.returncode == 0, second.stderr
            assert "KEYTRAIL_ALREADY_RUNNING" in second.stdout
            assert process.poll() is None
            request(url, "/api/quit", bootstrap["token"], {})
            assert process.wait(timeout=15) == 0

            # Synthetic checkpoint only: no keys searched, no network payout.
            with sqlite3.connect(data / "progress.sqlite3") as db:
                profile, start_hex = db.execute("SELECT profile,start_hex FROM progress").fetchone()
                db.execute("UPDATE progress SET next_hex=?,checked=? WHERE profile=?",
                           (hex(int(start_hex, 16) + 1234), "1234", profile))
            url, bootstrap = start()
            assert bootstrap["state"]["progress"]["checked"] == "1234"
            assert bootstrap["state"]["run_state"] == "PAUSED"
            assert not bootstrap["state"]["running"]
            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=15) == 0
            assert (data / "instance.lock").exists()
            diagnostic = subprocess.run(command + ["--diagnostics", "--self-test-payout", "--data-dir", str(data)],
                                        capture_output=True, text=True, timeout=30)
            assert diagnostic.returncode == 0, diagnostic.stderr
            report = json.loads((data / "diagnostics.json").read_text())
            assert report["offline_payout_selftest"]["pass"]
            assert report["offline_payout_selftest"]["real_network_broadcasts"] == 0
            assert report["platform"] == "Linux"
            print(json.dumps({"pass": True, "binary": bool(args.binary), "loopback_http": True,
                "host_origin_token_checked": True, "single_instance": True, "quit": True,
                "checkpoint_restart": True, "sigterm": True, "offline_payout": True,
                "searches_started": 0, "real_btc_transferred": 0}, indent=2))
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
