#!/usr/bin/env python3
"""Local FastAPI control plane that reuses the existing registration engine."""
from __future__ import annotations

import collections
import datetime
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import app_config
import jwt_inspect

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import grok_register_ttk as engine

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = Path(__file__).resolve().parent / "index.html"
PROXY_POOL_JS = Path(__file__).resolve().parent / "proxy-pool.js"
PROXY_POOL_CSS = Path(__file__).resolve().parent / "proxy-pool.css"
OUTLOOK_MAILBOX_JS = Path(__file__).resolve().parent / "outlook-mailbox.js"
LOG_LIMIT = 2000

app = FastAPI(title="grok-register WebUI", version="1.2")

_job_lock = threading.Lock()
_job_thread: Optional[threading.Thread] = None
_controller: Any = None
_maintenance_state: Optional[str] = None
_job_state = {
    "running": False,
    "target": 0,
    "success": 0,
    "fail": 0,
    "pending": 0,
    "warnings": 0,
    "uncertain": 0,
    "cancelled": False,
    "started_at": None,
    "finished_at": None,
    "accounts_file": "",
    "error": "",
}

_quality_lock = threading.Lock()
_quality_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "total": 0,
    "completed": 0,
    "healthy": 0,
    "hard": 0,
    "soft": 0,
    "risk": 0,
    "error": 0,
    "current_email": "",
}

_log_lock = threading.Lock()
_log_seq = 0
_logs = collections.deque(maxlen=LOG_LIMIT)
# 日志同时落盘，重启后仍可回看（原先只在内存，重启即丢）。
_log_store = None


def _console_log_dir() -> Path:
    """日志目录。

    默认项目根下的 logs/（与流量计量文件同目录）。允许用环境变量覆盖 ——
    不走 config.json 是因为 validate_config_structure 会拒绝未知键，新增
    配置项必须同步改面板表单，对「日志留久一点」这种诉求过重。
    """
    value = str(os.environ.get("GROK_BATCH_CONSOLE_LOG_DIR") or "").strip()
    if not value:
        return ROOT / "logs"
    base = Path(value).expanduser()
    return base if base.is_absolute() else (ROOT / base).resolve()


def _console_log_retention() -> int:
    try:
        return max(1, int(os.environ.get("GROK_BATCH_CONSOLE_LOG_DAYS") or 14))
    except Exception:
        return 14


def _get_log_store():
    global _log_store
    if _log_store is None:
        try:
            import console_log_store
            _log_store = console_log_store.ConsoleLogStore(
                _console_log_dir(), retention_days=_console_log_retention(),
            )
            try:
                _log_store.prune()
            except Exception:
                pass
        except Exception:
            _log_store = False  # 明确标记不可用，避免反复重试
    return _log_store or None


def _restore_logs_from_disk() -> None:
    """启动时把历史日志回填进内存队列，刷新页面即可看到。"""
    global _log_seq
    store = _get_log_store()
    if store is None:
        return
    try:
        lines = store.read_recent(limit=LOG_LIMIT)
    except Exception:
        return
    with _log_lock:
        for line in lines:
            _log_seq += 1
            _logs.append({"seq": _log_seq, "line": line})


def _append_log(message: str) -> None:
    global _log_seq
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), str(message))
    with _log_lock:
        _log_seq += 1
        _logs.append({"seq": _log_seq, "line": line})
    store = _get_log_store()
    if store is not None:
        try:
            store.write(line)
        except Exception:
            pass


def _state_snapshot() -> dict[str, Any]:
    with _job_lock:
        snapshot = dict(_job_state)
        snapshot["maintenance"] = _maintenance_state
        return snapshot


def _begin_maintenance(kind: str) -> None:
    global _maintenance_state
    with _job_lock:
        if _job_state["running"]:
            raise HTTPException(status_code=409, detail="注册任务运行期间不能执行维护操作")
        if _maintenance_state is not None:
            raise HTTPException(
                status_code=409,
                detail="已有维护操作正在执行: %s" % _maintenance_state,
            )
        _maintenance_state = str(kind)


def _end_maintenance(kind: str) -> None:
    global _maintenance_state
    with _job_lock:
        if _maintenance_state == str(kind):
            _maintenance_state = None


def _load_config_if_idle() -> dict[str, Any]:
    with _job_lock:
        if not _job_state["running"] and _maintenance_state is None:
            engine.load_config()
        return dict(engine.config)


