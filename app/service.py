import json
import hashlib
import os
from pathlib import Path
import platform
import threading
import time
from functools import wraps

from .claim import Claim
from .core import (CHUNK, PAYOUT, QUANTUM, TARGET, TEMPERATURE_LIMIT, VERSION, WIDTH,
                   atomic_json, partition, config_digest, validate_payout, config_range)
from .engine import cpu_scan, detect_gpu, gpu_scan, gpu_selftest, hardware
from .network import Monitor, Network, PriceFeed
from .store import Store


def serialized(method):
    @wraps(method)
    def locked(self,*args,**kwargs):
        with self.lock:
            return method(self,*args,**kwargs)
    return locked


def machine_signature():
    identity = platform.node()
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography", 0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                identity = winreg.QueryValueEx(key, "MachineGuid")[0]
        except OSError:
            pass
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class Application:
    def __init__(self, directory, network=None, monitor_enabled=True, machine_id=None):
        self.store = Store(directory)
        self.directory = self.store.directory
        self.network = network or Network()
        self.lock, self.stop_event, self.shutdown_event = threading.RLock(), threading.Event(), threading.Event()
        self.worker = None
        self.run_state, self.reason, self.backend, self.rate = "IDLE", "尚未开始计算", "检测中", 0
        self.gpu = {"available": False, "checking": True}
        self.settings_path = self.directory / "settings.json"
        self.settings = (json.loads(self.settings_path.read_text()) if self.settings_path.exists()
                         else {"plan_ready": False, "duration": 600, "backend": "auto"})
        machine = machine_id or machine_signature()
        copied = self.settings.get("machine_signature") not in (None, machine)
        if copied:
            self.settings["plan_ready"] = False
        self.settings["machine_signature"] = machine
        self.settings.setdefault("language", "en")
        atomic_json(self.settings_path, self.settings)
        self.claim = Claim(self.directory / "private", self.network, self.event,payout=self.store.config["payout"])
        self.last_receipt_check = 0
        self.monitor = Monitor(self.network, self.on_monitor)
        self.price = PriceFeed(self.network)
        self.last_monitor_state, self.started_at, self.deadline = None, None, None
        self.last_runtime_tick = None
        if monitor_enabled:
            self.monitor.start()
            self.price.start()
            threading.Thread(target=self.detect, name="detect-cuda", daemon=True).start()
            threading.Thread(target=self.guard, name="safety-watch", daemon=True).start()
        self.event("info", "软件已就绪。不会随启动自动计算，也不会自动广播领奖交易。")
        if copied:
            self.event("warning", "检测到数据来自另一台电脑：请导入本机独立编号，避免两台电脑重复计算。原进度未删除。")
        if (self.directory / "private/puzzle71-result.dpapi").exists():
            self.run_state = "FOUND"
            if self.claim.state["state"]=="NONE":
                self.claim.state.update(state="FOUND", message="本机已有加密结果，可重新准备待确认交易")
        elif int(self.store.progress()["checked"]) > 0:
            self.run_state, self.reason = "PAUSED", "已停止，进度已保存"

    def event(self, kind, message):
        self.store.event(kind, message)
        if kind in ("found", "alert"):
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except (ImportError, RuntimeError):
                pass

    def detect(self):
        value = detect_gpu()
        with self.lock:
            self.gpu = value
            self.backend = "CUDA 就绪" if value.get("available") else "CPU 就绪"
        self.event("info", ("已识别 " + value.get("name", "NVIDIA CUDA 设备")) if value.get("available")
                   else "没有兼容 CUDA 引擎，CPU 模式可用；不会假称使用独显。")

    def on_monitor(self, value):
        state = value["state"]
        if state != self.last_monitor_state:
            self.event("alert" if state in ("SPENT", "SPEND_DETECTED") else
                       "warning" if state == "STALE" else "info", value["message"])
            self.last_monitor_state = state
        if state in ("SPENT", "SPEND_DETECTED", "STALE"):
            self.stop(value["message"])

    def guard(self):
        while not self.shutdown_event.wait(2):
            if time.monotonic()-self.last_receipt_check>30:
                self.last_receipt_check=time.monotonic()
                self.claim.refresh_confirmation()
            if self.gpu.get("available"):
                value = hardware()
                if value.get("available"):
                    with self.lock:
                        self.gpu.update(value)
            if self.is_running():
                if self.deadline and time.monotonic() >= self.deadline:
                    self.stop("达到本次运行时限，进度已保存")
                status = self.monitor.snapshot()
                if (status["state"] not in ("READY", "CHECKING")
                        or not status.get("last_good") or time.time() - status["last_good"] > 120):
                    self.stop(status["message"])
                if self.backend.startswith("CUDA"):
                    value = self.gpu
                    if value.get("available"):
                        with self.lock:
                            self.gpu.update(value)
                        try:
                            if float(value["temperature"]) >= TEMPERATURE_LIMIT:
                                self.event("alert", "独显温度达到 82°C，停止并保存进度")
                                self.stop("温度保护：请等待机器降温")
                        except ValueError:
                            pass

    def is_running(self):
        return self.worker is not None and self.worker.is_alive()

    @serialized
    def configure(self, config):
        if self.is_running():
            raise ValueError("先停止当前计算，再更换分工配置")
        if self.claim.snapshot()["state"] != "NONE":
            raise ValueError("本机已有候选结果，先处理结果再修改配置")
        self.store.change_config(config)
        self.claim.configure(config["payout"])
        self.settings["plan_ready"] = True
        atomic_json(self.settings_path, self.settings)

    @serialized
    def set_payout(self, payout, auto=False, confirmation="", acknowledge=False):
        if self.is_running():
            raise ValueError("Stop before changing payout settings")
        value=str(payout).strip()
        output=validate_payout(value)
        canonical=output.address()
        if auto and (confirmation!=value or acknowledge is not True):
            raise ValueError("Confirm the full payout address and public-broadcast risk")
        if not isinstance(auto,bool):
            raise ValueError("Invalid automatic payout mode")
        with self.lock:
            old=self.store.config["payout"]
            self.claim.configure(canonical,auto,canonical if auto else "",acknowledge)
            config=dict(self.store.config,payout=canonical)
            config["digest"]=config_digest(config)
            try:
                self.store.change_config(config)
            except Exception:
                self.claim.disarm()
                self.claim.payout=old
                raise
        self.event("info","Automatic payout armed" if auto else "Payout address saved; automatic payout off")

    @serialized
    def start(self, duration=600, backend="auto"):
        if duration not in (0, 600, 3600, 14400):
            raise ValueError("运行时限仅支持 10 分钟、1 小时、4 小时或手动停止")
        if backend not in ("auto", "cpu"):
            raise ValueError("计算模式错误")
        if self.is_running():
            raise ValueError("当前已经在运行")
        if not self.settings.get("plan_ready"):
            raise ValueError("先生成或导入电脑编号配置；异地电脑应使用不同编号")
        if not self.store.config["payout"]:
            raise ValueError("Set a Bitcoin receiving address first")
        validate_payout(self.store.config["payout"])
        status = self.monitor.snapshot()
        if status["state"] != "READY" or not status.get("last_good") or time.time() - status["last_good"] > 120:
            raise ValueError("先完成最新链上核验；状态未明确时不启动")
        if self.claim.snapshot()["state"] != "NONE":
            raise ValueError("已有候选结果，计算保持停止")
        row = self.store.progress()
        if int(row["next_hex"], 16) > int(row["end_hex"], 16):
            raise ValueError("本机区间已完成")
        self.settings.update(duration=duration, backend=backend)
        atomic_json(self.settings_path, self.settings)
        self.stop_event.clear()
        self.started_at, self.last_runtime_tick = time.monotonic(), time.monotonic()
        self.deadline = self.started_at + duration if duration else None
        self.run_state, self.reason = "STARTING", "准备引擎与已解题自校验"
        self.worker = threading.Thread(target=self.run, name="fixed-puzzle71-worker", daemon=True)
        self.worker.start()

    def set_language(self, language):
        if language not in ("en", "zh-CN", "zh-TW", "ja", "es"):
            raise ValueError("Unsupported language")
        with self.lock:
            self.settings["language"] = language
            atomic_json(self.settings_path, self.settings)

    def stop(self, reason="用户停止，已保存最后确认进度"):
        if self.is_running():
            self.stop_event.set()
            with self.lock:
                self.reason, self.run_state = reason, "STOPPING"

    def should_stop(self):
        if self.deadline and time.monotonic() >= self.deadline:
            self.stop_event.set()
            self.reason = "达到本次运行时限，已保存进度"
        return self.stop_event.is_set() or self.shutdown_event.is_set()

    def commit(self, next_key, rate):
        with self.lock:
            current = time.monotonic()
            elapsed = current - self.last_runtime_tick if self.last_runtime_tick else 0
            self.last_runtime_tick = current
            self.store.commit(next_key, elapsed)
            self.rate = max(0, rate)

    def run(self):
        use_gpu = self.settings["backend"] == "auto" and self.gpu.get("available")
        self.backend = "CUDA · 设备 0" if use_gpu else "CPU · 单线程"
        work = self.directory / "work"
        work.mkdir(exist_ok=True)
        try:
            if use_gpu:
                self.event("info", "正在使用 CUDA 设备 0 复现已解 #20，验证硬件和引擎")
                try:
                    gpu_selftest(work, self.stop_event,
                        lambda line: self._engine_log(line, "preflight.log"))
                    self.event("info", "CUDA 已解题校验通过；正式区间为 #71")
                except Exception as error:
                    if self.should_stop():
                        return
                    use_gpu = False
                    self.backend = "CPU · CUDA 校验失败后降级"
                    self.event("warning", "CUDA 自校验未通过，改为 CPU：" + str(error)[:160])
            with self.lock:
                self.run_state, self.reason = "RUNNING", "正在计算本机独立区间"
            self.event("info", f"开始计算：电脑 {self.store.config['index']}/{self.store.config['total']}，{self.backend}")
            while not self.should_stop():
                row = self.store.progress()
                cursor, end = int(row["next_hex"], 16), int(row["end_hex"], 16)
                if cursor > end:
                    self.run_state, self.reason = "COMPLETE", "本机分配区间已完成"
                    self.event("info", self.reason)
                    break
                remaining = end - cursor + 1
                if use_gpu and remaining >= QUANTUM:
                    length = min(CHUNK, remaining // QUANTUM * QUANTUM)
                    try:
                        result = gpu_scan(cursor, cursor + length - 1, TARGET, work,
                            self.should_stop, self.commit, lambda line: self._engine_log(line, "cuda.log"))
                    except Exception as error:
                        if self.should_stop():
                            break
                        use_gpu = False
                        self.backend = "CPU · CUDA 运行失败后降级"
                        self.event("warning", "CUDA 运行异常，保留进度并降级 CPU：" + str(error)[:160])
                        continue
                else:
                    result = cpu_scan(cursor, end, TARGET, self.should_stop, self.commit)
                self.commit(result["next"], 0)
                if result["found"] is not None:
                    self.claim.save_result(result["found"])
                    candidate_path = result.get("found_path")
                    if candidate_path:
                        candidate = Path(candidate_path).resolve()
                        if candidate.is_relative_to(work.resolve()):
                            candidate.unlink(missing_ok=True)
                    self.run_state, self.reason = "FOUND", "发现结果！已验证并保存在本机，等待领奖复核"
                    self.stop_event.set()
                    break
        except Exception as error:
            self.run_state, self.reason = "ERROR", str(error)[:300]
            self.event("alert", "计算已停止：" + self.reason)
        finally:
            if self.run_state not in ("COMPLETE", "FOUND", "ERROR"):
                self.run_state = "PAUSED"
                if self.reason == "正在计算本机独立区间":
                    self.reason = "已停止，进度已保存"
            self.rate = 0
            self.event("info", self.reason)

    def _engine_log(self, line, filename):
        path = self.directory / "work" / filename
        if path.exists() and path.stat().st_size > 2_000_000:
            path.replace(path.with_suffix(".previous.log"))
        with path.open("a", encoding="utf8") as file:
            file.write(line + "\n")

    def snapshot(self):
        with self.lock:
            row = self.store.progress()
            return {"version": VERSION, "target": TARGET, "payout": self.store.config["payout"],
                "config": self.store.config, "plan_ready": self.settings.get("plan_ready", False),
                "machine_name": platform.node(), "cpu_count": os.cpu_count(),
                "run_state": self.run_state, "reason": self.reason, "running": self.is_running(),
                "backend": self.backend, "keys_per_second": self.rate, "hardware": dict(self.gpu),
                "progress": row, "total_keyspace": str(WIDTH),
                "total_checked": self.store.total_checked(),"saved_plans":self.store.saved_plans(),
                "selected_range": [f"{x:X}" for x in config_range(self.store.config)],
                "monitor": self.monitor.snapshot(), "claim": self.claim.snapshot(),
                "price": self.price.snapshot(), "language": self.settings.get("language", "en"),
                "events": self.store.events(), "duration": self.settings.get("duration", 600),
                "remaining_seconds": max(0, self.deadline - time.monotonic()) if self.deadline and self.is_running() else None,
                "data_directory": str(self.directory)}

    def shutdown(self):
        self.claim.disarm()
        self.stop("软件退出，保存进度")
        self.shutdown_event.set()
        self.monitor.stop()
        self.price.stop()
        if self.worker:
            self.worker.join(timeout=10)
