"""MooProxy 适配层：生成美国住宅代理，并提供本地链式桥接。

为什么需要本地桥
----------------
MooProxy 的入口 `us.mooproxy.net:55688` 从本机直连时 TCP 能建立但
代理握手无响应（实测返回空响应）。经干净出口（如东京节点）中转则正常，
说明入口对源 IP 有要求。

因此这里提供一个本地 HTTP 代理：浏览器只连本地端口，
由本进程经 SOCKS5 中转连到 MooProxy，再转发到目标站点。

链式结构：

    浏览器 → 本地桥(127.0.0.1:8890) → SOCKS5 中转 → MooProxy → 目标站点

代理池中的节点形如：

    http://lzh5233:<密码>_country-US_state-New York_city-None_session-XXX@127.0.0.1:8890

桥会从 Proxy-Authorization 里取出用户名/密码，原样用于 MooProxy 认证，
所以「一个账号一个 session」的粘性会话模型可以照常工作。

用法：
    # 生成 8 个纽约住宅节点（每个独立 session）
    python mooproxy_bridge.py generate --num 8 --state "New York"

    # 启动本地桥（默认 8890），并打印可直接填入配置的订阅地址
    python mooproxy_bridge.py serve --port 8890 --via socks5h://127.0.0.1:1080

    # 校验某个节点能否拿到美国住宅出口
    python mooproxy_bridge.py check --node "http://..."
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import select
import socket
import socketserver
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request

DEFAULT_API = "https://api.mooproxy.xyz/v1/api/generate_proxies"
DEFAULT_HOST = "us.mooproxy.net"
DEFAULT_PORT = 55688
DEFAULT_USER = os.environ.get("MOOPROXY_USER", "")
DEFAULT_PASS = os.environ.get("MOOPROXY_PASS", "")
# MooProxy 要求指定州，否则返回空列表（实测 amount 无 state 时 proxies 为空）。
DEFAULT_STATE = os.environ.get("MOOPROXY_STATE", "New York")


def _log(message):
    print("[mooproxy] %s" % message, flush=True)


class MooProxyError(RuntimeError):
    pass


# ---------------------------------------------------------------- 生成节点

def generate_nodes(
    username=DEFAULT_USER,
    password=DEFAULT_PASS,
    country="US",
    state=DEFAULT_STATE,
    city="",
    amount=8,
    check_score=False,
    check_tls=False,
    api=DEFAULT_API,
    timeout=60,
):
    """调用生成接口，返回 [{'proxy': 完整凭证, 'ip': 出口IP}, ...]。"""
    if not username or not password:
        raise MooProxyError(
            "缺少 MooProxy 账号：请设置 MOOPROXY_USER / MOOPROXY_PASS 环境变量，"
            "或用 --user/--pass 传入"
        )
    payload = {
        "username": username,
        "password": password,
        "country": country,
        # state 必填：留空或传 "无需大州" 时接口返回空列表。
        "state": state,
        # city 要传 None 而不是前端的占位文案「无需城市」，
        # 传占位文案会被当成真实城市名导致匹配不到上游节点。
        "city": city or None,
        "amount": max(1, int(amount)),
        "check_score": bool(check_score),
        "check_tls": bool(check_tls),
        "provider": "mooproxy",
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        api,
        data=body,
        headers={
            "Content-Type": "application/json",
            # 站点在 Cloudflare 后面，urllib 默认的 Python-urllib/3.x
            # User-Agent 会被直接 403，必须伪装成常见浏览器 UA。
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://api.mooproxy.xyz",
            "Referer": "https://api.mooproxy.xyz/",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    if isinstance(data, dict) and data.get("error"):
        raise MooProxyError("生成接口返回错误: %s" % data.get("error"))
    return (data or {}).get("proxies") or []


def generate_nodes_with_retry(
    attempts=6,
    delay=8.0,
    want=1,
    verify=True,
    expect_country="US",
    **kwargs,
):
    """反复生成直到拿到足够数量的合格节点。

    生成接口有两个实测行为需要容错：
      1. 会返回空列表（并非总是限流，重试通常即可成功）；
      2. 即使指定 country=US，仍可能混入其它国家（实测出现过印尼节点）。
    因此这里按「实际出口国家」过滤，而不是相信接口声称。

    verify=False 时跳过出口探测（只做数量检查），适合批量预取。
    """
    collected = []
    last_error = ""
    for attempt in range(1, max(1, int(attempts)) + 1):
        try:
            nodes = generate_nodes(**kwargs)
        except MooProxyError as exc:
            last_error = str(exc)
            nodes = []
        for node in nodes:
            if not verify:
                collected.append(node)
                continue
            info = _probe_node_country(node)
            country = info.get("country", "")
            if not country:
                # 探测失败通常意味着该节点已失效（实测会出现
                # "connect proxy error"）。这种节点若入池，会在注册
                # 中途断流，比直接丢弃代价大得多。
                _log("跳过不可用节点 %s（探测失败）" % node.get("ip"))
                continue
            if expect_country and country != expect_country:
                _log("跳过非%s节点 %s（实际 %s）" % (expect_country, node.get("ip"), country))
                continue
            node = dict(node)
            node["region"] = info.get("region", "")
            node["isp"] = info.get("isp", "")
            node["residential"] = not info.get("hosting") and not info.get("proxy")
            collected.append(node)
        if len(collected) >= max(1, int(want)):
            break
        if attempt < attempts:
            time.sleep(delay)
    if not collected:
        raise MooProxyError(
            "未能获取合格节点（已尝试 %d 次）%s" % (attempts, ("：" + last_error) if last_error else "")
        )
    return collected[: max(1, int(want))]


def _probe_node_country(node):
    """经隧道直连探测节点真实出口国家/地区。"""
    raw = str(node.get("proxy") or "").strip()
    if not raw:
        return {}
    parts = raw.split(":", 3)
    if len(parts) < 4:
        return {}
    user, password = parts[2], parts[3]
    via_host, via_port = _parse_via(os.environ.get("MOOPROXY_VIA", "socks5h://127.0.0.1:1080"))
    try:
        if via_host:
            sock = _socks5_connect(via_host, via_port, DEFAULT_HOST, DEFAULT_PORT, timeout=45)
        else:
            sock = socket.create_connection((DEFAULT_HOST, DEFAULT_PORT), timeout=45)
    except Exception:
        return {}
    try:
        sock.settimeout(45)
        token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
        sock.sendall((
            "GET http://ip-api.com/json/?fields=status,query,countryCode,regionName,city,isp,hosting,proxy "
            "HTTP/1.1\r\nHost: ip-api.com\r\nProxy-Authorization: Basic %s\r\nConnection: close\r\n\r\n" % token
        ).encode())
        data = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except Exception:
                break
            if not chunk:
                break
            data += chunk
    finally:
        try:
            sock.close()
        except Exception:
            pass
    body = data.split(b"\r\n\r\n", 1)[-1].decode("utf-8", "replace")
    try:
        info = json.loads(body)
    except Exception:
        return {}
    if not isinstance(info, dict) or info.get("status") != "success":
        return {}
    return {
        "ip": str(info.get("query") or ""),
        "country": str(info.get("countryCode") or ""),
        "region": str(info.get("regionName") or ""),
        "city": str(info.get("city") or ""),
        "isp": str(info.get("isp") or ""),
        "hosting": bool(info.get("hosting")),
        "proxy": bool(info.get("proxy")),
    }


def node_to_local_url(node, host=DEFAULT_HOST, port=DEFAULT_PORT, bridge_host="127.0.0.1", bridge_port=8890):
    """把 MooProxy 节点转成指向本地桥的代理 URL。

    桥会从 Proxy-Authorization 中取出原样的用户名/密码，
    所以这里只替换地址部分，凭据保持 MooProxy 的原值。
    """
    raw = str(node.get("proxy") or "").strip()
    if not raw:
        return ""
    parts = raw.split(":", 3)
    if len(parts) < 4:
        return ""
    user, password = parts[2], parts[3]
    quoted_user = urllib.parse.quote(user, safe="")
    quoted_pass = urllib.parse.quote(password, safe="")
    return "http://%s:%s@%s:%d" % (quoted_user, quoted_pass, bridge_host, bridge_port)


# ------------------------------------------------------------ SOCKS5 连接

def _socks5_connect(via_host, via_port, dest_host, dest_port, timeout=30):
    """经 SOCKS5 建立到目标的连接（远端解析域名）。"""
    sock = socket.create_connection((via_host, via_port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.sendall(b"\x05\x01\x00")
        if sock.recv(2) != b"\x05\x00":
            raise MooProxyError("SOCKS5 握手失败")
        host_bytes = dest_host.encode("idna") if not _is_ip(dest_host) else None
        if host_bytes is not None:
            request = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes
        elif ":" in dest_host:
            request = b"\x05\x01\x00\x04" + socket.inet_pton(socket.AF_INET6, dest_host)
        else:
            request = b"\x05\x01\x00\x01" + socket.inet_aton(dest_host)
        sock.sendall(request + struct.pack(">H", dest_port))
        reply = sock.recv(4)
        if len(reply) < 4 or reply[1] != 0:
            raise MooProxyError("SOCKS5 连接目标失败，代码 %s" % (reply[1] if len(reply) > 1 else "?"))
        atyp = reply[3]
        if atyp == 1:
            sock.recv(4)
        elif atyp == 3:
            length = sock.recv(1)[0]
            sock.recv(length)
        elif atyp == 4:
            sock.recv(16)
        sock.recv(2)
        return sock
    except Exception:
        sock.close()
        raise


def _is_ip(value):
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, value)
            return True
        except OSError:
            continue
    return False


def _parse_via(via):
    """解析 --via 参数，返回 (host, port)。"""
    raw = str(via or "").strip()
    if not raw:
        return "", 0
    if "://" in raw:
        parsed = urllib.parse.urlsplit(raw)
        return parsed.hostname or "", int(parsed.port or 1080)
    if ":" in raw:
        host, _, port = raw.rpartition(":")
        return host, int(port or 1080)
    return raw, 1080


# ---------------------------------------------------------------- 本地桥

class _BridgeHandler(socketserver.BaseRequestHandler):
    via_host = ""
    via_port = 0
    upstream_host = DEFAULT_HOST
    upstream_port = DEFAULT_PORT
    timeout = 60
    # 流量计量回调；为 None 时不统计。由 serve() 注入 traffic_meter.record。
    meter = None

    def _read_headers(self):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.request.recv(4096)
            if not chunk:
                return b""
            data += chunk
            if len(data) > 65536:
                break
        return data

    def _upstream_auth(self, headers_text):
        """从 Proxy-Authorization 里取出 MooProxy 凭据。"""
        for line in headers_text.split("\r\n"):
            if line.lower().startswith("proxy-authorization:"):
                token = line.split(":", 1)[1].strip()
                if token.lower().startswith("basic "):
                    try:
                        decoded = base64.b64decode(token[6:]).decode("utf-8", "replace")
                        user, _, password = decoded.partition(":")
                        if user and password:
                            return user, password
                    except Exception:
                        pass
        return "", ""

    def _open_upstream(self):
        if self.via_host:
            return _socks5_connect(
                self.via_host, self.via_port, self.upstream_host, self.upstream_port,
                timeout=self.timeout,
            )
        return socket.create_connection((self.upstream_host, self.upstream_port), timeout=self.timeout)

    def handle(self):
        try:
            initial = self._read_headers()
            if not initial:
                return
            head, _, rest = initial.partition(b"\r\n\r\n")
            text = head.decode("iso-8859-1", "replace")
            first_line = text.split("\r\n", 1)[0]
            parts = first_line.split()
            if len(parts) < 2:
                return
            method, target = parts[0].upper(), parts[1]

            user, password = self._upstream_auth(text)
            upstream = self._open_upstream()
            upstream.settimeout(self.timeout)
            try:
                if method == "CONNECT":
                    host, _, port = target.rpartition(":")
                    connect = "CONNECT %s:%s HTTP/1.1\r\nHost: %s:%s\r\n" % (host, port, host, port)
                    if user:
                        token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
                        connect += "Proxy-Authorization: Basic %s\r\n" % token
                    connect += "\r\n"
                    upstream.sendall(connect.encode())
                    meter_fn = self._meter_fn()
                    if meter_fn is not None:
                        try:
                            meter_fn("up", len(connect))
                        except Exception:
                            pass
                    reply = b""
                    while b"\r\n\r\n" not in reply:
                        chunk = upstream.recv(4096)
                        if not chunk:
                            break
                        reply += chunk
                    if b" 200" not in reply.split(b"\r\n", 1)[0]:
                        self.request.sendall(reply or b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                        return
                    self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    self._count_connection()
                    self._relay(self.request, upstream, meter=self._meter_fn())
                else:
                    # 普通 HTTP：改写请求行并补上上游认证头
                    lines = [l for l in text.split("\r\n") if not l.lower().startswith("proxy-authorization:")]
                    lines[0] = "%s %s HTTP/1.1" % (method, target)
                    if user:
                        token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
                        lines.append("Proxy-Authorization: Basic %s" % token)
                    rewritten = ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")
                    upstream.sendall(rewritten)
                    meter_fn = self._meter_fn()
                    if meter_fn is not None:
                        try:
                            meter_fn("up", len(rewritten))
                        except Exception:
                            pass
                    if rest:
                        upstream.sendall(rest)
                        if meter_fn is not None:
                            try:
                                meter_fn("up", len(rest))
                            except Exception:
                                pass
                    self._count_connection()
                    self._relay(self.request, upstream, meter=self._meter_fn())
            finally:
                try:
                    upstream.close()
                except Exception:
                    pass
        except Exception as exc:
            try:
                body = ("# bridge error: %s\n" % exc).encode("utf-8")
                self.request.sendall(
                    b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain\r\nContent-Length: "
                    + str(len(body)).encode() + b"\r\n\r\n" + body
                )
            except Exception:
                pass

    def _meter_fn(self):
        """取类属性上的原始计量函数。

        注意：meter 是挂在类上的普通函数，用 self.meter 取会变成绑定方法
        （自动多传一个 self），导致 record(direction, nbytes) 参数错位、
        被 except 静默吞掉，表现为「连接数正常但字节数恒为 0」。
        """
        return type(self).meter

    def _count_connection(self):
        if self.meter is None:
            return
        try:
            import traffic_meter
            traffic_meter.count_connection()
        except Exception:
            pass

    @staticmethod
    def _relay(left, right, meter=None):
        """双向中继，并可选地统计字节数。

        meter 接收 (direction, nbytes)，direction 为 "up"/"down"：
        up 是浏览器→上游（上行），down 是上游→浏览器（下行）。
        计量失败绝不能影响中继本身。
        """
        sockets = [left, right]
        while True:
            try:
                readable, _, errored = select.select(sockets, [], sockets, 90)
            except Exception:
                break
            if errored:
                break
            if not readable:
                break
            done = False
            for source in readable:
                target = right if source is left else left
                try:
                    chunk = source.recv(65536)
                except Exception:
                    done = True
                    break
                if not chunk:
                    done = True
                    break
                if meter is not None:
                    try:
                        meter("up" if source is left else "down", len(chunk))
                    except Exception:
                        pass
                try:
                    target.sendall(chunk)
                except Exception:
                    done = True
                    break
            if done:
                break


class _BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(
    port=8890,
    host="127.0.0.1",
    via="",
    upstream_host=DEFAULT_HOST,
    upstream_port=DEFAULT_PORT,
    meter=None,
):
    """启动本地链式桥。

    meter: 可选回调 (direction, nbytes)，用于统计本批代理流量。
           默认自动接入 traffic_meter；传 False 可显式关闭。
    """
    via_host, via_port = _parse_via(via)
    if meter is None:
        try:
            import traffic_meter
            meter = traffic_meter.record
        except Exception:
            meter = None
    handler = type("_Handler", (_BridgeHandler,), {
        "via_host": via_host,
        "via_port": via_port,
        "upstream_host": upstream_host,
        "upstream_port": upstream_port,
        "meter": meter,
    })
    server = _BridgeServer((host, port), handler)
    route = "经 %s:%s 中转" % (via_host, via_port) if via_host else "直连"
    _log("本地桥已启动: http://%s:%d（%s → %s:%d）" % (host, port, route, upstream_host, upstream_port))
    _log("订阅地址请填: http://%s:%d/subscription（或直接把节点写入 proxy_pool_file）" % (host, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("已停止")
    finally:
        server.server_close()


# ---------------------------------------------------------------- 校验

def check_node(node_url, expect_country="US", timeout=60):
    """经本地桥校验节点出口。"""
    parsed = urllib.parse.urlsplit(node_url)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8890)
    user = urllib.parse.unquote(parsed.username or "")
    password = urllib.parse.unquote(parsed.password or "")

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
    request = (
        "GET http://ip-api.com/json/?fields=status,country,countryCode,regionName,"
        "city,isp,org,as,hosting,proxy,mobile,query HTTP/1.1\r\n"
        "Host: ip-api.com\r\nProxy-Authorization: Basic %s\r\nConnection: close\r\n\r\n" % token
    )
    sock.sendall(request.encode())
    data = b""
    while True:
        try:
            chunk = sock.recv(4096)
        except Exception:
            break
        if not chunk:
            break
        data += chunk
    sock.close()
    body = data.split(b"\r\n\r\n", 1)[-1].decode("utf-8", "replace")
    try:
        info = json.loads(body)
    except Exception:
        return {"ok": False, "error": "响应非 JSON: %s" % body[:200]}
    if info.get("status") != "success":
        return {"ok": False, "error": "探测失败: %s" % body[:200]}
    country = str(info.get("countryCode") or "")
    residential = not info.get("hosting") and not info.get("proxy")
    ok = country == expect_country and residential
    return {
        "ok": ok,
        "ip": info.get("query"),
        "country": country,
        "region": info.get("regionName"),
        "city": info.get("city"),
        "isp": info.get("isp"),
        "hosting": info.get("hosting"),
        "proxy": info.get("proxy"),
        "mobile": info.get("mobile"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="MooProxy 美国住宅代理适配与本地链式桥")
    parser.add_argument("--user", default=DEFAULT_USER, help="MooProxy 账号")
    parser.add_argument("--pass", dest="password", default=DEFAULT_PASS, help="MooProxy 密码")
    parser.add_argument("--state", default=DEFAULT_STATE, help="州/省（必填，留空接口返回空列表）")
    parser.add_argument("--country", default="US", help="国家代码")
    parser.add_argument("--api", default=DEFAULT_API, help="生成接口地址")
    parser.add_argument("--via", default=os.environ.get("MOOPROXY_VIA", ""),
                        help="中转用的 SOCKS5（如 socks5h://127.0.0.1:1080）")

    sub = parser.add_subparsers(dest="command")

    p_gen = sub.add_parser("generate", help="生成节点")
    p_gen.add_argument("--num", type=int, default=8)
    p_gen.add_argument("--out", default="", help="写入文件（每行一个节点 URL）")
    p_gen.add_argument("--no-verify", dest="verify", action="store_false",
                       help="跳过出口国家校验（更快，但可能混入非目标国家节点）")
    p_gen.add_argument("--expect", default="US", help="期望的出口国家代码（默认 US）")
    p_gen.add_argument("--attempts", type=int, default=6, help="生成接口重试次数")
    p_gen.add_argument("--delay", type=float, default=8.0, help="重试间隔秒数")

    p_serve = sub.add_parser("serve", help="启动本地链式桥")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8890)
    p_serve.add_argument("--upstream-host", default=DEFAULT_HOST)
    p_serve.add_argument("--upstream-port", type=int, default=DEFAULT_PORT)

    p_check = sub.add_parser("check", help="校验节点出口")
    p_check.add_argument("--node", required=True)
    p_check.add_argument("--expect", default="US")

    args = parser.parse_args(argv)

    if args.command == "generate":
        if args.verify:
            nodes = generate_nodes_with_retry(
                attempts=args.attempts, delay=args.delay, want=args.num,
                verify=True, expect_country=args.expect,
                username=args.user, password=args.password,
                country=args.country, state=args.state, api=args.api,
            )
        else:
            nodes = generate_nodes(
                username=args.user, password=args.password,
                country=args.country, state=args.state,
                amount=args.num, api=args.api,
            )
        urls = []
        for node in nodes:
            url = node_to_local_url(node)
            if url:
                urls.append(url)
                detail = node.get("region") or ""
                isp = node.get("isp") or ""
                _log("节点 %s%s%s → 本地桥" % (
                    node.get("ip"),
                    (" / " + detail) if detail else "",
                    (" / " + isp) if isp else "",
                ))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write("\n".join(urls) + "\n")
            _log("已写入 %s（%d 个）" % (args.out, len(urls)))
        else:
            print("\n".join(urls))
        return 0

    if args.command == "serve":
        serve(port=args.port, host=args.host, via=args.via,
              upstream_host=args.upstream_host, upstream_port=args.upstream_port)
        return 0

    if args.command == "check":
        result = check_node(args.node, expect_country=args.expect)
        if result.get("ok"):
            _log("✅ 出口 %s（%s, %s）ISP=%s 住宅=%s"
                 % (result["ip"], result["country"], result.get("region"),
                    result.get("isp"), not result.get("hosting")))
            return 0
        _log("❌ 校验未通过: %s" % json.dumps(result, ensure_ascii=False))
        return 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
