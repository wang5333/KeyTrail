from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import threading
import time
import urllib.error
import urllib.request

from .core import ROOT, TARGET

PROVIDERS = ("https://blockstream.info/api", "https://mempool.space/api")


class PriceFeed:
    """Display-only USD quote; never gates solving or transaction fees."""
    def __init__(self, network, interval=300):
        self.network, self.interval = network, interval
        self.stop_event, self.lock = threading.Event(), threading.RLock()
        self.value = {"state": "UNAVAILABLE", "usd_per_btc": None, "updated_at": None,
                      "source": "mempool.space"}

    def check_once(self):
        try:
            data = self.network.json("https://mempool.space/api/v1/prices")
            price, stamp = data["USD"], data["time"]
            if (isinstance(price, bool) or not isinstance(price, (float, int))
                    or not math.isfinite(price) or not 0 < price < 1e9
                    or isinstance(stamp, bool) or not isinstance(stamp, (float, int))
                    or not math.isfinite(stamp) or stamp <= 0 or stamp > time.time()+300):
                raise ValueError("Invalid USD quote")
            with self.lock:
                self.value = {"state": "READY", "usd_per_btc": price,
                              "updated_at": stamp, "source": "mempool.space"}
        except Exception:
            with self.lock:
                self.value["state"] = "STALE" if self.value["usd_per_btc"] else "UNAVAILABLE"
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            value = dict(self.value)
        if value["updated_at"] and time.time()-value["updated_at"] > 3600:
            value["state"] = "STALE"
        return value

    def start(self):
        def loop():
            while not self.stop_event.is_set():
                self.check_once()
                self.stop_event.wait(self.interval)
        threading.Thread(target=loop, name="display-only-usd-quote", daemon=True).start()

    def stop(self):
        self.stop_event.set()


