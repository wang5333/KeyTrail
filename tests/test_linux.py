"""Linux behavior, native process adapters, and cross-platform regressions."""
import hashlib
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import tarfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".deps")]
from app.core import APP_ID, VERSION, point_address
from app.cuda import BITCRACK_COMMIT, engine_path
from app.engine import detect_gpu, gpu_scan
from app.protection import MAGIC, protect, result_path
from app.runtime import InstanceLock, default_data_directory
from app.service import Application
from app.window import open_window
from test_suite import FakeNetwork, TEST_OUTPUT

LINUX = sys.platform == "linux"


class BrowserTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Linux browser adapter")
    def test_linux_opens_browser_not_windows_subprocess(self):
        with patch("app.window.webbrowser.open", return_value=True) as browser, patch("app.window.subprocess.Popen") as popen:
            self.assertTrue(open_window("http://127.0.0.1:12345", TEST_OUTPUT))
            browser.assert_called_once_with("http://127.0.0.1:12345", new=2)
            popen.assert_not_called()

    def test_external_ui_url_rejected_before_browser(self):
        with patch("app.window.webbrowser.open") as browser:
            with self.assertRaises(ValueError):
                open_window("https://example.com", TEST_OUTPUT)
            browser.assert_not_called()


class InstanceTests(unittest.TestCase):
    def test_lock_excludes_second_owner_and_releases(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            first, second = InstanceLock(directory), InstanceLock(directory)
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                first.close()
                self.assertTrue(second.acquire())
            finally:
                first.close()
                second.close()

    def test_lock_file_is_not_unlinked(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            lock = InstanceLock(directory)
            self.assertTrue(lock.acquire())
            lock.close()
            self.assertTrue(lock.path.is_file())

    @unittest.skipUnless(LINUX, "XDG paths")
    def test_linux_uses_xdg_even_when_frozen(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/keytrail-xdg"}), patch("sys.frozen", True, create=True):
            self.assertEqual(default_data_directory(ROOT), Path("/tmp/keytrail-xdg/KeyTrail"))

    @unittest.skipUnless(LINUX, "XDG paths")
    def test_relative_xdg_is_ignored(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "relative"}):
            self.assertEqual(default_data_directory(ROOT), Path.home() / ".local/share/KeyTrail")

    def test_windows_has_no_linux_crypto_requirement(self):
        # The platform-specific dependency stays lazy; Windows remains DPAPI.
        text = (ROOT / "requirements.txt").read_text()
        self.assertNotIn("cryptography", text)


@unittest.skipUnless(LINUX, "Linux private result protection")
class LinuxProtectionTests(unittest.TestCase):
    def test_roundtrip_random_nonces_and_permissions(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            secret = bytes(range(32))
            first = protect(secret, directory=directory)
            second = protect(secret, directory=directory)
            self.assertTrue(first.startswith(MAGIC))
            self.assertNotEqual(first, second)
            self.assertNotIn(secret, first)
            self.assertEqual(protect(first, True, directory=directory), secret)
            self.assertEqual(Path(directory).stat().st_mode & 0o777, 0o700)
            self.assertEqual((Path(directory) / "result-protection.key").stat().st_mode & 0o777, 0o600)

    def test_tampered_ciphertext_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            data = protect(b"fixture", directory=directory)
            with self.assertRaisesRegex(ValueError, "authentication"):
                protect(data[:-1] + bytes([data[-1] ^ 1]), True, directory=directory)

    def test_missing_key_is_not_replaced_on_decrypt(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as a, tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as b:
            data = protect(b"fixture", directory=a)
            with self.assertRaisesRegex(RuntimeError, "key is missing"):
                protect(data, True, directory=b)
            self.assertFalse((Path(b) / "result-protection.key").exists())

    def test_wrong_key_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as a, tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as b:
            data = protect(b"fixture", directory=a)
            protect(b"other", directory=b)
            with self.assertRaisesRegex(ValueError, "authentication"):
                protect(data, True, directory=b)

    def test_world_readable_key_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            protect(b"fixture", directory=directory)
            (Path(directory) / "result-protection.key").chmod(0o644)
            with self.assertRaises(PermissionError):
                protect(b"fixture", directory=directory)

    def test_symlink_key_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            destination = Path(directory) / "elsewhere"
            destination.write_bytes(b"x" * 32)
            destination.chmod(0o600)
            (Path(directory) / "result-protection.key").symlink_to(destination)
            with self.assertRaises(OSError):
                protect(b"fixture", directory=directory)

    def test_unknown_blob_and_windows_dpapi_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            for data in (b"", b"Windows DPAPI blob", MAGIC + b"short"):
                with self.assertRaises(ValueError):
                    protect(data, True, directory=directory)

    def test_linux_uses_separate_result_filename(self):
        self.assertEqual(result_path(TEST_OUTPUT).name, "puzzle71-result.aesgcm")

    def test_foreign_windows_result_stops_search_without_modification(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            private = Path(directory) / "private"
            private.mkdir()
            path = private / "puzzle71-result.dpapi"
            path.write_bytes(b"foreign-result")
            app = Application(directory, FakeNetwork(), monitor_enabled=False)
            try:
                self.assertEqual(app.run_state, "FOUND")
                self.assertEqual(app.claim.snapshot()["state"], "NEEDS_REVIEW")
                self.assertEqual(path.read_bytes(), b"foreign-result")
                self.assertFalse(app.is_running())
            finally:
                app.shutdown()
                app.store.db.close()


class DetectionTests(unittest.TestCase):
    def test_gpu_detected_without_nvidia_smi_telemetry(self):
        result = subprocess.CompletedProcess([], 0, stdout="ID: 0\nFixture CUDA GPU\n", stderr="")
        with patch("app.engine.engine_path", return_value=Path("cuBitCrack")), patch("app.engine.subprocess.run", return_value=result), patch("app.engine.hardware", return_value={"available": False}):
            self.assertTrue(detect_gpu()["available"])

    def test_integrity_failure_uses_cpu_with_reason(self):
        with patch("app.engine.engine_path", side_effect=RuntimeError("hash mismatch")):
            value = detect_gpu()
            self.assertFalse(value["available"])
            self.assertIn("hash mismatch", value["reason"])

    @unittest.skipUnless(LINUX and platform.machine() == "x86_64", "native Linux runtime manifest")
    def test_bundled_cuda_runtime_hash_is_checked(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            folder = Path(directory)
            binary = folder / "cuBitCrack"
            binary.write_bytes(b"fixture")
            binary.chmod(0o755)
            runtime = folder / "libcudart.so.12"
            runtime.write_bytes(b"fixture-runtime")
            manifest = {"schema": 1, "platform": "Linux x64", "upstream_commit": BITCRACK_COMMIT,
                "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "runtime": {"file": runtime.name, "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest()}}
            path = folder / "engine-manifest.json"
            path.write_text(json.dumps(manifest))
            with patch("app.cuda.linux_vendor_directory", return_value=folder):
                self.assertEqual(engine_path(), binary)
                runtime.write_bytes(b"modified")
                with self.assertRaisesRegex(RuntimeError, "integrity"):
                    engine_path()
                manifest["runtime"]["file"] = "../escape"
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(RuntimeError, "filename"):
                    engine_path()

    @unittest.skipUnless(LINUX and platform.machine() == "x86_64", "native Linux engine selection")
    def test_linux_engine_manifest_is_checked(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            folder = Path(directory)
            binary = folder / "cuBitCrack"
            binary.write_bytes(b"fixture-engine")
            binary.chmod(0o755)
            manifest = {"schema": 1, "platform": "Linux x64", "upstream_commit": BITCRACK_COMMIT,
                        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
            (folder / "engine-manifest.json").write_text(json.dumps(manifest))
            with patch("app.cuda.linux_vendor_directory", return_value=folder):
                self.assertEqual(engine_path(), binary)
                binary.write_bytes(b"modified")
                with self.assertRaisesRegex(RuntimeError, "mismatch"):
                    engine_path()


@unittest.skipUnless(LINUX, "real POSIX PTY subprocess tests (synthetic CUDA output)")
class PosixScanTests(unittest.TestCase):
    def run_fixture(self, script, should_stop=lambda: False, start=1, end=10):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            tool = Path(directory) / "fake-cuda"
            tool.write_text("#!" + sys.executable + "\n" + script, encoding="utf8")
            tool.chmod(0o755)
            updates = []
            with patch("app.engine.engine_path", return_value=tool):
                result = gpu_scan(start, end, point_address(17), directory, should_stop,
                    lambda cursor, rate: updates.append((cursor, rate)), lambda *_: None)
            return result, updates

    def test_successful_end_marks_complete(self):
        result, updates = self.run_fixture("print('0.01 MKey/s (10 total)', flush=True)\nprint('Reached end of keyspace', flush=True)\n")
        self.assertEqual(result["next"], 11)
        self.assertIsNone(result["found"])
        self.assertEqual(updates[-1][0], 11)

    def test_crash_does_not_mark_complete(self):
        with self.assertRaisesRegex(RuntimeError, "CUDA"):
            self.run_fixture("import sys\nprint('Reached end of keyspace', flush=True)\nsys.exit(2)\n")

    def test_out_of_bounds_progress_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "CUDA"):
            self.run_fixture("print('0.01 MKey/s (999 total)', flush=True)\n")

    def test_stop_terminates_process_and_preserves_prefix(self):
        started = time.monotonic()
        result, updates = self.run_fixture("import time\nprint('0.01 MKey/s (5 total)', flush=True)\ntime.sleep(30)\n",
                                         lambda: time.monotonic() - started > .3)
        self.assertEqual(result["next"], 6)
        self.assertLess(time.monotonic() - started, 6)

    def test_sigterm_ignoring_engine_is_killed(self):
        started = time.monotonic()
        result, _ = self.run_fixture("import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready', flush=True)\ntime.sleep(30)\n",
                                    lambda: time.monotonic() - started > .3)
        self.assertEqual(result["next"], 1)
        self.assertLess(time.monotonic() - started, 7)

    def test_found_file_is_parsed_with_range_check(self):
        code = "import sys\nfrom pathlib import Path\np=Path(sys.argv[sys.argv.index('-o')+1])\np.write_text(sys.argv[-1]+' 11\\n')\n"
        result, _ = self.run_fixture(code, start=1, end=20)
        self.assertEqual(result["found"], 17)


class LinuxPackagingTests(unittest.TestCase):
    def package_fixture(self, root):
        spec = importlib.util.spec_from_file_location("keytrail_linux_packager", ROOT / "scripts/package-linux.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT = root
        for name in ("README.md", "LINUX.md", "THIRD_PARTY.md", "requirements-linux.txt",
                     f"verification/v{VERSION}.md", "scripts/build-linux-cuda.py",
                     "resources/licenses/example-LICENSE"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Fixture notice", encoding="utf8")
        (root / "dist").mkdir()
        binary = root / "dist/KeyTrail"
        binary.write_bytes(b"\x7fELF\x02fixture-binary")
        manifest = {"file": "KeyTrail", "version": VERSION, "platform": "Linux x64",
                    "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        (root / "dist/build-manifest-linux.json").write_text(json.dumps(manifest))
        return module

    def test_archive_has_executable_permissions_and_no_private_state(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            root = Path(directory)
            module = self.package_fixture(root)
            (root / "private").mkdir()
            (root / "private/result-protection.key").write_bytes(b"PRIVATE-NOT-FOR-EXPORT")
            (root / "dist/progress.sqlite3").write_bytes(b"PRIVATE-NOT-FOR-EXPORT")
            with patch.object(sys, "argv", ["package-linux.py"]), contextlib.redirect_stdout(io.StringIO()):
                module.main()
            with tarfile.open(next((root / "dist").glob("*.tar.gz"))) as archive:
                names = archive.getnames()
                binary = next(m for m in archive.getmembers() if m.name.endswith("/KeyTrail"))
                self.assertEqual(binary.mode, 0o755)
                self.assertFalse(any("private" in name or "sqlite" in name or name.endswith(".exe") for name in names))
                self.assertTrue(any(name.endswith("/SHA256SUMS.txt") for name in names))

    def test_modified_binary_is_rejected_before_packaging(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            root = Path(directory)
            module = self.package_fixture(root)
            (root / "dist/KeyTrail").write_bytes(b"modified")
            with patch.object(sys, "argv", ["package-linux.py"]), self.assertRaisesRegex(ValueError, "manifest"):
                module.main()

    def test_builder_and_runtime_pin_the_same_upstream(self):
        spec = importlib.util.spec_from_file_location("keytrail_cuda_builder", ROOT / "scripts/build-linux-cuda.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.BITCRACK_COMMIT, BITCRACK_COMMIT)


if __name__ == "__main__":
    unittest.main()
