"""novproxy 的单元测试。

重点覆盖解析与错误识别（供应商失败时回的是纯文本提示，不能当成
0 个节点静默通过），以及「入口是机房、出口才是住宅」这一特性下
必须用 exit_ip 去重、按出口国家校验的逻辑。
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import novproxy  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_parses_host_port_lines(self):
        text = "1.2.3.4:7000\n5.6.7.8:8080\n"
        self.assertEqual(novproxy.parse_proxy_lines(text),
                         ["1.2.3.4:7000", "5.6.7.8:8080"])

    def test_ignores_blank_lines_and_crlf(self):
        text = "\r\n1.2.3.4:7000\r\n\r\n5.6.7.8:80\r\n"
        self.assertEqual(novproxy.parse_proxy_lines(text),
                         ["1.2.3.4:7000", "5.6.7.8:80"])

    def test_deduplicates(self):
        text = "1.2.3.4:7000\n1.2.3.4:7000\n"
        self.assertEqual(novproxy.parse_proxy_lines(text), ["1.2.3.4:7000"])

    def test_rejects_invalid_port(self):
        text = "1.2.3.4:0\n1.2.3.4:99999\n1.2.3.4:7000\n"
        self.assertEqual(novproxy.parse_proxy_lines(text), ["1.2.3.4:7000"])

    def test_empty_returns_empty(self):
        for value in ["", None, "   \n  \n"]:
            self.assertEqual(novproxy.parse_proxy_lines(value), [])

    def test_whitelist_error_raises(self):
        with self.assertRaises(novproxy.NovProxyError) as ctx:
            novproxy.parse_proxy_lines("1.2.3.4 not added to whitelist")
        self.assertIn("白名单", str(ctx.exception))

    def test_traffic_expired_raises(self):
        with self.assertRaises(novproxy.NovProxyError) as ctx:
            novproxy.parse_proxy_lines("traffic expired")
        self.assertIn("流量", str(ctx.exception))

    def test_no_resource_raises(self):
        with self.assertRaises(novproxy.NovProxyError):
            novproxy.parse_proxy_lines("no available proxy")

    def test_generic_error_text_raises(self):
        with self.assertRaises(novproxy.NovProxyError):
            novproxy.parse_proxy_lines("some unexpected server message")


class UrlTests(unittest.TestCase):
    def test_builds_url(self):
        url = novproxy.build_extract_url(novproxy.DEFAULT_API, region="US", num=5, minutes=120)
        self.assertIn("region=US", url)
        self.assertIn("num=5", url)
        self.assertIn("time=120", url)
        self.assertIn("type=txt", url)

    def test_defaults_to_120_minutes(self):
        """time=10 的节点几十秒内就批量失效（实测立即可用仅 5/10），
        time=120 实测 9/10 可用且端口段独立，所以默认用 120。"""
        url = novproxy.build_extract_url(novproxy.DEFAULT_API, num=1)
        self.assertIn("time=120", url)

    def test_handles_existing_query(self):
        url = novproxy.build_extract_url("https://x/api?k=v", num=1)
        self.assertIn("?k=v&", url)
        self.assertNotIn("?k=v?", url)


class ProbeTests(unittest.TestCase):
    def _fetch(self, payload):
        def inner(_url, _timeout, _proxy):
            return payload if isinstance(payload, str) else json.dumps(payload)
        return inner

    def test_successful_probe(self):
        payload = {"status": "success", "query": "1.2.3.4", "countryCode": "US",
                   "city": "Tampa", "isp": "Frontier", "hosting": False}
        out = novproxy.probe_node("h:1", "US", fetch=self._fetch(payload))
        self.assertTrue(out["ok"])
        self.assertEqual(out["exit_ip"], "1.2.3.4")
        self.assertEqual(out["city"], "Tampa")

    def test_country_mismatch_marks_failed(self):
        payload = {"status": "success", "query": "1.2.3.4", "countryCode": "ID",
                   "city": "Makassar", "isp": "Telkom", "hosting": False}
        out = novproxy.probe_node("h:1", "US", fetch=self._fetch(payload))
        self.assertFalse(out["ok"])
        self.assertIn("国家不符", out["reason"])

    def test_upstream_error_marks_failed(self):
        out = novproxy.probe_node("h:1", "US", fetch=self._fetch("msg: connect proxy error"))
        self.assertFalse(out["ok"])
        self.assertIn("上游连接失败", out["reason"])

    def test_empty_response_marks_failed(self):
        out = novproxy.probe_node("h:1", "US", fetch=self._fetch(""))
        self.assertFalse(out["ok"])

    def test_unparsable_marks_failed(self):
        out = novproxy.probe_node("h:1", "US", fetch=self._fetch("<html>oops</html>"))
        self.assertFalse(out["ok"])

    def test_exception_marks_failed(self):
        def boom(*_a):
            raise RuntimeError("timeout")
        out = novproxy.probe_node("h:1", "US", fetch=boom)
        self.assertFalse(out["ok"])
        self.assertIn("timeout", out["reason"])

    def test_empty_expect_accepts_any_country(self):
        payload = {"status": "success", "query": "1.2.3.4", "countryCode": "JP",
                   "hosting": False}
        out = novproxy.probe_node("h:1", "", fetch=self._fetch(payload))
        self.assertTrue(out["ok"])


class VerifyTests(unittest.TestCase):
    def test_splits_good_and_bad_without_retry(self):
        original = novproxy.probe_node
        table = {
            "a:1": {"node": "a:1", "ok": True, "exit_ip": "1.1.1.1"},
            "b:2": {"node": "b:2", "ok": False, "reason": "上游连接失败"},
        }
        novproxy.probe_node = lambda n, e, t, fetch=None: table[n]
        self.addCleanup(setattr, novproxy, "probe_node", original)
        good, bad = novproxy.verify_nodes(["a:1", "b:2"], retry_failed=False, log=lambda _m: None)
        self.assertEqual([g["node"] for g in good], ["a:1"])
        self.assertEqual([b["node"] for b in bad], ["b:2"])

    def test_retry_recovers_failed_node(self):
        """预热重试能把首轮失败的节点救回来 —— 这是实测到的真实行为。"""
        calls = {"n": 0}

        def fake(node, _expect, _timeout, fetch=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"node": node, "ok": False, "reason": "上游连接失败"}
            return {"node": node, "ok": True, "exit_ip": "9.9.9.9"}

        original = novproxy.probe_node
        novproxy.probe_node = fake
        self.addCleanup(setattr, novproxy, "probe_node", original)
        good, bad = novproxy.verify_nodes(["a:1"], retry_failed=True,
                                          retry_delay=0, log=lambda _m: None)
        self.assertEqual(len(good), 1)
        self.assertEqual(bad, [])


class WriteTests(unittest.TestCase):
    def _write(self, nodes, **kwargs):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = str(Path(tmp.name) / "nodes.txt")
        novproxy.write_nodes(path, nodes, log=lambda _m: None, **kwargs)
        return Path(path).read_text(encoding="utf-8")

    def test_defaults_to_socks5h(self):
        """必须写 socks5h://（远端解析）。

        本项目跑在 Clash/Mihomo 类环境，容器 DNS 对多数域名返回 fake-ip
        （198.18.0.0/15）。socks5:// 会让本地桥先解析出 fake-ip 再交给
        上游，必然连不上；socks5h:// 把域名交给代理端解析才对。
        """
        self.assertEqual(self._write([{"node": "a:1"}, {"node": "b:2"}]),
                         "socks5h://a:1\nsocks5h://b:2\n")

    def test_upgrades_bare_socks5_to_socks5h(self):
        """已带 socks5:// 的输入也要升级，否则同样会踩 fake-ip。"""
        self.assertEqual(self._write([{"node": "socks5://a:1"}]),
                         "socks5h://a:1\n")

    def test_keeps_socks5h_as_is(self):
        self.assertEqual(self._write([{"node": "socks5h://a:1"}]), "socks5h://a:1\n")

    def test_does_not_rewrite_other_schemes(self):
        self.assertEqual(self._write([{"node": "http://a:1"}]), "http://a:1\n")

    def test_accepts_plain_strings(self):
        self.assertEqual(self._write(["a:1"]), "socks5h://a:1\n")

    def test_custom_scheme(self):
        self.assertEqual(self._write(["a:1"], scheme="socks5"), "socks5://a:1\n")

    def test_empty_list_writes_empty_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "nodes.txt")
            novproxy.write_nodes(path, [], log=lambda _m: None)
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "")

    def test_missing_path_raises(self):
        with self.assertRaises(novproxy.NovProxyError):
            novproxy.write_nodes("", [], log=lambda _m: None)