class Network:
    def request(self, url, *, data=None, timeout=10, limit=4_000_000):
        request = urllib.request.Request(url, data=data,
            headers={"User-Agent": "Puzzle71Desk/1.0", "Content-Type": "text/plain"} if data else
                    {"User-Agent": "Puzzle71Desk/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = response.read(limit + 1)
        if len(result) > limit:
            raise ValueError("服务端响应过大")
        return result.decode("utf8")

    def json(self, url):
        return json.loads(self.request(url))

    def any_text(self, suffix):
        errors = []
        for base in PROVIDERS:
            try:
                return self.request(base + suffix)
            except Exception as error:
                errors.append(type(error).__name__)
        raise RuntimeError("两个链上服务暂时均未响应：" + "/".join(errors))

    def any_json(self, suffix):
        return json.loads(self.any_text(suffix))

    def fee_rate(self):
        try:
            values = self.json("https://mempool.space/api/v1/fees/recommended")
            return max(1, min(1000, int(values["fastestFee"])))
        except Exception:
            return None

    def broadcast(self, raw_hex):
        # Reached only after explicit manual confirmation or a user-armed payout grant.
        errors = []
        for base in PROVIDERS:
            try:
                return self.request(base + "/tx", data=raw_hex.encode("ascii"), timeout=15).strip()
            except Exception as error:
                errors.append(str(error)[:160])
        raise RuntimeError("提交未获确认，请先核对交易 ID，避免重复操作：" + "; ".join(errors))


class Monitor:
    """A chain-spend monitor, not a detector of somebody's offline private-key knowledge."""
    def __init__(self, network, callback, principal=None, interval=30):
        self.network, self.callback, self.interval = network, callback, interval
        self.principal = principal if principal is not None else json.loads(
            (ROOT / "resources" / "principal-utxos.json").read_text())
        self.stop_event, self.wake_event = threading.Event(), threading.Event()
        self.thread = None
        self.last_good = 0
        self.status = {"state": "CHECKING", "message": "正在核对公开奖金状态", "checked_at": None,
                       "last_good": 0, "balance_sats": None, "sources_ok": 0, "interval": interval}
        self.lock = threading.RLock()

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="public-prize-monitor", daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            result = dict(self.status)
        if self.last_good and time.time() - self.last_good > 120 and result["state"] == "READY":
            result.update(state="STALE", message="状态数据已过期，暂停计算等待重新核验")
        return result

    def check_once(self):
        successes, failures = [], []
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {pool.submit(self.network.json, base + "/address/" + TARGET): base for base in PROVIDERS}
            for job in as_completed(jobs):
                try:
                    value = job.result()
                    if value["address"] != TARGET:
                        raise ValueError("地址响应不匹配")
                    for name in ("chain_stats", "mempool_stats"):
                        if any(int(value[name][k]) < 0 for k in ("funded_txo_sum", "spent_txo_sum")):
                            raise ValueError("余额响应错误")
                    successes.append((jobs[job], value))
                except Exception as error:
                    failures.append(type(error).__name__)
        if not successes:
            current = time.time()
            return {"state": "STALE" if not self.last_good or current - self.last_good > 120 else "CHECKING",
                    "message": "联网核验失败；不是已解题的证据", "checked_at": current,
                    "last_good": self.last_good, "sources_ok": 0, "balance_sats": None,
                    "errors": failures, "interval": self.interval}
        self.last_good = time.time()
        balances = [v["chain_stats"]["funded_txo_sum"] - v["chain_stats"]["spent_txo_sum"] for _, v in successes]
        state, message, spending = "READY", "未检测到主要奖金支出", []
        if any(v["chain_stats"]["spent_txo_sum"] or v["mempool_stats"]["spent_txo_sum"] for _, v in successes):
            verified = {base: 0 for base, _ in successes}
            with ThreadPoolExecutor(max_workers=6) as pool:
                jobs = {pool.submit(self.network.json,
                    f"{base}/tx/{outpoint['txid']}/outspend/{outpoint['vout']}"):
                    (base, outpoint) for base, _ in successes for outpoint in self.principal}
                for job in as_completed(jobs):
                    try:
                        value = job.result()
                        if type(value.get("spent")) is not bool:
                            raise ValueError("输出状态格式错误")
                        base, outpoint = jobs[job]
                        verified[base] += 1
                        if value.get("spent"):
                            spending.append({"provider": base, "outpoint": f"{outpoint['txid']}:{outpoint['vout']}",
                                             "txid": value.get("txid"), "confirmed": bool(value.get("status", {}).get("confirmed"))})
                    except Exception:
                        pass
            if spending:
                confirmed_pairs = {}
                for entry in spending:
                    if entry["confirmed"]:
                        key = (entry["outpoint"], entry["txid"])
                        confirmed_pairs.setdefault(key, set()).add(entry["provider"])
                agreed = any(len(sources) >= 2 for sources in confirmed_pairs.values())
                state = "SPENT" if agreed else "SPEND_DETECTED"
                message = ("两个来源确认主要奖金发生支出，已停止搜索" if agreed else
                           "检测到主要奖金疑似被花费，已暂停并等待复核")
            elif any(v["mempool_stats"]["spent_txo_sum"] for _, v in successes):
                state, message = "SPEND_DETECTED", "交易池出现该地址支出，先暂停核验；尚不等同于奖金已确认领取"
            elif not any(count == len(self.principal) for count in verified.values()):
                state, message = "STALE", "地址有历史支出，但主要奖金核验未完成，先暂停等待复核"
        return {"state": state, "message": message, "checked_at": time.time(), "last_good": self.last_good,
                "balance_sats": str(min(balances)), "sources_ok": len(successes),
                "provider_disagreement": len(set(balances)) > 1, "spending": spending,
                "interval": self.interval, "note": "只能观察交易池/链上花费，不能观察他人离线解题进展"}

    def loop(self):
        while not self.stop_event.is_set():
            try:
                value = self.check_once()
            except Exception as error:
                value = {"state": "STALE", "message": "核验暂时失败：" + type(error).__name__,
                         "checked_at": time.time(), "last_good": self.last_good, "sources_ok": 0,
                         "balance_sats": None, "interval": self.interval}
            with self.lock:
                self.status = value
            self.callback(dict(value))
            self.wake_event.wait(self.interval)
            self.wake_event.clear()

    def refresh(self):
        self.wake_event.set()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
