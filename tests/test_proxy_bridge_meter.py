"""proxy_bridge 的流量计量集成测试。

住宅代理按流量计费，计量必须准。这里钉住三个真实踩过的坑：

1. meter 挂在**类**属性上时，用 self.meter 取会变成绑定方法（多传
   self），导致 record() 参数错位、异常被静默吞掉 —— 表现为
   「连接数正常但字节数恒为 0」。
2. 计量钩子只挂在另一个桥模块上，而实际跑的是 proxy_bridge 这条路径，
   导致面板长期显示 1 B。
3. 计量回调本身抛异常时，绝不能影响中继。
"""
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proxy_bridge import LocalProxyBridge, _relay  # noqa: E402


def _start_fake_upstream():
    """假上游代理：CONNECT 返回固定大小响应，普通请求返回 1000 字节。"""
    holder = []
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    holder.append(srv.getsockname()[1])

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except Exception:
                return

            def handle(c):
                try:
                    head = b""
                    while b"\r\n\r\n" not in head:
                        chunk = c.recv(4096)
                        if not chunk:
                            return
                        head += chunk
                    if head.startswith(b"CONNECT"):
                        c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                        c.recv(65536)
                        c.sendall(b"X" * 30000)
                    else:
                        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + b"Y" * 1000)
                    time.sleep(0.2)
                except Exception:
                    pass
                finally:
                    try:
                        c.close()
                    except Exception:
                        pass

            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return srv, holder[0]


def _start_bridge(upstream_port, meter):
    """起一个指向假上游的本地桥，并注入计量回调。"""
    # 假上游实现的是 HTTP CONNECT 协议，所以这里必须用 http:// 上游；
    # 用 socks5h:// 会走 SOCKS 握手，假上游看不懂。
    bridge = LocalProxyBridge("http://127.0.0.1:%d" % upstream_port)
    # 直接覆盖实例属性：实例属性不走描述符协议，取出来就是原函数。
    bridge.meter = meter
    endpoint = bridge.start()
    return bridge, endpoint


def _relay_pair():
    """构造 relay 需要的真实拓扑，返回 (app, left, right, srv)。

    relay(left, right) 把两条**独立**连接对接起来：

        app  <--连接A-->  left  |  relay  |  right  <--连接B-->  srv

    绝不能用 socket.socketpair() 或让两端直连：那样 A 端写出的数据会
    被 B 端读到、再被 relay 转回 A，两个 socket 互相回灌形成无限乒乓，
    测试永远不返回（实测卡死）。
    """
    def _listener():
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        return srv

    # 连接 A：app <-> left
    la = _listener()
    app = socket.create_connection(la.getsockname(), timeout=10)
    left, _ = la.accept()
    la.close()

    # 连接 B：right <-> srv
    lb = _listener()
    right = socket.create_connection(lb.getsockname(), timeout=10)
    srv, _ = lb.accept()
    lb.close()

    return app, left, right, srv


class RelayMeterTests(unittest.TestCase):
    """直接测 _relay 的计量语义。"""

    def test_counts_both_directions(self):
        """上行（client→server）与下行（server→client）都要计量。"""
        app, left, right, srv = _relay_pair()
        calls = []

        def meter(direction, nbytes):
            calls.append((direction, nbytes))

        def server():
            time.sleep(0.1)
            srv.sendall(b"D" * 300)   # 下行：srv -> right -> left -> app
            time.sleep(0.1)
            srv.recv(65536)           # 收下上行，绝不回显

        threading.Thread(target=server, daemon=True).start()
        app.sendall(b"U" * 100)       # 上行：app -> left -> right -> srv
        _relay(left, right, timeout=1.0, meter=meter)
        app.close(); left.close(); right.close(); srv.close()

        up = sum(n for d, n in calls if d == "up")
        down = sum(n for d, n in calls if d == "down")
        self.assertGreaterEqual(up, 100, calls)
        self.assertGreaterEqual(down, 300, calls)

    def test_meter_exception_does_not_break_relay(self):
        """计量抛异常时中继必须照常完成。"""
        app, left, right, srv = _relay_pair()

        def boom(_direction, _nbytes):
            raise RuntimeError("meter exploded")

        def server():
            time.sleep(0.1)
            srv.sendall(b"D" * 500)

        threading.Thread(target=server, daemon=True).start()
        app.sendall(b"U" * 50)
        _relay(left, right, timeout=1.0, meter=boom)  # 不应抛出
        app.close(); left.close(); right.close(); srv.close()

    def test_no_meter_is_fine(self):
        app, left, right, srv = _relay_pair()

        def server():
            time.sleep(0.1)
            srv.sendall(b"D" * 200)

        threading.Thread(target=server, daemon=True).start()
        app.sendall(b"U" * 20)
        _relay(left, right, timeout=1.0, meter=None)
        app.close(); left.close(); right.close(); srv.close()


