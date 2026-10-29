"""Protected results, explicit opt-in payout authorization, and transaction receipts."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time

from embit import ec, script
from embit.networks import NETWORKS
from embit.transaction import Transaction, TransactionInput, TransactionOutput, SIGHASH

from .core import PAYOUT, TARGET, atomic_json, point_address, validate_payout


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("本发行版的结果保护使用 Windows 当前用户 DPAPI")
    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt32, kernel32 = ctypes.WinDLL("crypt32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt32.CryptUnprotectData
        ok = function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    else:
        function = crypt32.CryptProtectData
        ok = function(ctypes.byref(incoming), "Puzzle71 local result", None, None, None, 1, ctypes.byref(outgoing))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel32.LocalFree(outgoing.data)


def build_transaction(private_key, utxos, previous_transactions, fee_rate, *, target=TARGET, payout=PAYOUT, max_fee=200_000, chain="main"):
    """The injectable target is for offline tests; production callers always use constants."""
    key = ec.PrivateKey(private_key.to_bytes(32, "big"))
    owner_script = script.p2pkh(key.get_public_key())
    if owner_script.address(NETWORKS[chain]) != target:
        raise ValueError("候选私钥与公开谜题地址不匹配")
    output_script = validate_payout(payout,chain)
    if not 1 <= fee_rate <= 1000:
        raise ValueError("费率超出允许范围")
    selected, seen = [], set()
    for item in utxos:
        outpoint=(item["txid"],item["vout"])
        if outpoint in seen:
            raise ValueError("Duplicate transaction input")
        seen.add(outpoint)
        if item["value"] <= 149 * fee_rate:
            continue
        previous = Transaction.parse(bytes.fromhex(previous_transactions[item["txid"]]))
        if previous.txid().hex() != item["txid"]:
            raise ValueError("原始交易哈希不一致")
        if not 0 <= item["vout"] < len(previous.vout):
            raise ValueError("原始交易输出不存在")
        previous_output = previous.vout[item["vout"]]
        if previous_output.value != item["value"] or previous_output.script_pubkey.data != owner_script.data:
            raise ValueError("原始输出金额或锁定脚本不匹配")
        selected.append(item)
    if not selected:
        raise ValueError("没有核验通过且值得花费的输出")
    if len(selected) > 200:
        raise ValueError("输出过多，需人工分批复核")
    total = sum(item["value"] for item in selected)
    maximum_size = 10 + 149 * len(selected) + len(TransactionOutput(0, output_script).serialize())
    fee = maximum_size * fee_rate
    if fee > max_fee or total - fee < 546:
        raise ValueError("手续费超过本地上限，或净转出金额过小")
    transaction = Transaction(vin=[TransactionInput(bytes.fromhex(item["txid"]), item["vout"],
        sequence=0xFFFFFFFD) for item in selected],
        vout=[TransactionOutput(total - fee, output_script)])
    for index, item in enumerate(selected):
        digest = transaction.sighash_legacy(index, owner_script, SIGHASH.ALL)
        signature = key.sign(digest)
        if not key.get_public_key().verify(signature, digest):
            raise ValueError("签名自校验失败")
        encoded, public = signature.serialize() + b"\x01", key.get_public_key().sec()
        transaction.vin[index].script_sig = script.Script(bytes([len(encoded)]) + encoded + bytes([len(public)]) + public)
    raw = transaction.serialize()
    if len(raw) * fee_rate > fee:
        raise ValueError("实际交易大小超出手续费预算")
    return {"raw_hex": raw.hex(), "txid": transaction.txid().hex(), "payout": payout,
            "amount_sats": str(total - fee), "fee_sats": str(fee), "fee_rate": fee_rate,
            "vbytes": len(raw), "input_count": len(selected), "review_required": True}


class Claim:
    def __init__(self, directory, network, event, *, payout=PAYOUT, target=TARGET, chain="main"):
        self.directory, self.network, self.event = Path(directory), network, event
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.payout, self.target, self.chain = payout, target, chain
        if payout != "":
            validate_payout(payout,chain)
        self.state = {"state":"NONE","message":"No result yet","payout":payout}
        self.signed = None
        self.nonce, self.nonce_at, self.authorization = None, 0, None
        self.preparing = False
        ledger=self.directory/"transaction-review.json"
        raw=self.directory/"signed-transaction.txt"
        if ledger.exists() and raw.exists():
            try:
                prior=json.loads(ledger.read_text(encoding="utf8"))
                if prior.get("payout")==payout and prior.get("state") in ("SENDING","SUBMITTED","CONFIRMED","SUBMISSION_UNKNOWN"):
                    encoded=raw.read_text(encoding="ascii")
                    if Transaction.parse(bytes.fromhex(encoded)).txid().hex()==prior["txid"]:
                        self.signed=dict(prior,raw_hex=encoded)
                        self.state=prior
                        if prior["state"]=="SENDING":
                            self.state.update(state="SUBMISSION_UNKNOWN",message="Submission interrupted. Verify transaction ID before any retry.")
            except Exception:
                self.state.update(state="NEEDS_REVIEW",message="Saved transaction needs review")

    def snapshot(self):
        with self.lock:
            return dict(self.state,auto_armed=self.authorization is not None,payout=self.payout)

    def disarm(self):
        with self.lock:
            self.authorization=None

    def configure(self, payout, auto=False, confirmation="", acknowledge=False):
        output=None if payout=="" else validate_payout(payout,self.chain)
        if auto and output is None:
            raise ValueError("Set a Bitcoin receiving address first")
        with self.lock:
            if self.state["state"]!="NONE":
                if not auto and payout==self.payout:
                    self.authorization=None
                    return
                raise ValueError("Existing result: payout settings are locked")
            if auto and (confirmation!=payout or acknowledge is not True):
                raise ValueError("Confirm the full payout address and public-broadcast risk")
            self.payout=payout
            self.state["payout"]=payout
            self.authorization=({"payout":payout,"script":output.data.hex(),
                "max_fee":200000,"id":secrets.token_hex(24)} if auto else None)

    def _address(self, scalar):
        return script.p2pkh(ec.PrivateKey(scalar.to_bytes(32,"big")).get_public_key()).address(NETWORKS[self.chain])

    def _persist(self):
        atomic_json(self.directory/"transaction-review.json",{k:v for k,v in self.state.items() if k!="nonce"})

    def save_result(self, scalar):
        if self._address(scalar)!=self.target:
            raise ValueError("Independent puzzle address verification failed")
        encoded=protect(scalar.to_bytes(32,"big"))
        path=self.directory/"puzzle71-result.dpapi"
        temporary=path.with_suffix(".pending")
        with temporary.open("wb") as stream:
            stream.write(encoded);stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
        with self.lock:
            self.state.update(state="FOUND",message="Verified result saved locally")
        self.event("found","Verified result saved locally")
        threading.Thread(target=self.prepare,name="prepare-local-claim",daemon=True).start()

    def prepare(self):
        with self.lock:
            if self.preparing or self.state["state"] in ("SENDING","SUBMITTED","CONFIRMED","SUBMISSION_UNKNOWN"):
                return False
            self.preparing=True
            payout=self.payout
            authorization=self.authorization
        try:
            scalar=int.from_bytes(protect((self.directory/"puzzle71-result.dpapi").read_bytes(),True),"big")
            if self._address(scalar)!=self.target:
                raise ValueError("Saved result does not match puzzle")
            utxos=self.network.any_json("/address/"+self.target+"/utxo")
            fee_rate=self.network.fee_rate()
            if fee_rate is None:
                raise RuntimeError("Fee quote unavailable; nothing sent")
            previous={}
            for item in utxos:
                if item["value"]>149*fee_rate and item["txid"] not in previous:
                    previous[item["txid"]]=self.network.any_text("/tx/"+item["txid"]+"/hex").strip()
            signed=build_transaction(scalar,utxos,previous,fee_rate,target=self.target,payout=payout,chain=self.chain)
            path=self.directory/"signed-transaction.txt"
            temp=path.with_suffix(".pending")
            with temp.open("w",encoding="ascii") as stream:
                stream.write(signed["raw_hex"]);stream.flush();os.fsync(stream.fileno())
            temp.replace(path)
            with self.lock:
                self.signed=signed
                self.nonce,self.nonce_at=secrets.token_urlsafe(24),time.time()
                self.state={k:v for k,v in signed.items() if k!="raw_hex"}
                self.state.update(state="READY",nonce=self.nonce,message="Signed locally; not sent")
                self._persist()
                auto=(authorization is not None and self.authorization is authorization
                      and authorization["payout"]==payout
                      and authorization["script"]==validate_payout(payout,self.chain).data.hex()
                      and int(signed["fee_sats"])<=authorization["max_fee"])
            if auto:
                self._submit(authorization=authorization)
            else:
                self.event("found","Transaction ready for review")
            return True
        except Exception as error:
            with self.lock:
                if self.state["state"] not in ("SUBMISSION_UNKNOWN","SUBMITTED","CONFIRMED"):
                    self.state.update(state="NEEDS_REVIEW",message=str(error)[:250])
                    self._persist()
            self.event("warning",str(error)[:250])
            return False
        finally:
            with self.lock:
                self.preparing=False

    def confirm_broadcast(self, nonce, confirmation, acknowledge_race_risk):
        return self._submit(nonce=nonce,confirmation=confirmation,acknowledge=acknowledge_race_risk)

    def _submit(self, *, nonce=None, confirmation=None, acknowledge=False, authorization=None):
        with self.lock:
            if self.state["state"]!="READY" or not self.signed:
                raise ValueError("No reviewed transaction available")
            if authorization is not None:
                if self.authorization is not authorization or authorization["payout"]!=self.payout:
                    raise ValueError("Automatic payout authorization changed")
            elif (not secrets.compare_digest(str(nonce),self.nonce or "")
                    or time.time()-self.nonce_at>600 or confirmation!=self.payout or acknowledge is not True):
                raise ValueError("Confirm the payout address and broadcast risk; expired confirmations need a new transaction")
            raw,expected=self.signed["raw_hex"],self.signed["txid"]
            tx=Transaction.parse(bytes.fromhex(raw))
            if (tx.txid().hex()!=expected or len(tx.vout)!=1
                    or tx.vout[0].script_pubkey.data!=validate_payout(self.payout,self.chain).data
                    or tx.vout[0].value!=int(self.signed["amount_sats"])):
                raise ValueError("Signed destination or amount changed")
            self.state.update(state="SENDING",message="Submitting signed transaction",
                submission_mode="automatic" if authorization is not None else "manual")
            self.nonce=None
            self._persist()
        try:
            result=self.network.broadcast(raw)
            if result!=expected:
                raise RuntimeError("Returned transaction ID differs; verify before any retry")
            with self.lock:
                self.state.update(state="SUBMITTED",message="Submitted; awaiting confirmation",txid=result)
                self.authorization=None
                self._persist()
            self.event("found","Transaction submitted; awaiting confirmation")
            return {"txid":result,"submitted":True}
        except Exception as error:
            with self.lock:
                self.state.update(state="SUBMISSION_UNKNOWN",message="Verify transaction ID before retry: "+str(error)[:180])
                self.authorization=None
                self._persist()
            raise

    def refresh_confirmation(self):
        with self.lock:
            if self.state["state"] not in ("SUBMITTED","SUBMISSION_UNKNOWN"):
                return False
            expected=self.state["txid"]
            payout=self.payout
            amount=int(self.state["amount_sats"])
        try:
            value=self.network.any_json("/tx/"+expected)
            if value.get("txid")!=expected:
                raise ValueError("Receipt transaction ID mismatch")
            outputs=value.get("vout",[])
            if len(outputs)!=1 or outputs[0]["scriptpubkey"]!=validate_payout(payout,self.chain).data.hex() or outputs[0]["value"]!=amount:
                raise ValueError("Receipt amount or receiving script mismatch")
            confirmed=bool(value.get("status",{}).get("confirmed"))
            with self.lock:
                self.state.update(state="CONFIRMED" if confirmed else "SUBMITTED",
                    message="Confirmed on chain" if confirmed else "Submitted; awaiting confirmation")
                self._persist()
            if confirmed:
                self.event("found","Confirmed on chain")
            return confirmed
        except Exception:
            return False
