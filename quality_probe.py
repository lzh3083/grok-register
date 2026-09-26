#!/usr/bin/env python
"""账号降智检测：用推理 token 数判断账号是否被"降智"。

## 背景

xAI 会对部分账号做静默降级：接口照常返回 200，账号看起来完全正常，
但模型不再做逐步推理，直接给答案。这类账号在注册环节查不出任何异常
（botFlag 也是干净的），只有真正发一次需要推理的请求才能暴露。

## 判定依据

实测发现，`/chat/completions` 的 `usage.completion_tokens_details`
里有官方字段 `reasoning_tokens`：

    健康账号   reasoning_tokens ≈ 3000 ~ 8100
    降智账号   reasoning_tokens ≈ 0

这个字段由服务端直接给出，比自己用 `total - prompt - completion` 去
推算可靠（直连时 completion_tokens 只算可见正文，差值里混着其他项）。

注意：**不能**用"有没有 thinking 字段"来判断。实测
`cli-chat-proxy.grok.com` 在直连时既不返回 `reasoning_content` 也不
返回 `thinking`，但 reasoning_tokens 是有的；而经 CPA 中转时
`reasoning_content` 才会透出。只看字段存在性会把所有账号误判成降智。

## 判据

    0            → hard    （降智）
    1 ~ 阈值以下  → soft    （可疑，推理量异常少）
    阈值以上      → healthy

默认阈值 200：正常推理动辄几千 token，低于 200 基本可以认定没有在
真正思考，同时留出余量避免把"简单问题少推理"误判。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# 需要逐步推理的问题；答案唯一且可校验（3:27 时较小夹角 = 58.5 ≈ 58）。
DEFAULT_PROMPT = (
    "Think step by step. A clock shows 3:27. What is the smaller angle in "
    "degrees between the hour and minute hands? Reply with only the integer."
)
# 实测 grok-4.5 流式首包与完成仅需 6~15 秒（grok-4.7 需 35~50 秒以上易超时），
# 且通过 stream_options.include_usage 能稳定返回 reasoning_tokens（实测 300~1000+）。
DEFAULT_MODEL = "grok-4.5"
DEFAULT_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
CHAT_PATH = "/chat/completions"
# 与 cpa_xai.oauth_device 保持一致：刷新 token 时必须带同一个 client_id。
DEFAULT_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"

# 低于此推理量视为可疑。正常账号实测 250~1000+。
SOFT_REASONING_TOKENS = 50
# 判定阈值：reasoning_tokens 为 0 直接判降智。
HARD_REASONING_TOKENS = 0

VERDICT_HEALTHY = "healthy"
VERDICT_SOFT = "soft"
VERDICT_HARD = "hard"
VERDICT_RISK = "risk"
VERDICT_ERROR = "error"

# 账号本身有问题（token 失效 / 无权限 / 被限流），与降智区分开。
ACCOUNT_ERROR_MARKERS = (
    "permission-denied", "permission denied", "forbidden", "invalid token",
    "expired", "no auth", "quota", "rate limit", "ratelimit",
    "too many requests", "unauthorized",
)

# 对齐 @xai-official/grok CLI 的客户端身份头。缺失会被判为过期客户端
# 而返回 426。
CLIENT_HEADERS = {
    "User-Agent": "grok-pager/0.2.93 grok-shell/0.2.93 (linux; x86_64)",
    "X-XAI-Token-Auth": "xai-grok-cli",
    "x-authenticateresponse": "authenticate-response",
    "x-grok-client-identifier": "grok-pager",
    "x-grok-client-version": "0.2.93",
}


def _log(message):
    print("  %s" % message, flush=True)


def _int_field(payload, *keys):
    if not isinstance(payload, dict):
        return 0
    for key in keys:
        try:
            value = int(payload.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value:
            return value
    return 0


def extract_reasoning_tokens(usage):
    """从 usage 里取推理 token 数。

    优先用官方字段；取不到时回退到 total - prompt - completion 的差值
    （部分中转只给 total，差值同样是推理量）。
    """
    if not isinstance(usage, dict):
        return 0
    details = usage.get("completion_tokens_details")
    direct = _int_field(details, "reasoning_tokens", "reasoningTokens")
    if direct:
        return direct
    total = _int_field(usage, "total_tokens", "totalTokens")
    prompt = _int_field(usage, "prompt_tokens", "promptTokens")
    completion = _int_field(usage, "completion_tokens", "completionTokens")
    delta = total - prompt - completion
    return delta if delta > 0 else 0


def classify(reasoning_tokens, soft_threshold=SOFT_REASONING_TOKENS):
    """按推理量给账号定级。"""
    try:
        value = int(reasoning_tokens)
    except (TypeError, ValueError):
        value = 0
    if value <= HARD_REASONING_TOKENS:
        return VERDICT_HARD
    if value < int(soft_threshold):
        return VERDICT_SOFT
    return VERDICT_HEALTHY


def classify_failure(status, body):
    """HTTP 失败归类：账号问题 vs 传输/上游问题。"""
    lower = str(body or "").lower()
    if status in (401, 403, 400, 404, 409, 422, 429):
        return VERDICT_RISK
    for marker in ACCOUNT_ERROR_MARKERS:
        if marker in lower:
            return VERDICT_RISK
    return VERDICT_ERROR


def _chat_url(base_url):
    base = str(base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith(CHAT_PATH):
        return base
    return base + CHAT_PATH


def build_payload(model=DEFAULT_MODEL, prompt=DEFAULT_PROMPT, max_tokens=60, stream=True):
    payload = {
        "model": model,
        "stream": bool(stream),
        "max_tokens": int(max_tokens),
        "messages": [{"role": "user", "content": prompt}],
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


def refresh_access_token(record, timeout=30.0, urlopen=None):
    """用 refresh_token 换新的 access_token 并保存回磁盘。失败返回 None。

    这一步不能省：凭据文件里的 access_token 有效期只有 6 小时，过期的
    账号直接拿去探测会得到 HTTP 401，看起来像"账号被封"，实际只是
    没刷新。刷新成功后必须写回磁盘，避免后续反复请求 auth.x.ai。
    """
    refresh = str(record.get("refresh_token") or "").strip()
    endpoint = str(record.get("token_endpoint") or "").strip()
    if not refresh or not endpoint:
        return None
    client_id = str(record.get("client_id") or DEFAULT_CLIENT_ID).strip()
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": client_id,
    }
    request = urllib.request.Request(
        endpoint,
        data=urllib.parse.urlencode(form).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "grok-register-quality/1.0",
        },
    )
    opener = urlopen or urllib.request.urlopen
    try:
        with opener(request, timeout=float(timeout)) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return None
    token = str(payload.get("access_token") or "").strip()
    if token:
        record["access_token"] = token
        if payload.get("refresh_token"):
            record["refresh_token"] = str(payload["refresh_token"]).strip()
        expires_in = int(payload.get("expires_in") or 21600)
        import datetime
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        record["last_refresh"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        exp_utc = now_utc + datetime.timedelta(seconds=expires_in)
        record["expired"] = exp_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        path = record.get("_path")
        if path:
            try:
                from pathlib import Path
                p = Path(path)
                data_to_write = {k: v for k, v in record.items() if not k.startswith("_")}
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(data_to_write, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(p)
            except Exception:
                pass
    return token or None


def _ensure_fresh_token(record, timeout=100.0, urlopen=None):
    """返回可用的 access_token，必要时先刷新。"""
    access = str(record.get("access_token") or record.get("key") or "").strip()
    if access and not _token_expired(record.get("expired")):
        return access, False
    refreshed = refresh_access_token(record, urlopen=urlopen)
    if refreshed:
        return refreshed, True
    return access, False


def _token_expired(value):
    """expired 是 UTC 的 ISO 时间串；无法解析时按"未过期"处理。"""
    text = str(value or "").strip()
    if not text:
        return False
    import datetime

    normalized = text.replace("Z", "+00:00")
    try:
        stamp = datetime.datetime.fromisoformat(normalized)
    except Exception:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    # 留 60 秒余量，避免刚刷完就过期。
    return stamp <= datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)


def probe_account(record, model=DEFAULT_MODEL, prompt=DEFAULT_PROMPT,
                  timeout=60.0, base_url="", urlopen=None, max_tokens=60,
                  stream=True, allow_refresh=True):
    """探测单个账号，返回判定结果字典。永不抛异常。

    默认启用流式 stream=True 与 stream_options.include_usage，实测 6~15 秒完成
    （非流式需等待全部推理完成，耗时常超过 35~50 秒）。
    """
    email = str(record.get("email") or "").strip()
    result = {
        "email": email,
        "verdict": VERDICT_ERROR,
        "reasoning_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "content": "",
        "status": 0,
        "error": "",
        "model": model,
        "refreshed": False,
    }
    if allow_refresh:
        access, refreshed = _ensure_fresh_token(record, timeout=timeout, urlopen=urlopen)
        result["refreshed"] = refreshed
    else:
        access = str(record.get("access_token") or record.get("key") or "").strip()
    if not access:
        result["verdict"] = VERDICT_RISK
        result["error"] = "凭据缺少 access_token"
        return result

    headers = dict(CLIENT_HEADERS)
    headers["Authorization"] = "Bearer " + access
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "text/event-stream" if stream else "application/json"
    target = _chat_url(base_url or record.get("base_url") or DEFAULT_BASE_URL)
    payload = build_payload(model=model, prompt=prompt, max_tokens=max_tokens, stream=stream)
    request = urllib.request.Request(
        target, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST",
    )
    opener = urlopen or urllib.request.urlopen
    started = time.time()
    usage = {}
    content_chunks = []
    try:
        with opener(request, timeout=float(timeout)) as response:
            result["status"] = int(getattr(response, "status", 200) or 200)
            if stream and hasattr(response, "readline"):
                first_chunk = response.readline()
                raw_first = first_chunk.decode("utf-8", "replace").strip() if isinstance(first_chunk, (bytes, bytearray)) else str(first_chunk or "").strip()
                if raw_first.startswith("data:") or raw_first.startswith(":") or b"data:" in first_chunk:
                    lines = [first_chunk] + list(response)
                    for line in lines:
                        raw = line.decode("utf-8", "replace").strip() if isinstance(line, (bytes, bytearray)) else str(line or "").strip()
                        if not raw.startswith("data:"):
                            continue
                        data_str = raw[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except Exception:
                            continue
                        if isinstance(chunk, dict):
                            if "usage" in chunk and isinstance(chunk["usage"], dict):
                                usage = chunk["usage"]
                            for ch in chunk.get("choices") or []:
                                if isinstance(ch, dict):
                                    dl = ch.get("delta") or {}
                                    if "content" in dl and dl["content"]:
                                        content_chunks.append(str(dl["content"]))
                else:
                    body = (first_chunk + response.read()).decode("utf-8", "replace")
                    data = json.loads(body)
                    usage = data.get("usage") or {}
                    for ch in data.get("choices") or []:
                        if isinstance(ch, dict):
                            msg = ch.get("message") or ch.get("delta") or {}
                            if "content" in msg and msg["content"]:
                                content_chunks.append(str(msg["content"]))
            else:
                body = response.read().decode("utf-8", "replace")
                data = json.loads(body)
                usage = data.get("usage") or {}
                for ch in data.get("choices") or []:
                    if isinstance(ch, dict):
                        msg = ch.get("message") or ch.get("delta") or {}
                        if "content" in msg and msg["content"]:
                            content_chunks.append(str(msg["content"]))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        result["status"] = int(getattr(exc, "code", 0) or 0)
        result["verdict"] = classify_failure(result["status"], detail)
        result["error"] = "HTTP %s: %s" % (result["status"], detail or "无响应体")
        result["duration_sec"] = round(time.time() - started, 2)
        return result
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        result["duration_sec"] = round(time.time() - started, 2)
        return result

    reasoning = extract_reasoning_tokens(usage)
    result["reasoning_tokens"] = reasoning
    result["completion_tokens"] = _int_field(usage, "completion_tokens", "completionTokens")
    result["total_tokens"] = _int_field(usage, "total_tokens", "totalTokens")
    result["content"] = "".join(content_chunks).strip()[:200]
    result["verdict"] = classify(reasoning)
    result["duration_sec"] = round(time.time() - started, 2)
    return result


def load_credentials(auth_dir="cpa_auths"):
    """读取目录下全部 CPA 凭据（只匹配 xai-*.json）。"""
    from pathlib import Path

    records = []
    directory = Path(auth_dir)
    if not directory.is_dir():
        return records
    for path in sorted(directory.glob("xai-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            data.setdefault("email", path.stem.replace("xai-", ""))
            data["_path"] = str(path)
            records.append(data)
    return records


def probe_all(records, model=DEFAULT_MODEL, prompt=DEFAULT_PROMPT,
              workers=4, timeout=100.0, log=_log):
    """并发探测多个账号，返回结果列表（保持输入顺序）。"""
    results = [None] * len(records)

    def run(index, record):
        return index, probe_account(record, model=model, prompt=prompt, timeout=timeout)

    if workers <= 1 or len(records) <= 1:
        for index, record in enumerate(records):
            _, item = run(index, record)
            results[index] = item
            if log:
                log(_format_line(item))
        return results

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = [pool.submit(run, i, r) for i, r in enumerate(records)]
        for future in as_completed(futures):
            try:
                index, item = future.result()
            except Exception as exc:
                continue
            results[index] = item
            if log:
                log(_format_line(item))
    return [r for r in results if r is not None]


def _format_line(item):
    email = str(item.get("email") or "?")
    verdict = str(item.get("verdict") or "?")
    mark = {
        VERDICT_HEALTHY: "✅", VERDICT_SOFT: "⚠️",
        VERDICT_HARD: "🧠", VERDICT_RISK: "🚫",
    }.get(verdict, "❓")
    detail = "推理 %s tok" % item.get("reasoning_tokens", 0)
    if item.get("error"):
        detail = str(item["error"])[:70]
    return "%s %-26s %-8s %s" % (mark, email[:26], verdict, detail)


def summarize(results):
    """汇总统计，便于面板展示。"""
    summary = {
        "total": len(results),
        "healthy": 0, "soft": 0, "hard": 0, "risk": 0, "error": 0,
        "degraded_emails": [], "risk_emails": [],
    }
    for item in results:
        verdict = str(item.get("verdict") or VERDICT_ERROR)
        if verdict not in summary:
            verdict = VERDICT_ERROR
        summary[verdict] += 1
        if verdict == VERDICT_HARD:
            summary["degraded_emails"].append(item.get("email"))
        elif verdict == VERDICT_RISK:
            summary["risk_emails"].append(item.get("email"))
    return summary
