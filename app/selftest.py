"""Offline EXE payout test: synthetic outputs, no Bitcoin network access."""
import time
from embit import ec,script
from embit.transaction import Transaction,TransactionInput,TransactionOutput
from .claim import Claim
from .core import point_address

TEST_PAYOUT=script.p2tr(ec.PrivateKey((2).to_bytes(32,"big")).get_public_key()).address()


def payout_selftest(directory):
    key=ec.PrivateKey((1).to_bytes(32,"big")).get_public_key()
    previous=Transaction(vin=[TransactionInput(bytes.fromhex("11"*32),0)],
        vout=[TransactionOutput(1000000,script.p2pkh(key))])
    txid=previous.txid().hex()

    class OfflineNetwork:
        def __init__(self):
            self.broadcasts=[]
        def any_json(self,path):
            return [{"txid":txid,"vout":0,"value":1000000}]
        def any_text(self,path):
            return previous.serialize().hex()
        def fee_rate(self):
            return 2
        def broadcast(self,raw):
            self.broadcasts.append(raw)
            return Transaction.parse(bytes.fromhex(raw)).txid().hex()

    network=OfflineNetwork()
    claim=Claim(directory,network,lambda *_:None,target=point_address(1),payout=TEST_PAYOUT)
    claim.configure(TEST_PAYOUT,True,TEST_PAYOUT,True)
    claim.save_result(1)
    for _ in range(200):
        if claim.snapshot()["state"] in ("SUBMITTED","NEEDS_REVIEW","SUBMISSION_UNKNOWN"):
            break
        time.sleep(.01)
    state=claim.snapshot()
    assert state["state"]=="SUBMITTED",state
    assert len(network.broadcasts)==1
    tx=Transaction.parse(bytes.fromhex(network.broadcasts[0]))
    assert tx.vout[0].script_pubkey.address()==TEST_PAYOUT
    assert tx.vout[0].value+int(state["fee_sats"])==1000000
    assert claim.prepare() is False
    return {"pass":True,"offline_only":True,"real_network_broadcasts":0,
        "synthetic_callback_count":1,"destination_verified":True,"amount_and_fee_verified":True}