def _new_accounts_file() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return str(ROOT / ("accounts_%s.txt" % stamp))


def _update_progress(batch: Any) -> None:
    with _job_lock:
        _job_state["success"] = int(batch.success_count)
        _job_state["fail"] = int(batch.fail_count)
        _job_state["pending"] = int(batch.registered_unsaved_count)
        _job_state["warnings"] = int(batch.postprocess_warning_count)
        _job_state["uncertain"] = int(getattr(batch, "uncertain_count", 0) or 0)
        _job_state["cancelled"] = bool(batch.cancelled)


def _run_job(count: int, controller: Any, accounts_file: str) -> None:
    global _controller
    try:
        import traffic_meter
        traffic_meter.begin_batch()
        _append_log("[*] 已开启本批代理流量计量")
    except Exception:
        pass
    try:
        batch = engine.run_registration_common(
            count=count,
            log_callback=_append_log,
            cancel_callback=controller.should_stop,
            accounts_output_file=accounts_file,
            observer=lambda batch, _account, _output: _update_progress(batch),
        )
        _update_progress(batch)
        if bool(engine.config.get("quality_auto_probe", False)) and int(batch.success_count) > 0:
            try:
                _append_log("[quality] 注册完成，自动触发降智检测扫描...")
                quality_scan()
            except Exception as q_exc:
                _append_log("[quality] 自动触发降智检测跳过: %s" % q_exc)
    except Exception as exc:
        with _job_lock:
            _job_state["error"] = str(exc)
        _append_log("[!] WebUI 任务异常: %s" % exc)
    finally:
        try:
            import traffic_meter
            success_count = int(_job_state.get("success") or 0)
            if success_count:
                traffic_meter.record_account(success_count)
            t_data = traffic_meter.finish_batch()
            _append_log("[*] 本批代理流量: 上行 %s / 下行 %s / 合计 %s (连接数 %s)" % (
                traffic_meter.format_bytes(t_data.get("bytes_up")),
                traffic_meter.format_bytes(t_data.get("bytes_down")),
                traffic_meter.format_bytes(t_data.get("bytes_total")),
                t_data.get("connections", 0),
            ))
        except Exception:
            pass
        with _job_lock:
            _job_state["running"] = False
            _job_state["finished_at"] = time.time()
            _job_state["cancelled"] = bool(
                _job_state["cancelled"] or controller.should_stop()
            )
            _controller = None
        _append_log("[*] WebUI 任务结束")