class BridgeMeterTests(unittest.TestCase):
    """经真实本地桥的端到端计量。"""

    def setUp(self):
        self.calls = []
        self.upstream, self.upstream_port = _start_fake_upstream()
        self.bridge, self.endpoint = _start_bridge(
            self.upstream_port, lambda d, n: self.calls.append((d, n))
        )
        self.bridge_port = int(self.endpoint.rsplit(":", 1)[1])

    def tearDown(self):
        try:
            self.bridge.stop()
        except Exception:
            pass
        try:
            self.upstream.close()
        except Exception:
            pass

    def test_meter_is_not_bound_as_instance_method(self):
        """meter 必须是原函数，不能是绑定方法。

        回归测试：曾因 self.meter 变成 bound method，参数错位导致
        字节数恒为 0 而连接数正常。
        """
        probe = LocalProxyBridge("http://127.0.0.1:9")
        fn = lambda direction, nbytes: None  # noqa: E731
        probe.meter = fn
        resolved = probe._meter_fn()
        self.assertIs(resolved, fn)
        # 绑定方法会多出 self 参数，这里确认没有
        self.assertEqual(resolved.__code__.co_argcount, 2)

    def test_connect_counts_both_directions(self):
        s = socket.create_connection(("127.0.0.1", self.bridge_port), timeout=15)
        s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        self.assertIn(b"200", s.recv(4096))
        s.sendall(b"U" * 5000)
        received = 0
        while received < 30000:
            chunk = s.recv(65536)
            if not chunk:
                break
            received += len(chunk)
        s.close()
        time.sleep(0.5)
        up = sum(n for d, n in self.calls if d == "up")
        down = sum(n for d, n in self.calls if d == "down")
        self.assertGreaterEqual(down, 30000)
        # 上行至少包含 5000 字节载荷（外加握手）
        self.assertGreaterEqual(up, 5000)
        self.assertEqual(received, 30000)

    def test_plain_http_counts_rewritten_headers(self):
        """非 CONNECT 路径的上行也必须被计量。"""
        s = socket.create_connection(("127.0.0.1", self.bridge_port), timeout=15)
        s.sendall(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n"
                  b"Connection: close\r\n\r\n")
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        s.close()
        time.sleep(0.5)
        up = sum(n for d, n in self.calls if d == "up")
        down = sum(n for d, n in self.calls if d == "down")
        self.assertGreater(up, 0, "请求头未被计入上行")
        self.assertGreaterEqual(down, 1000)

    def test_connection_counted_once_per_request(self):
        """每个请求计一次连接数。

        连接计数走的是 traffic_meter.count_connection()，不是注入的
        meter 回调，所以这里替换掉 traffic_meter 来观察。
        """
        import traffic_meter
        counted = []
        original = traffic_meter.count_connection
        traffic_meter.count_connection = lambda *a, **k: counted.append(1)
        try:
            s = socket.create_connection(("127.0.0.1", self.bridge_port), timeout=15)
            s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
            s.recv(4096)
            s.close()
            time.sleep(0.3)
        finally:
            traffic_meter.count_connection = original
        self.assertEqual(len(counted), 1, "连接数应恰好计一次")

    def test_connection_not_counted_when_meter_disabled(self):
        """meter 为 None（未启用计量）时不应计连接数。"""
        import traffic_meter
        counted = []
        original = traffic_meter.count_connection
        traffic_meter.count_connection = lambda *a, **k: counted.append(1)
        bridge, endpoint = _start_bridge(self.upstream_port, None)
        try:
            port = int(endpoint.rsplit(":", 1)[1])
            s = socket.create_connection(("127.0.0.1", port), timeout=15)
            s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
            s.recv(4096)
            s.close()
            time.sleep(0.3)
        finally:
            traffic_meter.count_connection = original
            try:
                bridge.stop()
            except Exception:
                pass
        self.assertEqual(counted, [])

    def test_broken_upstream_does_not_crash(self):
        """上游连不上时应安静失败，不抛异常。"""
        bridge, endpoint = _start_bridge(1, lambda d, n: None)  # 端口 1 必然拒绝
        try:
            port = int(endpoint.rsplit(":", 1)[1])
            s = socket.create_connection(("127.0.0.1", port), timeout=15)
            s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
            try:
                s.recv(4096)
            except Exception:
                pass
            s.close()
        finally:
            try:
                bridge.stop()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
