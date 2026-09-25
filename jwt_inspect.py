"""xAI access_token 的 JWT 载荷检查。

背景
----
xAI 部分 access_token 的 payload 会带 `bfs` 字段（常见值 2）。**只要 key
存在即视为被标记**，与 grok.com 的 botFlagSource / policy=deny 不是同一
个信号 —— 后者已被证实不可靠，不再作为风控门禁。

这里的检查纯本地、不发网络请求，用于给已导出的 CPA 凭据打标。
"""
from __future__ import annotations

import base64
import json

# 标记字段名（大小写不敏感匹配）
BFS_CLAIM = "bfs"


def decode_jwt_payload(token):
    """解码 JWT 的 payload 段。

    只做 base64url 解码，**不校验签名** —— 我们只是读取自己刚拿到的
    token 里的声明，不是做安全校验。解析失败返回 {}。
    """
    if not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1].strip()
    if not segment:
        return {}
    padding = "=" * (-len(segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(segment + padding)
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def bfs_value(payload):
    """返回 bfs 标记值；未标记返回 None。

    注意：值为 0 或空串也算「存在该 key」，仍视为标记 —— 与上游项目
    的判定保持一致（只要 key 存在即视为标记）。
    """
    if not isinstance(payload, dict):
        return None
    for key, value in payload.items():
        if str(key).lower() == BFS_CLAIM:
            return value
    return None


def inspect_token(token):
    """检查一个 access_token，返回 {bfs, bfs_value, subject, email}。"""
    payload = decode_jwt_payload(token)
    value = bfs_value(payload)
    return {
        "bfs": value is not None,
        "bfs_value": value,
        "subject": str(payload.get("sub") or ""),
        "email": str(payload.get("email") or ""),
    }
