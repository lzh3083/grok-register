"""mooproxy_bridge 的流量计量集成测试。

这里钉住两个真实踩过的坑：
1. meter 挂在类属性上时，用 self.meter 取会变成绑定方法（多传 self），
   导致 record() 参数错位、异常被静默吞掉 —— 表现为「连接数正常但
   字节数恒为 0」。
2. 非 CONNECT 路径只计量请求体、漏掉改写的请求头，会让上行恒偏小。
"""
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mooproxy_bridge as mb  # noqa: E402


def _start_fake_upstream():
    """假上游代理：返回固定大小的响应。"""
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
    handler = type("_TestHandler", (mb._BridgeHandler,), {
        "via_host": "", "via_port": 0,
        "upstream_host": "127.0.0.1", "upstream_port": upstream_port,
        "timeout": 20, "meter": meter,
    })
    server = mb._BridgeServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


class BridgeMeterTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.upstream, self.upstream_port = _start_fake_upstream()
        self.server, self.bridge_port = _start_bridge(
            self.upstream_port, lambda d, n: self.calls.append((d, n))
        )

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        try:
            self.upstream.close()
        except Exception:
            pass

    def test_meter_is_not_bound_as_instance_method(self):
        """meter 必须是原函数，不能是绑定方法。

        回归测试：曾因 self.meter 变成 bound method，导致参数错位、
        字节数恒为 0 而连接数正常。
        """
        handler_cls = type("_Probe", (mb._BridgeHandler,), {"meter": lambda d, n: None})
        instance = handler_cls.__new__(handler_cls)
        fn = instance._meter_fn()
        self.assertIs(fn, handler_cls.meter)
        # 绑定方法会多出 self 参数，这里确认没有
        self.assertEqual(fn.__code__.co_argcount, 2)

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
        """非 CONNECT 路径必须把改写后的请求头计入上行。

        回归测试：曾只计量请求体，导致上行恒偏小。
        """
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

    def test_meter_exception_does_not_break_relay(self):
        """计量回调抛异常时，中继必须照常工作。"""
        def boom(direction, nbytes):
            raise RuntimeError("meter exploded")

        server, port = _start_bridge(self.upstream_port, boom)
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=15)
            s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
            self.assertIn(b"200", s.recv(4096))
            s.sendall(b"U" * 100)
            received = 0
            while received < 30000:
                chunk = s.recv(65536)
                if not chunk:
                    break
                received += len(chunk)
            s.close()
            self.assertEqual(received, 30000)
        finally:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass

    def test_no_meter_means_no_counting(self):
        """meter=None 时中继照常，不应报错。"""
        server, port = _start_bridge(self.upstream_port, None)
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=15)
            s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
            self.assertIn(b"200", s.recv(4096))
            s.sendall(b"U" * 100)
            received = 0
            while received < 30000:
                chunk = s.recv(65536)
                if not chunk:
                    break
                received += len(chunk)
            s.close()
            self.assertEqual(received, 30000)
            self.assertEqual(self.calls, [])
        finally:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