class GenerateTests(unittest.TestCase):
    def test_dedupes_by_exit_ip(self):
        """同一住宅 IP 经不同端口重复出现时只保留一个。"""
        original_fetch = novproxy.fetch_nodes
        original_verify = novproxy.verify_nodes
        novproxy.fetch_nodes = lambda *a, **k: ["a:1", "b:2"]
        novproxy.verify_nodes = lambda nodes, **k: (
            [{"node": n, "ok": True, "exit_ip": "1.1.1.1"} for n in nodes], [])
        self.addCleanup(setattr, novproxy, "fetch_nodes", original_fetch)
        self.addCleanup(setattr, novproxy, "verify_nodes", original_verify)
        good = novproxy.generate("", "out", want=5, rounds=1, log=lambda _m: None)
        self.assertEqual(len(good), 1)

    def test_raises_when_nothing_usable(self):
        original_fetch = novproxy.fetch_nodes
        original_verify = novproxy.verify_nodes
        novproxy.fetch_nodes = lambda *a, **k: ["a:1"]
        novproxy.verify_nodes = lambda nodes, **k: ([], [{"node": "a:1", "ok": False}])
        self.addCleanup(setattr, novproxy, "fetch_nodes", original_fetch)
        self.addCleanup(setattr, novproxy, "verify_nodes", original_verify)
        with self.assertRaises(novproxy.NovProxyError):
            novproxy.generate("", "out", want=1, rounds=1, log=lambda _m: None)

    def test_respects_want_limit(self):
        original_fetch = novproxy.fetch_nodes
        original_verify = novproxy.verify_nodes
        novproxy.fetch_nodes = lambda *a, **k: ["a:1", "b:2", "c:3"]
        novproxy.verify_nodes = lambda nodes, **k: (
            [{"node": n, "ok": True, "exit_ip": n} for n in nodes], [])
        self.addCleanup(setattr, novproxy, "fetch_nodes", original_fetch)
        self.addCleanup(setattr, novproxy, "verify_nodes", original_verify)
        good = novproxy.generate("", "out", want=2, rounds=1, log=lambda _m: None)
        self.assertEqual(len(good), 2)


if __name__ == "__main__":
    unittest.main()
