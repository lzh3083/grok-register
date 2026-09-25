"""辣椒HTTP（lajiaohttp）美国动态住宅代理适配层。

用途
----
把辣椒的"提取 IP"API 转换成本项目代理池可以直接消费的节点列表，
并在入池前做**美国住宅真实性校验**，避免机房 IP / 非美 IP 混入。

为什么需要本地服务而不是直接填 subscription_url
------------------------------------------------
项目原生支持 `proxy_pool_subscription_url`，理论上可以直接填辣椒的
提取链接。但直接用有三个问题：

1. **粘性会话时长与刷新周期耦合**：辣椒 `t` 参数是粘性会话的分钟数，
   而项目按 `proxy_pool_refresh_interval_sec` 刷新节点。若刷新周期 > t，
   在用节点会在注册中途失效；若太短，则频繁消耗提取次数。
2. **无法校验 IP 真实性**：辣椒池子大，可能返回被标记的 IP。直接入池
   会把脏 IP 带进注册流程。
3. **白名单源 IP 漂移**：API 白名单模式要求调用方源 IP 在白名单内。
   多出口主机访问不同目标时源 IP 可能不同（负载均衡/分流），
   需要一个统一出口并给出明确报错。

因此这里提供一个本地小服务：项目把它当作订阅地址，它负责提取、校验、
缓存与统一出口。

粘性会话设计
------------
一个账号从打开注册页到拿到 SSO、再跑完 CPA 导出，耗时可能超过 10 分钟。
所以粘性时长 `t` 必须显著大于单账号流程耗时，默认取 60 分钟。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_API = "http://api.lajiaohttp.com/api/extract_ip"

# 辣椒 txt 返回形如 1.2.3.4:8080，也可能给网关域名。
_PROXY_LINE_RE = re.compile(r"^(?P<host>[A-Za-z0-9._-]+):(?P<port>\d{1,5})$")
_WHITELIST_HINT_RE = re.compile(r"not\s+added\s+to\s+whitelist", re.I)


class LajiaoError(RuntimeError):
    """提取或校验失败。"""


def _log(message: str) -> None:
    sys.stderr.write("[lajiao] %s\n" % message)
    sys.stderr.flush()


def _http_get(url: str, timeout: float = 20.0, via: str = "") -> str:
    """GET 一个 URL，可选经由上游代理（用于固定白名单源 IP）。"""
    handlers = []
    if via:
        handlers.append(urllib.request.ProxyHandler({"http": via, "https": via}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))  # 显式直连，忽略环境变量
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "Accept": "text/plain, */*",
        },
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise LajiaoError("提取接口 HTTP %s" % exc.code) from exc
    except Exception as exc:
        raise LajiaoError("提取接口请求失败: %s" % exc) from exc


def parse_proxy_lines(text: str) -> list:
    """从接口返回里解析出代理地址列表。

    辣椒在失败时会返回纯文本提示（例如 "1.2.3.4 not added to whitelist"），
    这里把它识别成明确错误，而不是当成 0 个节点静默通过。
    """
    proxies = []
    for raw_line in str(text or "").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        match = _PROXY_LINE_RE.match(line)
        if match:
            port = int(match.group("port"))
            if 1 <= port <= 65535:
                proxies.append("%s:%d" % (match.group("host"), port))
            continue
        if _WHITELIST_HINT_RE.search(line):
            raise LajiaoError(
                "源 IP 未加入白名单: %s —— 请把该 IP 加入辣椒后台的 API 白名单，"
                "或用 --via 指定一个已加白名单的固定出口" % line
            )
        # 其它非空文本视为服务端错误提示
        if len(line) > 2 and ":" not in line:
            raise LajiaoError("提取接口返回异常内容: %s" % line[:200])
    return proxies


def build_extract_url(
    api: str = DEFAULT_API,
    regions: str = "US",
    num: int = 1,
    protocol: str = "http",
    cate: int = 2,
    sticky_minutes: int = 60,
    extra: str = "",
) -> str:
    """拼出提取链接。

    cate: 1=轮换（每次请求换 IP），2=粘性（固定时间内保持同一 IP）。
          注册场景必须用 2。
    """
    params = {
        "regions": regions,
        "num": max(1, int(num)),
        "protocol": protocol,
        "type": "txt",
        "cate": int(cate),
        "t": max(1, int(sticky_minutes)),
        "lb": 1,
    }
    url = "%s?%s" % (api.rstrip("/"), urllib.parse.urlencode(params))
    extra = str(extra or "").strip().lstrip("&")
    if extra:
        url = "%s&%s" % (url, extra)
    return url


def extract_proxies(
    num: int = 1,
    regions: str = "US",
    sticky_minutes: int = 60,
    api: str = DEFAULT_API,
    protocol: str = "http",
    via: str = "",
    timeout: float = 20.0,
    extra: str = "",
) -> list:
    """调用提取接口，返回 ["ip:port", ...]。"""
    url = build_extract_url(
        api=api, regions=regions, num=num, protocol=protocol,
        cate=2, sticky_minutes=sticky_minutes, extra=extra,
    )
    body = _http_get(url, timeout=timeout, via=via)
    proxies = parse_proxy_lines(body)
    if not proxies:
        raise LajiaoError("提取接口未返回任何代理节点: %r" % body[:200])
    return proxies


def probe_proxy(proxy: str, timeout: float = 20.0) -> dict:
    """探测单个代理的出口 IP 与国家/机房属性。

    返回 {"ok", "ip", "country", "hosting", "proxy_url", "error"}。
    校验走 ip-api.com（免费、无需 key），用于确认拿到的确实是美国住宅 IP。
    """
    result = {"ok": False, "proxy_url": proxy, "ip": "", "country": "", "hosting": None, "error": ""}
    proxy_url = proxy if "://" in proxy else "http://%s" % proxy
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    )
    # 先确认能出网
    try:
        request = urllib.request.Request("http://ip-api.com/json/?fields=status,country,countryCode,isp,org,as,hosting,proxy,mobile")
        with opener.open(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        result["error"] = "代理不可用: %s" % exc
        return result
    if not isinstance(data, dict) or data.get("status") != "success":
        result["error"] = "探测接口返回异常: %r" % (data,)
        return result
    result.update({
        "ok": True,
        "ip": str(data.get("query") or ""),
        "country": str(data.get("countryCode") or ""),
        "hosting": data.get("hosting"),
        "isp": str(data.get("isp") or ""),
        "asn": str(data.get("as") or ""),
    })
    return result


class LajiaoPool:
    """带缓存与校验的代理池取数器（线程安全）。"""

    def __init__(
        self,
        num: int = 8,
        regions: str = "US",
        sticky_minutes: int = 60,
        refresh_sec: int = 600,
        api: str = DEFAULT_API,
        via: str = "",
        require_residential: bool = True,
        require_country: str = "US",
    ):
        self.num = max(1, int(num))
        self.regions = str(regions or "US")
        self.sticky_minutes = max(1, int(sticky_minutes))
        self.refresh_sec = max(30, int(refresh_sec))
        self.api = api
        self.via = via
        self.require_residential = bool(require_residential)
        self.require_country = str(require_country or "").upper()
        self._lock = threading.Lock()
        self._nodes: list = []
        self._fetched_at = 0.0
        self._last_error = ""

    def snapshot(self) -> list:
        """返回当前有效节点；到期则重新提取。"""
        with self._lock:
            now = time.time()
            if self._nodes and (now - self._fetched_at) < self.refresh_sec:
                return list(self._nodes)
            try:
                nodes = self._refresh_locked()
                return list(nodes)
            except LajiaoError as exc:
                self._last_error = str(exc)
                # 刷新失败但仍有未过期节点时，继续沿用旧节点（fail-soft）
                if self._nodes and (now - self._fetched_at) < self.sticky_minutes * 60:
                    _log("刷新失败，沿用旧节点: %s" % exc)
                    return list(self._nodes)
                raise

    def _refresh_locked(self) -> list:
        raw = extract_proxies(
            num=self.num, regions=self.regions, sticky_minutes=self.sticky_minutes,
            api=self.api, via=self.via,
        )
        accepted, rejected = [], []
        for proxy in raw:
            if self.require_residential or self.require_country:
                info = probe_proxy(proxy)
                if not info["ok"]:
                    rejected.append((proxy, info["error"]))
                    continue
                if self.require_country and info["country"] != self.require_country:
                    rejected.append((proxy, "国家不符: %s" % info["country"]))
                    continue
                if self.require_residential and info["hosting"]:
                    rejected.append((proxy, "非住宅(hosting=true): %s" % info["isp"]))
                    continue
                _log("通过校验 %s -> %s %s (%s)" % (proxy, info["ip"], info["country"], info["isp"]))
            accepted.append(proxy)
        for proxy, reason in rejected:
            _log("剔除节点 %s: %s" % (proxy, reason))
        if not accepted:
            raise LajiaoError(
                "提取到 %d 个节点但全部未通过校验（要求国家=%s 住宅=%s）"
                % (len(raw), self.require_country or "任意", self.require_residential)
            )
        self._nodes = accepted
        self._fetched_at = time.time()
        self._last_error = ""
        _log("已刷新代理池: %d 个可用节点（粘性 %d 分钟）" % (len(accepted), self.sticky_minutes))
        return accepted

    def status(self) -> dict:
        with self._lock:
            age = time.time() - self._fetched_at if self._fetched_at else None
            return {
                "nodes": len(self._nodes),
                "age_sec": round(age, 1) if age is not None else None,
                "refresh_sec": self.refresh_sec,
                "sticky_minutes": self.sticky_minutes,
                "regions": self.regions,
                "last_error": self._last_error,
            }


def make_handler(pool: LajiaoPool):
    """构造订阅式 HTTP 处理器。"""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
            path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
            if path in ("/proxies", "/", "/sub"):
                try:
                    nodes = pool.snapshot()
                except LajiaoError as exc:
                    _log("取数失败: %s" % exc)
                    self._send(502, ("# error: %s\n" % exc).encode("utf-8"), "text/plain; charset=utf-8")
                    return
                body = ("\n".join(nodes) + "\n").encode("utf-8")
                self._send(200, body, "text/plain; charset=utf-8")
                return
            if path == "/health":
                body = json.dumps(pool.status(), ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
                return
            self._send(404, b"not found\n", "text/plain; charset=utf-8")

        def log_message(self, fmt, *args):  # 静音默认访问日志
            return

    return Handler


def cmd_extract(args) -> int:
    """命令行：提取并校验，打印结果（用于接入前自检）。"""
    try:
        proxies = extract_proxies(
            num=args.num, regions=args.regions, sticky_minutes=args.sticky,
            api=args.api, via=args.via,
        )
    except LajiaoError as exc:
        _log("提取失败: %s" % exc)
        return 2
    print("提取到 %d 个节点:" % len(proxies))
    for proxy in proxies:
        print("  %s" % proxy)
    if args.check:
        print("\n校验（出口 IP / 国家 / 是否机房）:")
        for proxy in proxies:
            info = probe_proxy(proxy)
            if info["ok"]:
                print("  %-22s -> %-15s %s  hosting=%s  %s"
                      % (proxy, info["ip"], info["country"], info["hosting"], info.get("isp", "")))
            else:
                print("  %-22s -> 失败: %s" % (proxy, info["error"]))
    return 0


def cmd_serve(args) -> int:
    """命令行：启动本地订阅服务。"""
    pool = LajiaoPool(
        num=args.num, regions=args.regions, sticky_minutes=args.sticky,
        refresh_sec=args.refresh, api=args.api, via=args.via,
        require_residential=not args.allow_datacenter,
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(pool))
    _log("订阅服务已启动: http://%s:%d/proxies  (健康检查 /health)" % (args.host, args.port))
    _log("请在 config.json 设置 proxy_pool_subscription_url 为该地址")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("收到中断，退出")
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lajiao_proxy",
        description="辣椒HTTP 美国动态住宅代理适配层（提取 / 校验 / 本地订阅服务）",
    )
    parser.add_argument("--api", default=os.environ.get("LAJIAO_API", DEFAULT_API),
                        help="提取接口地址")
    parser.add_argument("--regions", default=os.environ.get("LAJIAO_REGIONS", "US"),
                        help="地区代码，默认 US")
    parser.add_argument("--num", type=int, default=int(os.environ.get("LAJIAO_NUM", "8")),
                        help="一次提取的 IP 数")
    parser.add_argument("--sticky", type=int, default=int(os.environ.get("LAJIAO_STICKY_MIN", "60")),
                        help="粘性会话分钟数（必须大于单账号流程耗时）")
    parser.add_argument("--via", default=os.environ.get("LAJIAO_VIA", ""),
                        help="调用提取接口时使用的上游代理（用于固定白名单源 IP）")
    sub = parser.add_subparsers(dest="command")

    p_extract = sub.add_parser("extract", help="提取并（可选）校验节点")
    p_extract.add_argument("--check", action="store_true", help="逐个校验出口 IP 与国家")
    p_extract.set_defaults(func=cmd_extract)

    p_serve = sub.add_parser("serve", help="启动本地订阅服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8899)
    p_serve.add_argument("--refresh", type=int, default=600, help="节点缓存秒数")
    p_serve.add_argument("--allow-datacenter", action="store_true",
                         help="允许机房 IP（默认只放行住宅 IP）")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
