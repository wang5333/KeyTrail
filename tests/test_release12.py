from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/".deps")]
import json
import tempfile
import time
import unittest
from embit import ec,script
from embit.networks import NETWORKS
from embit.transaction import Transaction
from app.core import START,END,TARGET,make_config,config_digest,config_range,partition,parse_range,validate_payout,point_address
from app.claim import Claim
from app.service import Application
from app.store import Store
from test_suite import FakeNetwork,synthetic_funds,TEST_OUTPUT,PAYOUT


class RangeTests(unittest.TestCase):
    def test_custom_split_exact_and_disjoint(self):
        config=make_config(3,1,start=f"{START+500:X}",end=f"{START+1499:X}")
        parts=[partition(3,i,*config_range(config)) for i in (1,2,3)]
        self.assertEqual(parts[0][0],START+500)
        self.assertEqual(parts[-1][1],START+1499)
        self.assertEqual(sum(b-a+1 for a,b in parts),1000)
        self.assertEqual(parts[0][1]+1,parts[1][0])
        self.assertEqual(parts[1][1]+1,parts[2][0])

    def test_bad_ranges_rejected(self):
        for a,b in ((hex(START-1),hex(END)),(hex(START),hex(END+1)),(hex(END),hex(START)),("xyz",hex(END)),("-1",hex(END))):
            with self.assertRaises(ValueError):parse_range(a,b)
        with self.assertRaises(ValueError):make_config(3,1,start=f"{START:X}",end=f"{START+1:X}")

    def test_old_checkpoint_survives_custom_search_and_overlap_is_not_double_counted(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as d:
            store=Store(d)
            original=dict(store.config)
            store.commit(START+1000)
            custom=make_config(start=f"{START+500:X}",end=f"{START+1499:X}")
            store.change_config(custom)
            store.commit(START+1500)
            self.assertEqual(store.total_checked(),"1500")
            self.assertEqual(len(store.saved_plans()),2)
            store.change_config(original)
            self.assertEqual(store.progress()["checked"],"1000")
            self.assertEqual(store.total_checked(),"1500")
            store.db.close()


class PayoutTests(unittest.TestCase):
    def test_mainnet_addresses_and_wrong_networks(self):
        key=ec.PrivateKey((2).to_bytes(32,"big")).get_public_key()
        for out in (script.p2pkh(key),script.p2wpkh(key),script.p2sh(script.p2wpkh(key)),script.p2tr(key)):
            self.assertEqual(validate_payout(out.address()).data,out.data)
            with self.assertRaises(ValueError):validate_payout(out.address(NETWORKS["regtest"]))

    def test_payout_edit_preserves_progress_and_authorization_does_not_survive_restart(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as d:
            app=Application(d,FakeNetwork(),monitor_enabled=False)
            app.configure(make_config())
            app.store.commit(START+1000)
            destination=point_address(2)
            with self.assertRaises(ValueError):app.set_payout(destination,True,destination,False)
            app.set_payout(destination,True,destination,True)
            self.assertEqual(app.store.progress()["checked"],"1000")
            self.assertTrue(app.claim.snapshot()["auto_armed"])
            app.shutdown();app.store.db.close()
            again=Application(d,FakeNetwork(),monitor_enabled=False)
            self.assertEqual(again.store.config["payout"],destination)
            self.assertFalse(again.claim.snapshot()["auto_armed"])
            self.assertEqual(again.store.progress()["checked"],"1000")
            again.shutdown();again.store.db.close()

    def test_config_import_cannot_arm_automatic_payout(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as d:
            app=Application(d,FakeNetwork(),monitor_enabled=False)
            app.set_payout(PAYOUT,True,PAYOUT,True)
            config=make_config()
            config["auto_armed"]=True
            app.configure(config)
            self.assertFalse(app.claim.snapshot()["auto_armed"])
            app.shutdown();app.store.db.close()


class FixtureNetwork:
    def __init__(self):
        self.utxos,self.previous=synthetic_funds()
        self.broadcasts=[]
    def fee_rate(self):return 2
    def any_json(self,path):return self.utxos
    def any_text(self,path):return self.previous[path.split("/")[2]]
    def broadcast(self,raw):
        self.broadcasts.append(raw)
        return Transaction.parse(bytes.fromhex(raw)).txid().hex()


class AutoFlowTests(unittest.TestCase):
    def test_default_manual_flow_never_broadcasts(self):
        self.exercise(False)
    def test_explicit_authorization_broadcasts_exactly_once(self):
        self.exercise(True)
    def exercise(self,enabled):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT) as d:
            network=FixtureNetwork()
            claim=Claim(d,network,lambda *_:None,target=point_address(1),payout=PAYOUT)
            if enabled:claim.configure(PAYOUT,True,PAYOUT,True)
            claim.save_result(1)
            for _ in range(150):
                if claim.snapshot()["state"] in ("READY","SUBMITTED","NEEDS_REVIEW"):
                    if not enabled or claim.snapshot()["state"]!="READY":break
                time.sleep(.01)
            self.assertEqual(claim.snapshot()["state"],"SUBMITTED" if enabled else "READY")
            self.assertEqual(len(network.broadcasts),1 if enabled else 0)
            if enabled:
                self.assertFalse(claim.prepare())
                self.assertEqual(len(network.broadcasts),1)


if __name__=="__main__":
    unittest.main()
