"""批次代理流量计量。

为什么需要
----------
住宅代理按流量计费，跑批量注册时最关心的就是「这批用了多少」。但代理
凭据会被脱敏，直接看日志看不出用量。

做法
----
不额外起计量代理，而是在已有的本地代理桥（mooproxy_bridge）中继路径上
累加字节数。桥本来就要转发每一个字节，所以零额外开销、零额外端口。

只记录字节数与连接数，**不记录**目标域名、URL 或代理地址。

用法：
    from traffic_meter import begin_batch, record, snapshot, finish_batch

    begin_batch()                     # 批次开始（可重复调用，幂等）
    record("up", 1234)                # 中继路径上调用
    print(snapshot())                 # {'bytes_up':…, 'bytes_down':…, …}
    finish_batch()                    # 批次结束，落盘并归档历史

落盘位置由 GROK_BATCH_TRAFFIC_FILE 指定；未设置时仅内存计数，
不影响任何既有行为。
"""
from __future__ import annotations

import atexit
import datetime
import json
import os
import threading
import time
from pathlib import Path

TRAFFIC_FILE_ENV = "GROK_BATCH_TRAFFIC_FILE"
HISTORY_FILE_ENV = "GROK_BATCH_TRAFFIC_HISTORY_FILE"
HISTORY_LIMIT = 200

_LOCK = threading.RLock()
_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "bytes_up": 0,
    "bytes_down": 0,
    "connections": 0,
    "accounts": 0,
}


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _traffic_path():
    value = str(os.environ.get(TRAFFIC_FILE_ENV) or "").strip()
    return Path(value) if value else None


def _history_path():
    value = str(os.environ.get(HISTORY_FILE_ENV) or "").strip()
    return Path(value) if value else None


def _write_json(path, payload):
    """原子写，避免面板读到半截 JSON。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(path))
        try:
            os.chmod(str(path), 0o600)
        except Exception:
            pass
    except Exception:
        pass


def snapshot():
    """当前计量快照。任何时候都可安全调用。"""
    with _LOCK:
        data = dict(_STATE)
    data["bytes_total"] = int(data.get("bytes_up", 0)) + int(data.get("bytes_down", 0))
    return data


def begin_batch():
    """开始新批次。已有批次仍在跑时不重置，避免丢失计数。"""
    with _LOCK:
        if _STATE["running"]:
            return snapshot()
        _STATE.update({
            "running": True,
            "started_at": _now(),
            "finished_at": None,
            "bytes_up": 0,
            "bytes_down": 0,
            "connections": 0,
            "accounts": 0,
        })
        data = snapshot()
    _flush(data)
    return data


def record(direction, nbytes):
    """累加一个中继块的字节数。热路径，必须极快且不抛异常。"""
    try:
        count = int(nbytes)
    except Exception:
        return
    if count <= 0:
        return
    with _LOCK:
        if direction == "up":
            _STATE["bytes_up"] += count
        else:
            _STATE["bytes_down"] += count


def count_connection():
    with _LOCK:
        _STATE["connections"] += 1


def count_account(count=1):
    with _LOCK:
        _STATE["accounts"] += max(0, int(count))


def _flush(data=None):
    path = _traffic_path()
    if path is None:
        return
    _write_json(path, data if data is not None else snapshot())


def flush():
    """把当前计数落盘（面板轮询用）。"""
    _flush()


def finish_batch():
    """结束批次：落盘并追加一条历史记录。"""
    with _LOCK:
        if not _STATE["running"] and _STATE["started_at"] is None:
            return snapshot()
        _STATE["running"] = False
        _STATE["finished_at"] = _now()
        data = snapshot()
    _flush(data)
    _append_history(data)
    return data


def _append_history(data):
    path = _history_path()
    if path is None:
        return
    entry = {
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
        "bytes_up": data.get("bytes_up", 0),
        "bytes_down": data.get("bytes_down", 0),
        "bytes_total": data.get("bytes_total", 0),
        "connections": data.get("connections", 0),
        "accounts": data.get("accounts", 0),
    }
    try:
        history = []
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    history = loaded
                elif isinstance(loaded, dict) and isinstance(loaded.get("batches"), list):
                    history = loaded["batches"]
            except Exception:
                history = []
        history.append(entry)
        _write_json(path, {"batches": history[-HISTORY_LIMIT:]})
    except Exception:
        pass


def read_history():
    """读取历史批次，返回列表（新到旧）。"""
    path = _history_path()
    if path is None or not path.is_file():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(loaded, dict):
        loaded = loaded.get("batches") or []
    if not isinstance(loaded, list):
        return []
    return list(reversed(loaded))


def read_metrics():
    """面板用：读取计量数据。

    批次进行中时以内存为准 —— 内存是实时值，落盘只在批次开始/结束和
    显式 flush 时发生。若优先读文件，面板会显示陈旧数据（例如刚跑完
    一批却仍显示 0）。
    """
    with _LOCK:
        live = _STATE["running"] or _STATE["bytes_up"] or _STATE["bytes_down"]
    if live:
        return snapshot()
    path = _traffic_path()
    if path is not None and path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                loaded.setdefault("bytes_total",
                                  int(loaded.get("bytes_up", 0)) + int(loaded.get("bytes_down", 0)))
                return loaded
        except Exception:
            pass
    return snapshot()


def format_bytes(value):
    """人类可读的字节数。"""
    try:
        size = float(value)
    except Exception:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0:
            return ("%.0f %s" if unit == "B" else "%.2f %s") % (size, unit)
        size /= 1024.0
    return "%.2f PB" % size


@atexit.register
def _close_on_exit():
    try:
        with _LOCK:
            if _STATE["running"]:
                _STATE["running"] = False
                _STATE["finished_at"] = _now()
                data = snapshot()
            else:
                data = None
        if data is not None:
            _flush(data)
            _append_history(data)
    except Exception:
        pass
