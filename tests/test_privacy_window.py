from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".deps")]
import tempfile
import time
import unittest
from unittest.mock import Mock

from app.core import PAYOUT, make_config, validate_config, validate_payout
from app.claim import Claim
from app.service import Application
from app.window import WindowControls, valid_local_url
from test_suite import FakeNetwork, TEST_OUTPUT


class PrivateDefaultsTests(unittest.TestCase):
    def test_new_config_has_no_receiving_address(self):
        self.assertEqual(PAYOUT, "")
        config = make_config()
        self.assertEqual(config["payout"], "")
        self.assertEqual(validate_config(config), config)
        with self.assertRaises(ValueError):
            validate_payout()

    def test_blank_destination_never_arms_or_starts(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as d:
            app = Application(d, FakeNetwork(), monitor_enabled=False)
            try:
                app.configure(make_config())
                app.monitor.status = {"state": "READY", "last_good": time.time()}
                with self.assertRaisesRegex(ValueError, "receiving address"):
                    app.start(600, "cpu")
                with self.assertRaises(ValueError):
                    app.claim.configure("", True, "", True)
                self.assertFalse(app.is_running())
                self.assertFalse(app.claim.snapshot()["auto_armed"])
                self.assertEqual(app.snapshot()["payout"], "")
            finally:
                app.shutdown()
                app.store.db.close()

    def test_builder_rejects_invalid_nonempty_placeholder(self):
        for value in (None, " ", "YOUR_ADDRESS", "invalid"):
            with self.assertRaises(ValueError):
                make_config(payout=value)


class NativeWindowTests(unittest.TestCase):
    def test_bridge_has_only_narrow_window_actions(self):
        controls = WindowControls()
        window = Mock()
        controls._window = window
        self.assertTrue(controls.maximize())
        window.maximize.assert_called_once()
        self.assertFalse(controls.maximize())
        window.restore.assert_called_once()
        controls.minimize()
        window.minimize.assert_called_once()
        controls.close()
        window.hide.assert_called_once()
        window.destroy.assert_not_called()
        self.assertEqual({k for k in dir(controls) if not k.startswith("_")},
                         {"minimize", "maximize", "close"})

    def test_ui_url_is_restricted_to_local_backend(self):
        self.assertTrue(valid_local_url("http://127.0.0.1:1234"))
        for value in ("https://example.com", "file:///C:/Windows", "http://localhost:1",
                      "http://127.0.0.1:1@evil.com", "http://127.0.0.1:1/path",
                      "http://127.0.0.1:x", "http://127.0.0.1:1#other"):
            self.assertFalse(valid_local_url(value), value)
