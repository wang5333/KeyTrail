from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".deps")]

from concurrent.futures import ThreadPoolExecutor
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from embit import ec, script
from embit.transaction import Transaction, TransactionInput, TransactionOutput

from app.claim import Claim, build_transaction, protect
from app.core import (END, START, TARGET, WIDTH, make_config, partition,
                      point_address, validate_config, validate_payout)
PAYOUT = script.p2tr(ec.PrivateKey((2).to_bytes(32,"big")).get_public_key()).address()

from app.engine import cpu_scan
from app.network import Monitor, PROVIDERS, PriceFeed
from app.server import make_server
from app.service import Application
from app.store import Store

TEST_OUTPUT = ROOT / ".test-output"
TEST_OUTPUT.mkdir(exist_ok=True)


class CoreTests(unittest.TestCase):
    def test_all_partition_sizes_exactly_cover(self):
        for total in (1, 2, 3, 5, 17, 64, 1024):
            ranges = [partition(total, index) for index in range(1, total + 1)]
            self.assertEqual(ranges[0][0], START)
            self.assertEqual(ranges[-1][1], END)
            self.assertEqual(sum(b - a + 1 for a, b in ranges), WIDTH)
            self.assertTrue(all(ranges[i][1] + 1 == ranges[i + 1][0] for i in range(total - 1)))

    def test_invalid_node_rejected(self):
        for n, i in ((0, 1), (2, 0), (2, 3), (1025, 1)):
            with self.assertRaises(ValueError):
                partition(n, i)

    def test_config_checks(self):
        config = make_config(3, 2)
        self.assertEqual(validate_config(config), config)
        bad = dict(config, payout="attacker")
        with self.assertRaises(ValueError):
            validate_config(bad)
        bad = dict(config, index=1)
        with self.assertRaises(ValueError):
            validate_config(bad)

    def test_payout_and_curve(self):
        self.assertEqual(validate_payout(PAYOUT).script_type(), "p2tr")
        self.assertEqual(point_address(0xD2C55), "1HsMJxNiV7TLxmoF6uJNkydxPFDog4NQum")

    def test_two_independent_cpu_workers(self):
        # All scalars in this fixture are public test data, unrelated to #71.
        target = point_address(17)
        jobs = [(1, 10), (11, 20)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda interval: cpu_scan(*interval, target, lambda: False,
                                                             lambda *_: None), jobs))
        self.assertIsNone(results[0]["found"])
        self.assertEqual(results[1]["found"], 17)

    def test_cpu_stops_and_reports_only_completed_prefix(self):
        updates = []
        result = cpu_scan(0x80000, 0xFFFFF, point_address(0xD2C55),
                          lambda: True, lambda nxt, rate: updates.append(nxt))
        self.assertIsNone(result["found"])
        self.assertGreater(result["next"], 0x80000)
        self.assertEqual(updates[-1], result["next"])


