"""End-to-end payout acceptance on an isolated Bitcoin Core REGTEST node only."""
import base64
from decimal import Decimal
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/".deps")]
from embit import ec,script
from embit.networks import NETWORKS
from app.claim import Claim

OUT=ROOT/"evidence/regtest-v1.2"
OUT.mkdir(parents=True,exist_ok=True)
with socket.socket() as socket_:
    socket_.bind(("127.0.0.1",0))
    PORT=socket_.getsockname()[1]
DATADIR=OUT/("node-"+str(time.time_ns()))
DATADIR.mkdir()
EXE=ROOT/".test-tools/bitcoin-core-32.1/bitcoin-32.1/bin/bitcoind.exe"


class RPC:
    def call(self,method,params=None,wallet=None):
        cookie=(DATADIR/"regtest/.cookie").read_text().strip()
        path="/wallet/"+wallet if wallet else "/"
        req=urllib.request.Request("http://127.0.0.1:"+str(PORT)+path,
            data=json.dumps({"jsonrpc":"2.0","id":"local-test","method":method,"params":params or []}).encode(),
            headers={"Authorization":"Basic "+base64.b64encode(cookie.encode()).decode(),"Content-Type":"application/json"})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req,timeout=30) as response:
                result=json.loads(response.read(),parse_float=Decimal)
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode()) from error
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        return result["result"]


class LocalOnlyNetwork:
    def __init__(self,rpc,target,ambiguous=False):
        self.rpc,self.target,self.broadcasts,self.ambiguous=rpc,target,[],ambiguous

    def any_json(self,path):
        if path.startswith("/address/") and path.endswith("/utxo"):
            result=self.rpc.call("scantxoutset",["start",["addr("+self.target+")"]])
            return [{"txid":x["txid"],"vout":x["vout"],"value":int(x["amount"]*100000000)} for x in result["unspents"]]
        if path.startswith("/tx/"):
            tx=self.rpc.call("getrawtransaction",[path[4:],True])
            return {"txid":tx["txid"],"status":{"confirmed":tx.get("confirmations",0)>0},
                "vout":[{"scriptpubkey":v["scriptPubKey"]["hex"],"value":int(v["value"]*100000000)} for v in tx["vout"]]}
        raise AssertionError("Unexpected test URL")

    def any_text(self,path):
        assert path.startswith("/tx/") and path.endswith("/hex")
        return self.rpc.call("getrawtransaction",[path.split("/")[2]])

    def fee_rate(self):
        return 2

    def broadcast(self,raw):
        # Hard tripwire: these tests must never send to any non-regtest node.
        assert self.rpc.call("getblockchaininfo")["chain"]=="regtest"
        self.broadcasts.append(raw)
        txid=self.rpc.call("sendrawtransaction",[raw])
        if self.ambiguous:
            raise TimeoutError("Simulated lost reply AFTER regtest acceptance")
        return txid


def main():
    log=(OUT/"node.stdout.log").open("w",encoding="utf8")
    process=subprocess.Popen([str(EXE),"-regtest","-server=1","-listen=0","-connect=0",
        "-dnsseed=0","-discover=0","-txindex=1","-fallbackfee=0.00002",
        "-rpcbind=127.0.0.1","-rpcallowip=127.0.0.1","-rpcport="+str(PORT),
        "-datadir="+str(DATADIR)],stdout=log,stderr=subprocess.STDOUT,creationflags=0x08000000)
    rpc=RPC()
    try:
        for _ in range(100):
            try:
                assert rpc.call("getblockchaininfo")["chain"]=="regtest"
                break
            except Exception:
                time.sleep(.2)
        else:
            raise RuntimeError("Regtest node failed to start")
        rpc.call("createwallet",["miner"])
        rpc.call("createwallet",["receiver"])
        miner=rpc.call("getnewaddress",wallet="miner")
        rpc.call("generatetoaddress",[101,miner])
        source=script.p2pkh(ec.PrivateKey((1).to_bytes(32,"big")).get_public_key()).address(NETWORKS["regtest"])
        cases=[]
        for kind in ("bech32m","bech32","legacy","ambiguous"):
            destination=rpc.call("getnewaddress",["","bech32m" if kind=="ambiguous" else kind],wallet="receiver")
            rpc.call("sendtoaddress",[source,0.01],wallet="miner")
            rpc.call("generatetoaddress",[1,miner])
            network=LocalOnlyNetwork(rpc,source,kind=="ambiguous")
            folder=OUT/("claim-"+kind+"-"+str(time.time_ns()))
            claim=Claim(folder,network,lambda *_:None,payout=destination,target=source,chain="regtest")
            claim.configure(destination,True,destination,True)
            claim.save_result(1)
            for _ in range(100):
                state=claim.snapshot()
                if state["state"] in ("SUBMITTED","SUBMISSION_UNKNOWN","NEEDS_REVIEW"):
                    break
                time.sleep(.1)
            assert state["state"]==("SUBMISSION_UNKNOWN" if kind=="ambiguous" else "SUBMITTED"),state
            assert len(network.broadcasts)==1
            assert claim.prepare() is False,"An accepted/ambiguous transaction must not auto-retry"
            assert not claim.refresh_confirmation()
            rpc.call("generatetoaddress",[1,miner])
            assert claim.refresh_confirmation()
            received=rpc.call("getreceivedbyaddress",[destination,1],wallet="receiver")
            assert int(received*100000000)==int(claim.snapshot()["amount_sats"])
            restored=Claim(folder,network,lambda *_:None,payout=destination,target=source,chain="regtest")
            assert restored.snapshot()["state"]=="CONFIRMED"
            assert not restored.snapshot()["auto_armed"]
            cases.append({"type":kind,"txid":claim.snapshot()["txid"],"received_sats":int(received*100000000),
                "fee_sats":int(claim.snapshot()["fee_sats"]),"confirmed":True,"broadcasts":len(network.broadcasts)})
        report={"pass":True,"chain":"regtest","real_btc_used":False,"mainnet_broadcasts":0,"cases":cases,
            "checks":["automatic signing","Core mempool acceptance","mined confirmation","exact receiving amount",
                      "Taproot / SegWit / legacy","lost-response recovery without repeat broadcast",
                      "receipt restoration","auto authorization resets on restart"]}
        (ROOT/"evidence/regtest-check-v1.2.json").write_text(json.dumps(report,indent=2),encoding="utf8")
        print(json.dumps(report),flush=True)
    finally:
        try:
            rpc.call("stop")
        except Exception:
            pass
        try:
            process.wait(20)
        except subprocess.TimeoutExpired:
            process.terminate();process.wait(10)
        log.close()


if __name__=="__main__":
    main()