@app.get("/", include_in_schema=False)
def index():
    html = INDEX_HTML.read_text(encoding="utf-8")
    if PROXY_POOL_CSS.is_file():
        html = html.replace("</head>", '<link rel="stylesheet" href="./proxy-pool.css">\n</head>', 1)
    if PROXY_POOL_JS.is_file():
        html = html.replace("</body>", '<script src="./proxy-pool.js"></script>\n</body>', 1)
    if OUTLOOK_MAILBOX_JS.is_file():
        html = html.replace("</body>", '<script src="./outlook-mailbox.js"></script>\n</body>', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/proxy-pool.js", include_in_schema=False)
def proxy_pool_js():
    return FileResponse(PROXY_POOL_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/proxy-pool.css", include_in_schema=False)
def proxy_pool_css():
    return FileResponse(PROXY_POOL_CSS, media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/outlook-mailbox.js", include_in_schema=False)
def outlook_mailbox_js():
    return FileResponse(OUTLOOK_MAILBOX_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.middleware("http")
async def protect_outlook_mailbox_api(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/mailboxes/outlook"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _require_local_origin(request: Request) -> None:
    origin = str(request.headers.get("origin") or "").strip()
    if not origin:
        return
    from urllib.parse import urlsplit
    host = (urlsplit(origin).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=403, detail="Outlook 邮箱池只允许本地 WebUI 访问")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def get_config():
    return {"ok": True, "config": _load_config_if_idle()}


@app.put("/api/config")
async def put_config(request: Request):
    updates = await request.json()
    if not isinstance(updates, dict):
        raise HTTPException(status_code=400, detail="配置更新必须是 JSON 对象")

    allowed = set(engine.DEFAULT_CONFIG)
    unknown = sorted(set(updates) - allowed)
    if unknown:
        raise HTTPException(status_code=400, detail="未知配置项: " + ", ".join(unknown))

    with _job_lock:
        if _job_state["running"]:
            raise HTTPException(status_code=409, detail="任务运行期间不能修改配置")
        if _maintenance_state is not None:
            raise HTTPException(status_code=409, detail="维护操作期间不能修改配置")
        engine.load_config()
        candidate = dict(engine.config)
        candidate.update(updates)
        try:
            validated = engine.validate_config_structure(candidate)
        except engine.ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        engine.config.clear()
        engine.config.update(validated)
        engine.save_config()
        result = dict(engine.config)
    return {"ok": True, "config": result}


@app.get("/api/mailboxes/outlook")
def get_outlook_mailboxes(request: Request):
    _require_local_origin(request)
    from outlook_mailbox_pool import load_outlook_mailbox_pool
    cfg = _load_config_if_idle()
    try:
        summary = load_outlook_mailbox_pool(cfg.get("outlook_accounts_file", ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({
        "ok": True,
        "path": summary["path"],
        "data": summary["data"],
        "count": summary["count"],
        "invalid": summary["invalid"],
        "duplicates": summary["duplicates"],
        "accounts": summary["accounts"],
    })


@app.put("/api/mailboxes/outlook")
async def put_outlook_mailboxes(request: Request):
    _require_local_origin(request)
    payload = await request.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), str):
        raise HTTPException(status_code=400, detail="请求必须包含字符串字段 data")
    from outlook_mailbox_pool import save_outlook_mailbox_pool
    with _job_lock:
        if _job_state["running"]:
            raise HTTPException(status_code=409, detail="任务运行期间不能修改 Outlook 邮箱池")
        if _maintenance_state is not None:
            raise HTTPException(status_code=409, detail="维护操作期间不能修改 Outlook 邮箱池")
        engine.load_config()
        path = engine.config.get("outlook_accounts_file", "")
        try:
            summary = save_outlook_mailbox_pool(path, payload["data"])
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    _append_log("[*] Outlook 邮箱池已保存: %s 个账号" % summary["count"])
    return JSONResponse({"ok": True, **summary})


@app.post("/api/mailboxes/outlook/test")
async def test_outlook_mailboxes(request: Request):
    _require_local_origin(request)
    payload = await request.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), str):
        raise HTTPException(status_code=400, detail="请求必须包含字符串字段 data")
    from outlook_mailbox_pool import probe_outlook_mailbox_pool_data

    kind = "outlook_mailbox_test"
    _begin_maintenance(kind)
    try:
        try:
            summary = await run_in_threadpool(probe_outlook_mailbox_pool_data, payload["data"])
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _end_maintenance(kind)
    _append_log(
        "[*] Outlook 邮箱池健康检查完成: %s/%s 个账号可用"
        % (summary["healthy"], summary["count"])
    )
    return JSONResponse({"ok": True, **summary})


@app.get("/api/proxy-pool/status")
def proxy_pool_status():
    from proxy_pool import manager_snapshot
    cfg = _load_config_if_idle()
    return {"ok": True, **manager_snapshot(config=cfg)}


@app.get("/api/traffic")
def traffic_status():
    """本批代理流量计量。

    住宅代理按流量计费，这个接口让面板直接显示用量，避免超支。
    计量在本地代理桥的中继路径上累加，不额外起代理、不记录目标地址。
    """
    try:
        import traffic_meter
    except Exception as exc:
        return {"ok": False, "error": "traffic_meter 不可用: %s" % exc}

    # 计量路径来自配置，而 traffic_meter 通过环境变量取路径。
    # WebUI 进程启动时不一定跑过配置校验，所以这里补一次注入，
    # 否则面板会读不到已落盘的计量文件。
    try:
        cfg = _load_config_if_idle()
        app_config._apply_traffic_env(cfg)
    except Exception:
        pass

    current = traffic_meter.read_metrics()
    history = traffic_meter.read_history()

    # 历史批次的均值，用于预估下一批消耗。
    totals = [int(item.get("bytes_total") or 0) for item in history if item.get("bytes_total")]
    accounts = [int(item.get("accounts") or 0) for item in history if item.get("accounts")]
    average_batch = int(sum(totals) / len(totals)) if totals else 0
    total_accounts = sum(accounts)
    average_account = int(sum(totals) / total_accounts) if total_accounts else 0

    # 累计总量与时间窗口。任何一项失败都不应让整个接口挂掉，
    # 面板宁可不显示也不能白屏。
    def _safe(fn, default):
        try:
            return fn()
        except Exception:
            return dict(default)

    empty = {"batches": 0, "bytes_up": 0, "bytes_down": 0, "bytes_total": 0,
             "connections": 0, "accounts": 0}
    lifetime = _safe(traffic_meter.totals, empty)
    windows = {}
    for hours in (1, 24, 168):
        block = _safe(lambda h=hours: traffic_meter.window(h), empty)
        block["bytes_total_text"] = traffic_meter.format_bytes(block.get("bytes_total"))
        windows["h%d" % hours] = block
    for key in ("bytes_up", "bytes_down", "bytes_total"):
        lifetime.setdefault(key, 0)
    lifetime["bytes_up_text"] = traffic_meter.format_bytes(lifetime.get("bytes_up"))
    lifetime["bytes_down_text"] = traffic_meter.format_bytes(lifetime.get("bytes_down"))
    lifetime["bytes_total_text"] = traffic_meter.format_bytes(lifetime.get("bytes_total"))

    return {
        "ok": True,
        "current": {
            **current,
            "bytes_up_text": traffic_meter.format_bytes(current.get("bytes_up")),
            "bytes_down_text": traffic_meter.format_bytes(current.get("bytes_down")),
            "bytes_total_text": traffic_meter.format_bytes(current.get("bytes_total")),
        },
        "average_batch": average_batch,
        "average_batch_text": traffic_meter.format_bytes(average_batch),
        "average_account": average_account,
        "average_account_text": traffic_meter.format_bytes(average_account),
        "lifetime": lifetime,
        "windows": windows,
        "history": [
            {
                **item,
                "bytes_total_text": traffic_meter.format_bytes(item.get("bytes_total")),
            }
            for item in history[:20]
        ],
    }


@app.get("/api/cpa/status")
def cpa_status():
    """CPA 凭据导出与远程同步状态。

    只读接口：扫描本地导出目录，并在启用远程同步时顺带确认目标可达性。
    远程探测失败不应让整个接口报错 —— 面板仍需展示本地凭据情况。
    """
    cfg = _load_config_if_idle()
    auth_dir = Path(str(cfg.get("cpa_auth_dir") or "./cpa_auths")).expanduser()
    if not auth_dir.is_absolute():
        auth_dir = (Path(engine.__file__).resolve().parent / auth_dir).resolve()

    res_file = auth_dir / "quality_results.json"
    quality_map = {}
    quality_summary = {}
    if res_file.is_file():
        try:
            q_data = json.loads(res_file.read_text(encoding="utf-8"))
            quality_map = q_data.get("results") or {}
            quality_summary = q_data.get("summary") or {}
        except Exception:
            pass

    credentials = []
    for path in sorted(auth_dir.glob("xai-*.json")):
        entry = {"file": path.name, "email": "", "expired": "", "size": 0}
        try:
            stat = path.stat()
            entry["size"] = int(stat.st_size)
            entry["mtime"] = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            data = json.loads(path.read_text(encoding="utf-8"))
            entry["email"] = str(data.get("email") or "")
            entry["expired"] = str(data.get("expired") or "")
            # 本地解码 access_token，检查 xAI 的 bfs 标记（纯本地、无网络）。
            info = jwt_inspect.inspect_token(data.get("access_token"))
            entry["bfs"] = bool(info.get("bfs"))
            entry["bfs_value"] = info.get("bfs_value")
            q = quality_map.get(entry["email"])
            if q:
                entry["quality"] = {
                    "verdict": q.get("verdict"),
                    "reasoning_tokens": q.get("reasoning_tokens", 0),
                    "content": q.get("content", ""),
                    "error": q.get("error", ""),
                }
        except Exception as exc:
            entry["error"] = str(exc)
        credentials.append(entry)

    failed_file = auth_dir / "cpa_auth_failed.txt"
    failed = []
    if failed_file.is_file():
        # 失败记录是追加写的流水账，补导出成功后旧记录仍在。只展示
        # "至今仍无凭据" 的邮箱，否则面板会误报已解决的问题。
        succeeded = {str(item.get("email") or "").strip().lower() for item in credentials}
        seen_failed = set()
        try:
            for line in failed_file.read_text(encoding="utf-8").splitlines():
                parts = line.split("----")
                if not parts or not parts[0].strip():
                    continue
                email = parts[0].strip()
                key = email.lower()
                if key in succeeded or key in seen_failed:
                    continue
                # 旧记录可能含换行残片，只保留形似邮箱的条目
                if "@" not in email:
                    continue
                seen_failed.add(key)
                failed.append({
                    "email": email,
                    "error": parts[1].strip() if len(parts) > 1 else "",
                })
        except Exception:
            pass

    sync = {
        "enabled": bool(cfg.get("cpa_sync_enabled", False)),
        "target": str(cfg.get("cpa_sync_target") or ""),
        "auth_dir": str(cfg.get("cpa_sync_auth_dir") or ""),
        "reachable": None,
        "error": "",
    }
    if sync["enabled"] and sync["target"] and sync["auth_dir"]:
        try:
            import cpa_sync
            cpa_sync.check_target(
                sync["target"], sync["auth_dir"],
                use_sudo=bool(cfg.get("cpa_sync_use_sudo", True)),
                log=lambda _message: None,
            )
            sync["reachable"] = True
        except Exception as exc:
            sync["reachable"] = False
            sync["error"] = str(exc)

    return {
        "ok": True,
        "export_enabled": bool(cfg.get("cpa_export_enabled", False)),
        "auth_dir": str(auth_dir),
        "count": len(credentials),
        "credentials": credentials,
        "failed": failed,
        "sync": sync,
        "bfs_flagged": sum(1 for item in credentials if item.get("bfs")),
        "quality_summary": quality_summary,
        "quality_scanning": bool(_quality_state["running"]),
        "quality_state": dict(_quality_state),
    }


@app.post("/api/proxy-pool/reload")
def proxy_pool_reload():
    from proxy_pool import get_manager
    kind = "proxy_reload"
    _begin_maintenance(kind)
    try:
        engine.load_config()
        try:
            cfg = engine.validate_config_structure(dict(engine.config))
            manager = get_manager(config=cfg, log=_append_log)
            snapshot = manager.reload_sources(force=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _end_maintenance(kind)
    _append_log("[*] 代理池已重新加载")
    return {"ok": True, **snapshot}


@app.post("/api/proxy-pool/novproxy")
def proxy_pool_novproxy(
    num: Optional[int] = Query(None, ge=1, le=500),
    minutes: Optional[int] = Query(None, ge=1, le=1440),
    region: Optional[str] = Query(None, min_length=2, max_length=8),
    expect: Optional[str] = Query(None, min_length=2, max_length=8),
):
    """从 NovProxy 提取一批住宅节点并写入节点文件。

    提取到的节点会逐个探测真实出口（国家/是否机房），只有通过校验的
    才入池。num 默认读取 novproxy_num 或 register_count（实现一账号一 IP）。
    """
    kind = "proxy_novproxy"
    _begin_maintenance(kind)
    try:
        import novproxy
        engine.load_config()
        try:
            cfg = engine.validate_config_structure(dict(engine.config))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        out_path = str(cfg.get("proxy_pool_file") or "./novproxy_nodes.txt")
        api_base = str(cfg.get("novproxy_api") or novproxy.DEFAULT_API)
        actual_num = int(num or cfg.get("novproxy_num") or cfg.get("register_count") or 5)
        actual_minutes = int(minutes or cfg.get("novproxy_minutes") or 120)
        actual_region = str(region or cfg.get("novproxy_region") or "US")
        actual_expect = str(expect or cfg.get("us_consistency_expect_country") or "US")
        try:
            nodes = novproxy.generate(
                api_base, out_path, region=actual_region, want=actual_num,
                minutes=actual_minutes, expect_country=actual_expect, log=_append_log,
            )
            novproxy.write_nodes(out_path, nodes, log=_append_log)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="NovProxy 提取失败: %s" % exc) from exc
        # 新节点入池后立刻重载，面板马上能看到。
        try:
            from proxy_pool import get_manager
            manager = get_manager(config=cfg, log=_append_log)
            snapshot = manager.reload_sources(force=True)
        except Exception:
            snapshot = {}
    finally:
        _end_maintenance(kind)
    _append_log("[*] NovProxy 已提取 %s 个节点 → %s" % (len(nodes), out_path))
    return {
        "ok": True,
        "count": len(nodes),
        "out_path": out_path,
        "nodes": [novproxy.mask_node(n) for n in nodes],
        **snapshot,
    }


@app.get("/api/quality/status")
def quality_status():
    """获取降智检测状态及历史探测汇总。"""
    cfg = _load_config_if_idle()
    auth_dir = Path(str(cfg.get("cpa_auth_dir") or "./cpa_auths")).expanduser()
    if not auth_dir.is_absolute():
        auth_dir = (Path(engine.__file__).resolve().parent / auth_dir).resolve()
    res_file = auth_dir / "quality_results.json"
    summary = {}
    results = {}
    updated_at = ""
    if res_file.is_file():
        try:
            q_data = json.loads(res_file.read_text(encoding="utf-8"))
            summary = q_data.get("summary") or {}
            results = q_data.get("results") or {}
            updated_at = str(q_data.get("updated_at") or "")
        except Exception:
            pass
    with _quality_lock:
        scan_state = dict(_quality_state)
    return {
        "ok": True,
        "scanning": scan_state["running"],
        "scan_state": scan_state,
        "summary": summary,
        "results": results,
        "updated_at": updated_at,
    }


@app.post("/api/quality/scan")
def quality_scan():
    """触发全量 CPA 凭据账号的降智检测后台扫描。"""
    global _quality_state
    with _quality_lock:
        if _quality_state["running"]:
            raise HTTPException(status_code=409, detail="降智扫描正在进行中，请等待完成")
        _quality_state.update({
            "running": True,
            "started_at": time.time(),
            "finished_at": None,
            "total": 0,
            "completed": 0,
            "healthy": 0,
            "hard": 0,
            "soft": 0,
            "risk": 0,
            "error": 0,
            "current_email": "",
            "error_msg": "",
        })

    def _task():
        import quality_probe as qp
        try:
            _append_log("[quality] 开始执行全量账号降智质量扫描...")
            cfg = _load_config_if_idle()
            auth_dir = Path(str(cfg.get("cpa_auth_dir") or "./cpa_auths")).expanduser()
            if not auth_dir.is_absolute():
                auth_dir = (Path(engine.__file__).resolve().parent / auth_dir).resolve()
            records = qp.load_credentials(str(auth_dir))
            with _quality_lock:
                _quality_state["total"] = len(records)
            _append_log("[quality] 共发现 %d 个待测凭据" % len(records))

            res_file = auth_dir / "quality_results.json"
            saved_map = {}
            if res_file.is_file():
                try:
                    saved_map = json.loads(res_file.read_text(encoding="utf-8")).get("results") or {}
                except Exception:
                    pass

            results = []
            soft_thresh = int(cfg.get("quality_soft_threshold") or 50)
            for idx, rec in enumerate(records, 1):
                email = rec.get("email") or "?"
                with _quality_lock:
                    _quality_state["current_email"] = email
                _append_log("[%d/%d] 正在探测账号质量: %s ..." % (idx, len(records), email))
                res = qp.probe_account(rec, timeout=50.0, stream=True, allow_refresh=True)
                res["verdict"] = qp.classify(res.get("reasoning_tokens", 0), soft_threshold=soft_thresh)
                res["probed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                results.append(res)
                saved_map[email] = res
                with _quality_lock:
                    _quality_state["completed"] = idx
                    v = res.get("verdict", "error")
                    if v in _quality_state:
                        _quality_state[v] += 1
                line_desc = qp._format_line(res)
                _append_log("[quality] %s (耗时 %.1fs)" % (line_desc, res.get("duration_sec", 0)))

            summary = qp.summarize(results)
            payload = {
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "summary": summary,
                "results": saved_map,
            }
            tmp_file = res_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_file.replace(res_file)
            _append_log(
                "[*] 降智扫描完成！健康: %d, 降智: %d, 可疑: %d, 不可用: %d, 错误: %d"
                % (summary["healthy"], summary["hard"], summary["soft"], summary["risk"], summary["error"])
            )
        except Exception as exc:
            with _quality_lock:
                _quality_state["error_msg"] = str(exc)
            _append_log("[quality] 扫描异常终止: %s" % exc)
        finally:
            with _quality_lock:
                _quality_state["running"] = False
                _quality_state["finished_at"] = time.time()
                _quality_state["current_email"] = ""

    thread = threading.Thread(target=_task, name="quality-scan-thread", daemon=True)
    thread.start()
    return {"ok": True, "message": "降智扫描已在后台启动"}


@app.post("/api/proxy-pool/test")
def proxy_pool_test():
    from proxy_pool import get_manager
    kind = "proxy_test"
    _begin_maintenance(kind)
    try:
        engine.load_config()
        try:
            cfg = engine.validate_config_structure(dict(engine.config))
            manager = get_manager(config=cfg, log=_append_log)
            manager.reload_sources(force=True)
            results = manager.probe_all(force=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _end_maintenance(kind)
    _append_log("[*] 代理池测试完成: %s 个节点" % len(results))
    return {"ok": True, "results": results, **manager.snapshot()}


@app.post("/api/proxy-pool/preflight")
def proxy_pool_preflight(node_id: str = Query(..., min_length=1)):
    from proxy_pool import get_manager
    kind = "proxy_preflight"
    _begin_maintenance(kind)
    try:
        engine.load_config()
        try:
            cfg = engine.validate_config_structure(dict(engine.config))
            if not cfg.get("proxy_pool_preflight_enabled", True):
                raise HTTPException(status_code=409, detail="注册路径预检已在配置中关闭")
            manager = get_manager(config=cfg, log=_append_log)
            result = manager.preflight_node(node_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _end_maintenance(kind)
    _append_log("[*] 代理节点注册路径预检完成: %s" % node_id)
    return {"ok": True, "result": result, **manager.snapshot()}


@app.get("/api/status")
def status():
    return {"ok": True, **_state_snapshot()}


@app.on_event("startup")
def _on_startup() -> None:
    """启动时回填历史日志，使面板刷新后仍能看到上次运行的记录。"""
    _restore_logs_from_disk()


@app.get("/api/logs")
def logs(after: int = Query(default=0, ge=0)):
    with _log_lock:
        entries = [dict(item) for item in _logs if int(item["seq"]) > int(after)]
        latest = int(_log_seq)
    return {"ok": True, "latest": latest, "entries": entries}


@app.get("/api/logs/export")
def logs_export():
    """把持久化的控制台日志作为附件下载，便于离线排查。"""
    store = _get_log_store()
    if store is None:
        raise HTTPException(status_code=503, detail="日志持久化不可用")
    try:
        lines = store.read_recent(limit=0)  # 0 = 不截断
    except Exception as exc:
        raise HTTPException(status_code=500, detail="读取日志失败: %s" % exc)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return JSONResponse(
        {"ok": True, "count": len(lines), "generated_at": stamp, "lines": lines},
        headers={"Content-Disposition": 'attachment; filename="grok-console-%s.log"' % stamp},
    )


@app.post("/api/start")
def start():
    global _job_thread, _controller

    with _job_lock:
        if _job_state["running"]:
            raise HTTPException(status_code=409, detail="已有注册任务正在运行")
        if _maintenance_state is not None:
            raise HTTPException(status_code=409, detail="维护操作进行中，暂不能启动注册: %s" % _maintenance_state)

        engine.load_config()
        try:
            validated = engine.validate_run_requirements(dict(engine.config))
        except engine.ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        engine.config.clear()
        engine.config.update(validated)

        count = engine.resolve_registration_count(
            int(engine.config["register_count"]), log_callback=_append_log
        )
        controller = engine.CliStopController()
        accounts_file = _new_accounts_file()

        _job_state.update({
            "running": True,
            "target": count,
            "success": 0,
            "fail": 0,
            "pending": 0,
            "warnings": 0,
            "uncertain": 0,
            "cancelled": False,
            "started_at": time.time(),
            "finished_at": None,
            "accounts_file": accounts_file,
            "error": "",
        })
        _controller = controller
        thread = threading.Thread(
            target=_run_job,
            args=(count, controller, accounts_file),
            name="grok-register-web-job",
            daemon=True,
        )
        _job_thread = thread
        try:
            thread.start()
        except Exception:
            _job_state["running"] = False
            _job_state["finished_at"] = time.time()
            _controller = None
            _job_thread = None
            raise

    _append_log("[*] WebUI 启动注册任务，目标数量: %s" % count)
    return {"ok": True, "started": True, "target": count, "accounts_file": accounts_file}


@app.post("/api/stop")
def stop():
    with _job_lock:
        controller = _controller
        running = bool(_job_state["running"])
    if not running or controller is None:
        return {"ok": True, "stopped": False}
    controller.stop()
    _append_log("[!] WebUI 已发送停止请求")
    return {"ok": True, "stopped": True}


def main() -> None:
    import uvicorn

    uvicorn.run("web.server:app", host="127.0.0.1", port=8092, workers=1)


if __name__ == "__main__":
    main()