class StoreTests(unittest.TestCase):
    def test_restart_and_no_rollback(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as d:
            store = Store(d)
            config = make_config(3, 2)
            store.change_config(config)
            start = partition(3, 2)[0]
            store.commit(start + 1234, 3.5)
            with self.assertRaises(ValueError):
                store.commit(start + 100)
            store.db.close()
            second = Store(d)
            self.assertEqual(second.config, config)
            self.assertEqual(int(second.progress()["next_hex"], 16), start + 1234)
            self.assertEqual(second.progress()["checked"], "1234")
            second.db.close()


class FakeNetwork:
    def __init__(self):
        self.offline = False
        self.spent = False
        self.pending = False
        self.dust_only = False
        self.disagree = False
        self.broadcasts = []

    def json(self, url):
        if self.offline:
            raise OSError("offline")
        if "/outspend/" in url:
            return {"spent": self.spent and not self.dust_only,
                    "txid": ("b" if self.disagree and url.startswith(PROVIDERS[1]) else "a") * 64,
                    "status": {"confirmed": not self.pending}}
        return {"address": TARGET,
                "chain_stats": {"funded_txo_sum": 710000000, "spent_txo_sum": 10 if self.spent and not self.pending else 0},
                "mempool_stats": {"funded_txo_sum": 0, "spent_txo_sum": 10 if self.spent and self.pending else 0}}

    def broadcast(self, raw_hex):
        self.broadcasts.append(raw_hex)
        return Transaction.parse(bytes.fromhex(raw_hex)).txid().hex()


class MonitorTests(unittest.TestCase):
    def monitor(self, network):
        return Monitor(network, lambda *_: None, principal=[{"txid": "1" * 64, "vout": 0}])

    def test_ready_not_solved(self):
        result = self.monitor(FakeNetwork()).check_once()
        self.assertEqual(result["state"], "READY")
        self.assertEqual(result["sources_ok"], 2)

    def test_confirmed_spend_requires_two_agreeing_sources(self):
        network = FakeNetwork()
        network.spent = True
        self.assertEqual(self.monitor(network).check_once()["state"], "SPENT")
        network.disagree = True
        self.assertEqual(self.monitor(network).check_once()["state"], "SPEND_DETECTED")

    def test_pending_spend_pauses_but_is_not_confirmed_solved(self):
        network = FakeNetwork()
        network.spent, network.pending = True, True
        self.assertEqual(self.monitor(network).check_once()["state"], "SPEND_DETECTED")

    def test_dust_spend_does_not_mean_prize_spent(self):
        network = FakeNetwork()
        network.spent, network.dust_only = True, True
        self.assertEqual(self.monitor(network).check_once()["state"], "READY")

    def test_network_failure_is_not_prize_claim(self):
        network = FakeNetwork()
        network.offline = True
        self.assertEqual(self.monitor(network).check_once()["state"], "STALE")


class DisplayUpdateTests(unittest.TestCase):
    def test_usd_quote_and_stale_fallback(self):
        class Quote:
            fail = False
            def json(self, url):
                if self.fail:
                    raise OSError("offline")
                return {"USD": 80000, "time": time.time()}
        network = Quote()
        feed = PriceFeed(network)
        self.assertEqual(feed.check_once()["state"], "READY")
        self.assertEqual(feed.snapshot()["usd_per_btc"], 80000)
        network.fail = True
        self.assertEqual(feed.check_once()["state"], "STALE")
        self.assertEqual(feed.snapshot()["usd_per_btc"], 80000)

    def test_bad_quotes_are_not_displayed_as_zero_dollars(self):
        for bad in (0, -1, True, float("nan"), "80000", None):
            class Quote:
                def json(self, url):
                    return {"USD": bad, "time": time.time()}
            result = PriceFeed(Quote()).check_once()
            self.assertEqual(result["state"], "UNAVAILABLE")
            self.assertIsNone(result["usd_per_btc"])

    def test_default_english_and_saved_language_preserve_progress(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            app = Application(directory, FakeNetwork(), monitor_enabled=False)
            self.assertEqual(app.snapshot()["language"], "en")
            app.configure(make_config(2, 1, payout=PAYOUT))
            start = int(app.store.progress()["next_hex"], 16)
            app.store.commit(start+202505191424)
            for language in ("en", "zh-CN", "zh-TW", "ja", "es"):
                app.set_language(language)
                self.assertEqual(app.snapshot()["language"], language)
                self.assertEqual(app.store.progress()["checked"], "202505191424")
            with self.assertRaises(ValueError):
                app.set_language("invalid")
            config = dict(app.store.config)
            app.shutdown()
            app.store.db.close()
            again = Application(directory, FakeNetwork(), monitor_enabled=False)
            self.assertEqual(again.snapshot()["language"], "es")
            self.assertEqual(again.store.config, config)
            self.assertEqual(again.store.progress()["checked"], "202505191424")
            self.assertEqual(again.run_state, "PAUSED")
            again.shutdown()
            again.store.db.close()

    def test_failed_principal_checks_do_not_report_ready(self):
        class Partial(FakeNetwork):
            def json(self, url):
                if "/outspend/" in url:
                    raise OSError("outspend endpoint unavailable")
                return super().json(url)
        network = Partial()
        network.spent = True
        monitor = Monitor(network, lambda *_: None, principal=[{"txid": "1"*64, "vout": 0}])
        self.assertEqual(monitor.check_once()["state"], "STALE")


class PortabilityTests(unittest.TestCase):
    def test_service_stop_and_restart_uses_saved_cursor(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            app = Application(directory, FakeNetwork(), monitor_enabled=False)
            app.configure(make_config(2, 1, payout=PAYOUT))
            app.monitor.status = {"state": "READY", "last_good": time.time(), "message": "fixture"}
            app.monitor.last_good = time.time()
            starts = []
            def local_fixture(start, end, target, should_stop, progress):
                self.assertEqual(target, TARGET)
                starts.append(start)
                progress(start + 512, 1000)
                while not should_stop():
                    time.sleep(.01)
                return {"next": start + 512, "found": None}
            with patch("app.service.cpu_scan", local_fixture):
                for _ in range(2):
                    app.start(600, "cpu")
                    deadline = time.monotonic() + 3
                    while len(starts) < _ + 1 and time.monotonic() < deadline:
                        time.sleep(.01)
                    app.stop()
                    app.worker.join(3)
                    self.assertFalse(app.is_running())
            self.assertEqual(starts[1], starts[0] + 512)
            self.assertEqual(app.store.progress()["checked"], "1024")
            app.shutdown()
            app.store.db.close()

    def test_copied_data_requires_new_node_confirmation(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            first = Application(directory, FakeNetwork(), monitor_enabled=False, machine_id="machine-A")
            config = make_config(3, 1)
            first.configure(config)
            start = int(first.store.progress()["start_hex"], 16)
            first.store.commit(start + 100)
            first.shutdown()
            first.store.db.close()
            second = Application(directory, FakeNetwork(), monitor_enabled=False, machine_id="machine-B")
            self.assertFalse(second.settings["plan_ready"])
            self.assertEqual(second.store.progress()["checked"], "100")
            second.configure(make_config(3, 2, config["plan_id"]))
            self.assertTrue(second.settings["plan_ready"])
            second.shutdown()
            second.store.db.close()

    def test_checking_status_cannot_extend_expired_chain_check(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as directory:
            app = Application(directory, FakeNetwork(), monitor_enabled=False)
            app.is_running = lambda: True
            app.monitor.snapshot = lambda: {"state": "CHECKING", "last_good": time.time()-180,
                                            "message": "stale"}
            app.stop = Mock()
            thread = threading.Thread(target=app.guard, daemon=True)
            thread.start()
            time.sleep(2.2)
            app.shutdown_event.set()
            thread.join(3)
            app.stop.assert_called_with("stale")
            app.store.db.close()


def synthetic_funds(value=1_000_000):
    owner = ec.PrivateKey((1).to_bytes(32, "big")).get_public_key()
    previous = Transaction(vin=[TransactionInput(bytes.fromhex("11" * 32), 0)],
                           vout=[TransactionOutput(value, script.p2pkh(owner))])
    item = {"txid": previous.txid().hex(), "vout": 0, "value": value}
    return [item], {item["txid"]: previous.serialize().hex()}


class ClaimTests(unittest.TestCase):
    def test_offline_signatures_and_destination(self):
        utxos, previous = synthetic_funds()
        value = build_transaction(1, utxos, previous, 2, target=point_address(1), payout=PAYOUT)
        transaction = Transaction.parse(bytes.fromhex(value["raw_hex"]))
        self.assertEqual(len(transaction.vout), 1)
        self.assertEqual(transaction.vout[0].script_pubkey.address(), PAYOUT)
        self.assertEqual(transaction.vout[0].value + int(value["fee_sats"]), utxos[0]["value"])
        encoded = transaction.vin[0].script_sig.data
        sig_len = encoded[0]
        signature = ec.Signature.parse(encoded[1:sig_len])
        key = ec.PrivateKey((1).to_bytes(32, "big")).get_public_key()
        self.assertTrue(key.verify(signature, transaction.sighash_legacy(0, script.p2pkh(key))))

    def test_reject_wrong_previous_amount(self):
        utxos, previous = synthetic_funds()
        utxos[0]["value"] += 1
        with self.assertRaises(ValueError):
            build_transaction(1, utxos, previous, 1, target=point_address(1), payout=PAYOUT)

    def test_reject_fee_cap_and_wrong_key(self):
        utxos, previous = synthetic_funds()
        with self.assertRaises(ValueError):
            build_transaction(1, utxos, previous, 1000, target=point_address(1), payout=PAYOUT, max_fee=1)
        with self.assertRaises(ValueError):
            build_transaction(2, utxos, previous, 1, target=point_address(1), payout=PAYOUT)

    def test_dpapi_roundtrip(self):
        secret = bytes(range(32))
        protected = protect(secret)
        self.assertNotEqual(protected, secret)
        self.assertEqual(protect(protected, True), secret)

    def test_broadcast_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as d:
            network = FakeNetwork()
            claim = Claim(d, network, lambda *_: None, payout=PAYOUT)
            utxos, previous = synthetic_funds()
            claim.signed = build_transaction(1, utxos, previous, 1, target=point_address(1), payout=PAYOUT)
            claim.state = {"state": "READY"}
            claim.nonce, claim.nonce_at = "test-nonce", time.time()
            with self.assertRaises(ValueError):
                claim.confirm_broadcast("wrong", PAYOUT, True)
            with self.assertRaises(ValueError):
                claim.confirm_broadcast("test-nonce", PAYOUT, False)
            self.assertEqual(network.broadcasts, [])
            result = claim.confirm_broadcast("test-nonce", PAYOUT, True)
            self.assertTrue(result["submitted"])
            self.assertEqual(len(network.broadcasts), 1)  # Mock only: never contacts Bitcoin.


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_OUTPUT)
        self.app = Application(self.tmp.name, FakeNetwork(), monitor_enabled=False)
        self.server = make_server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.app.store.db.close()
        self.tmp.cleanup()

    def request(self, path, body=None, **headers):
        request = urllib.request.Request(self.url + path,
            data=None if body is None else json.dumps(body).encode(), headers=headers)
        return urllib.request.urlopen(request, timeout=4)

    def test_bootstrap_and_readonly_state(self):
        bootstrap = json.load(self.request("/api/bootstrap"))
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/state")
        state = json.load(self.request("/api/state", **{"X-Local-Token": bootstrap["token"]}))
        self.assertFalse(state["running"])
        self.assertEqual(state["target"], TARGET)

    def test_cross_origin_and_traversal_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("/api/bootstrap", Origin="https://not-local.example")
        self.assertEqual(context.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/../../main.py")

    def test_start_needs_plan_and_chain_check(self):
        token = json.load(self.request("/api/bootstrap"))["token"]
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("/api/start", {"duration": 600, "backend": "cpu"}, **{"X-Local-Token": token})
        self.assertEqual(context.exception.code, 400)
        self.assertFalse(self.app.is_running())

    def test_language_endpoint_and_locale_script(self):
        token = json.load(self.request("/api/bootstrap"))["token"]
        self.request("/api/preferences", {"language": "ja"}, **{"X-Local-Token": token}).read()
        state = json.load(self.request("/api/bootstrap"))["state"]
        self.assertEqual(state["language"], "ja")
        self.assertIn(b"PuzzleI18n", self.request("/i18n.js").read())
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/api/preferences", {"language": "invalid"}, **{"X-Local-Token": token})

    def test_exported_config_nodes_are_disjoint(self):
        import io, zipfile
        token = json.load(self.request("/api/bootstrap"))["token"]
        raw = self.request("/api/plan", {"total": 3}, **{"X-Local-Token": token}).read()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            configs = [json.loads(archive.read(f"computer-{i:03}.json")) for i in range(1, 4)]
        self.assertEqual(len(set(c["plan_id"] for c in configs)), 1)
        ranges = [partition(c["total"], c["index"]) for c in configs]
        self.assertTrue(all(ranges[i][1] + 1 == ranges[i + 1][0] for i in (0, 1)))

    def test_invalid_plan_sizes_return_400_without_losing_server(self):
        token = json.load(self.request("/api/bootstrap"))["token"]
        for total in (-1, 0, 1025):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/plan", {"total": total}, **{"X-Local-Token": token})
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(json.load(self.request("/api/ping"))["app"], "puzzle71-desk")


if __name__ == "__main__":
    unittest.main(verbosity=2)
