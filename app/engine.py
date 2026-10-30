import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time

from embit import base58, ec, hashes

from .core import CREATE_NO_WINDOW
from .cuda import engine_path

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SPEED = re.compile(r"([0-9.]+)\s*MKey/s")
TOTAL = re.compile(r"\(([0-9,]+) total\)")


def hardware():
    try:
        p = subprocess.run(["nvidia-smi", "--id=0",
            "--query-gpu=name,driver_version,temperature.gpu,utilization.gpu,memory.used,power.draw",
            "--format=csv,noheader,nounits"], capture_output=True, text=True,
            timeout=4, creationflags=CREATE_NO_WINDOW)
        values = [s.strip() for s in p.stdout.strip().split(",")]
        if p.returncode or len(values) != 6:
            return {"available": False}
        return dict(zip(("name", "driver", "temperature", "utilization", "memory_mb", "power_w"), values),
                    available=True)
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False}


def detect_gpu():
    try:
        tool = engine_path()
    except (OSError, RuntimeError) as error:
        return {"available": False, "reason": str(error)}
    try:
        p = subprocess.run([str(tool), "--list-devices"], capture_output=True, text=True,
                           timeout=8, creationflags=CREATE_NO_WINDOW)
        if p.returncode or "ID:" not in p.stdout:
            return {"available": False, "reason": "未找到兼容 CUDA 设备；将使用 CPU"}
        # nvidia-smi telemetry is optional and must not override successful
        # CUDA device detection (headless installations may not include it).
        telemetry = hardware()
        telemetry.pop("available", None)
        return {**telemetry, "available": True, "details": p.stdout.strip()}
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "reason": "CUDA 检测未完成；将使用 CPU"}


def _next_point(x, y):
    if x == GX:
        if (y + GY) % P == 0:
            raise ValueError("curve infinity")
        slope = 3 * x * x * pow(2 * y, -1, P) % P
    else:
        slope = (GY - y) * pow((GX - x) % P, -1, P) % P
    nx = (slope * slope - x - GX) % P
    return nx, (slope * (x - nx) - y) % P


def cpu_scan(start, end, target, should_stop, progress):
    target_hash = base58.decode_check(target)[1:]
    key = ec.PrivateKey(start.to_bytes(32, "big"), compressed=False)
    sec = key.get_public_key().sec()
    x, y = int.from_bytes(sec[1:33], "big"), int.from_bytes(sec[33:], "big")
    scalar, last_key, last_time = start, start, time.monotonic()
    while scalar <= end:
        public_key = bytes([2 | (y & 1)]) + x.to_bytes(32, "big")
        # embit supplies a RIPEMD-160 fallback when OpenSSL 3 does not expose
        # the legacy provider (notably minimal Ubuntu 22.04 installations).
        candidate_hash = hashes.hash160(public_key)
        if candidate_hash == target_hash:
            return {"next": scalar, "found": scalar, "backend": "CPU"}
        scalar += 1
        if scalar & 511 == 0:
            current = time.monotonic()
            if current - last_time >= 0.5:
                progress(scalar, (scalar - last_key) / (current - last_time))
                last_key, last_time = scalar, current
            if should_stop():
                progress(scalar, 0)
                return {"next": scalar, "found": None, "backend": "CPU"}
        if scalar <= end:
            x, y = _next_point(x, y)
    progress(end + 1, 0)
    return {"next": end + 1, "found": None, "backend": "CPU"}


def gpu_scan(start, end, target, directory, should_stop, progress, log, parameters=(64, 128, 128)):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    found = directory / ("candidate-" + str(time.time_ns()) + ".found.txt")
    b, t, points = parameters
    command = [str(engine_path()), "-d", "0", "-b", str(b), "-t", str(t),
               "-p", str(points), "-c", "-f", "--keyspace", f"{start:X}:{end:X}",
               "-o", str(found), target]
    if os.name == "nt":
        from winpty import PtyProcess
        console = PtyProcess.spawn(command, cwd=str(directory), dimensions=(30, 240))
    else:
        from .terminal import PosixTerminal
        console = PosixTerminal(command, str(directory))
    pending, exited, code = queue.Queue(), None, None
    next_key, reached_end, was_stopped = start, False, False
    stop_started = None

    def reader():
        buffer = ""
        try:
            while True:
                text = console.read(4096)
                for char in text:
                    if char in "\r\n":
                        if buffer:
                            pending.put(ANSI.sub("", buffer))
                            buffer = ""
                    else:
                        buffer += char
        except (EOFError, OSError):
            if buffer:
                pending.put(ANSI.sub("", buffer))
            pending.put(None)

    threading.Thread(target=reader, name="cuda-output", daemon=True).start()
    try:
        while True:
            alive = console.isalive()
            if alive and not was_stopped and should_stop():
                was_stopped = True
                stop_started = time.monotonic()
                try:
                    os.kill(console.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if alive and os.name != "nt" and stop_started and time.monotonic() - stop_started > 3:
                try:
                    os.killpg(console.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if not alive:
                if exited is None:
                    exited = time.monotonic()
                elif time.monotonic() - exited > 1 and pending.empty():
                    break
            try:
                line = pending.get(timeout=0.15)
            except queue.Empty:
                continue
            if line is None:
                break
            # With -o the tool logs only the destination filename, not the found scalar.
            log(line)
            reached_end |= "Reached end of keyspace" in line
            speed, total = SPEED.search(line), TOTAL.search(line)
            if total:
                candidate_next = start + int(total.group(1).replace(",", ""))
                if not start <= candidate_next <= end + 1:
                    raise ValueError("CUDA 进度越界")
                next_key = max(next_key, candidate_next)
                progress(next_key, float(speed.group(1)) * 1_000_000 if speed else 0)
        code = console.exitstatus
    finally:
        if os.name == "nt":
            if console.isalive():
                os.kill(console.pid, signal.SIGTERM)
            console.close(force=True)
            try:
                console.fileobj.shutdown(2)
            except OSError:
                pass
            console.fileobj.close()
            console._server.close()
        else:
            console.close()
        if code is None and os.name != "nt":
            code = console.exitstatus
    scalar = None
    if found.exists():
        for line in found.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == target:
                possible = int(fields[1], 16)
                if start <= possible <= end:
                    scalar = possible
                    break
    if scalar is None and not was_stopped and code != 0:
        raise RuntimeError("CUDA 引擎未正常结束，将保留已确认进度")
    if reached_end and code == 0 and scalar is None:
        next_key = end + 1
        progress(next_key, 0)
    return {"next": next_key, "found": scalar, "backend": "CUDA",
            "found_path": str(found) if scalar is not None else None, "pid": console.pid}


def gpu_selftest(directory, stop, log):
    started = time.monotonic()
    result = gpu_scan(0x80000, 0xFFFFF, "1HsMJxNiV7TLxmoF6uJNkydxPFDog4NQum",
        directory, lambda: stop.is_set() or time.monotonic() - started > 45,
        lambda *_: None, log, (32, 128, 16))
    if result["found"] != 0xD2C55:
        raise RuntimeError("CUDA 已解题校验失败；请查看自校验日志")
    if result.get("found_path"):
        # This is only the already-public solved-puzzle test answer.
        Path(result["found_path"]).unlink(missing_ok=True)
    return True
