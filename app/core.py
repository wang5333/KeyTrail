import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
import uuid

from embit import base58, ec, script
from embit.networks import NETWORKS

APP_ID = "puzzle71-desk"
VERSION = "1.3.0"
TARGET = "1PWo3JeB9jrGwfHDNpdGK54CRas7fsVzXU"
PAYOUT = ""  # Receiving addresses belong only in each machine's local config.
START, END = 1 << 70, (1 << 71) - 1
WIDTH = END - START + 1
QUANTUM = 64 * 128 * 128  # All complete CUDA batches are exactly 2^20 keys.
CHUNK = QUANTUM * 65536
TEMPERATURE_LIMIT = 82.0
TOOL_HASH = "0eecdbfabaaef36f497e944a6ceb468d01824f3ae6457b4ae4b3ac8e95eebb55"
RUNTIME_HASH = "111f216aef35f45086888c3f0a30bb9ab48e2b333daeddafd3a76be037a22a6e"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
TARGET_HASH = base58.decode_check(TARGET)[1:]


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf8")
    temp.replace(path)


def point_address(private_key):
    return script.p2pkh(ec.PrivateKey(private_key.to_bytes(32, "big")).get_public_key()).address()


def validate_payout(value=PAYOUT, chain="main"):
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("Invalid Bitcoin address")
    try:
        out = script.address_to_scriptpubkey(value)
        canonical = out.address(NETWORKS[chain])
    except Exception as error:
        raise ValueError("Invalid Bitcoin address") from error
    if canonical != value and not (value.lower().startswith(("bc1", "bcrt1")) and canonical == value.lower()):
        raise ValueError("Wrong Bitcoin network")
    return out


def partition(total, index, start=START, end=END):
    if isinstance(total, bool) or isinstance(index, bool):
        raise ValueError("电脑数量或编号错误")
    if not 1 <= total <= 1024 or not 1 <= index <= total:
        raise ValueError("电脑数量为 1–1024，编号为 1–总台数")
    if not START <= start <= end <= END or end-start+1 < total:
        raise ValueError("Range must be within puzzle #71 and contain at least one key per computer")
    width = end-start+1
    return start + width * (index-1)//total, start + width*index//total-1


def parse_range(start, end):
    def parse(value):
        if not isinstance(value, str) or not value or len(value)>20:
            raise ValueError("Enter hexadecimal range bounds")
        value = value.removeprefix("0x").removeprefix("0X")
        if not value or any(c not in "0123456789abcdefABCDEF" for c in value):
            raise ValueError("Enter hexadecimal range bounds")
        return int(value,16)
    begin, finish = parse(start), parse(end)
    partition(1,1,begin,finish)
    return begin, finish


def config_range(config):
    return (START, END) if config["schema"]==1 else parse_range(config["range_start"],config["range_end"])


def config_digest(config):
    fields = {k: config[k] for k in ("schema", "plan_id", "total", "index", "target", "payout")}
    if config["schema"]==2:
        fields.update(range_start=config["range_start"],range_end=config["range_end"])
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def make_config(total=1, index=1, plan_id=None, *, start=None, end=None, payout=PAYOUT):
    begin, finish = (START, END) if start is None and end is None else parse_range(start,end)
    partition(int(total), int(index), begin, finish)
    config = {
        "schema": 1, "plan_id": plan_id or uuid.uuid4().hex,
        "total": int(total), "index": int(index),
        "target": TARGET, "payout": payout,
    }
    if payout != "":
        validate_payout(payout)
    if start is not None:
        config.update(schema=2,range_start=f"{begin:X}",range_end=f"{finish:X}")
    config["digest"] = config_digest(config)
    return config


def validate_config(config):
    if not isinstance(config, dict) or config.get("schema") not in (1,2):
        raise ValueError("配置文件版本错误")
    if config.get("target") != TARGET:
        raise ValueError("本软件仅接受固定 #71 目标和已核对的收款地址")
    plan_id = config.get("plan_id", "")
    if not isinstance(plan_id, str) or len(plan_id) != 32:
        raise ValueError("计划编号错误")
    int(plan_id, 16)
    if type(config.get("total")) is not int or type(config.get("index")) is not int:
        raise ValueError("电脑编号必须为整数")
    partition(config["total"], config["index"],*config_range(config))
    if config.get("digest") != config_digest(config):
        raise ValueError("配置校验失败，请重新从主计划导出")
    if config["payout"] != "":
        validate_payout(config["payout"])
    return config


def profile_id(config):
    legacy = config["plan_id"] + "-" + str(config["index"])
    if config["schema"]==1:
        return legacy
    begin,end=partition(config["total"],config["index"],*config_range(config))
    return legacy+"-"+hashlib.sha256(f"{begin}:{end}".encode()).hexdigest()[:16]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return time.time()
