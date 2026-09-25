import unittest
from unittest.mock import patch

import browser_runtime
import proxy_pool
from cpa_export import CpaExportSettings
from proxy_pool import ProxyLease


class ProxyRuntimeTests(unittest.TestCase):
    def setUp(self):
        proxy_pool._TLS.lease = None
        browser_runtime.configure_runtime({"proxy_mode": "auto", "proxy": "http://legacy:pass@127.0.0.1:7890"})

    def tearDown(self):
        proxy_pool._TLS.lease = None

    def test_auto_mode_keeps_legacy_proxy(self):
        self.assertEqual(browser_runtime.get_configured_proxy(), "http://legacy:pass@127.0.0.1:7890")

    def test_managed_lease_overrides_legacy_proxy(self):
        proxy_pool._TLS.lease = ProxyLease("node", "http://lease:pass@127.0.0.2:7890", "w", 1, 1, "a", "s")
        self.assertEqual(browser_runtime.get_configured_proxy(), "http://lease:pass@127.0.0.2:7890")

    def test_direct_lease_suppresses_legacy_proxy(self):
        proxy_pool._TLS.lease = ProxyLease("direct", "", "w", 1, 1, "a", "s")
        self.assertEqual(browser_runtime.get_configured_proxy(), "")
        self.assertEqual(browser_runtime.get_proxies(), {})

    def test_explicit_empty_proxies_bypasses_active_lease(self):
        proxy_pool._TLS.lease = ProxyLease("node", "http://lease:pass@127.0.0.2:7890", "w", 1, 1, "a", "s")
        response = object()
        with patch.object(browser_runtime.requests, "post", return_value=response) as request:
            self.assertIs(browser_runtime.http_post("https://example.invalid", proxies={}), response)
        kwargs = request.call_args.kwargs
        self.assertNotIn("proxies", kwargs)

    def test_cpa_inherits_lease_unless_explicitly_overridden(self):
        proxy_pool._TLS.lease = ProxyLease("node", "http://lease:pass@127.0.0.2:7890", "w", 1, 1, "a", "s")
        inherited = CpaExportSettings.from_config({"proxy": "http://legacy:7890", "cpa_proxy": ""})
        self.assertEqual(inherited.proxy, "http://lease:pass@127.0.0.2:7890")
        explicit = CpaExportSettings.from_config({"proxy": "http://legacy:7890", "cpa_proxy": "http://cpa:9999"})
        self.assertEqual(explicit.proxy, "http://cpa:9999")


if __name__ == "__main__":
    unittest.main()


class _FakeOptions:
    """记录 set_proxy / set_argument 的调用，用于验证代理写入路径。"""

    def __init__(self):
        self.proxy_calls = []
        self.arg_calls = []

    def set_proxy(self, value):
        self.proxy_calls.append(value)

    def set_argument(self, *args):
        self.arg_calls.append(args)


class BrowserProxyOptionTests(unittest.TestCase):
    """DrissionPage 的 set_proxy() 不支持 socks5，会静默忽略并让浏览器走直连。

    实测表现为 ERR_CONNECTION_RESET，极难排查。这里钉住：
    socks* 必须走 --proxy-server 启动参数，其它协议保持原路径。
    """

    def test_socks5_uses_start_argument_not_set_proxy(self):
        options = _FakeOptions()
        browser_runtime.apply_browser_proxy_option(options, "socks5://1.2.3.4:7001")
        self.assertEqual(options.proxy_calls, [])
        self.assertEqual(options.arg_calls, [("--proxy-server=socks5://1.2.3.4:7001",)])

    def test_socks5h_uses_start_argument(self):
        options = _FakeOptions()
        browser_runtime.apply_browser_proxy_option(options, "socks5h://1.2.3.4:7001")
        self.assertEqual(options.proxy_calls, [])
        self.assertTrue(options.arg_calls)

    def test_socks4_uses_start_argument(self):
        options = _FakeOptions()
        browser_runtime.apply_browser_proxy_option(options, "socks4://1.2.3.4:1080")
        self.assertEqual(options.proxy_calls, [])
        self.assertTrue(options.arg_calls)

    def test_http_still_uses_set_proxy(self):
        options = _FakeOptions()
        browser_runtime.apply_browser_proxy_option(options, "http://1.2.3.4:8080")
        self.assertEqual(options.proxy_calls, ["http://1.2.3.4:8080"])
        self.assertEqual(options.arg_calls, [])

    def test_http_with_auth_still_uses_set_proxy(self):
        options = _FakeOptions()
        browser_runtime.apply_browser_proxy_option(options, "http://u:p@1.2.3.4:8080")
        self.assertEqual(options.proxy_calls, ["http://u:p@1.2.3.4:8080"])

    def test_empty_proxy_is_noop(self):
        options = _FakeOptions()
        browser_runtime.apply_browser_proxy_option(options, "")
        self.assertEqual(options.proxy_calls, [])
        self.assertEqual(options.arg_calls, [])

    def test_falls_back_to_argument_when_set_proxy_absent(self):
        class NoSetProxy:
            def __init__(self):
                self.arg_calls = []

            def set_argument(self, *args):
                self.arg_calls.append(args)

        options = NoSetProxy()
        browser_runtime.apply_browser_proxy_option(options, "http://1.2.3.4:8080")
        self.assertEqual(options.arg_calls, [("--proxy-server=http://1.2.3.4:8080",)])

    def test_set_proxy_exception_falls_back_to_argument(self):
        class Boom:
            def __init__(self):
                self.arg_calls = []

            def set_proxy(self, _value):
                raise RuntimeError("unsupported")

            def set_argument(self, *args):
                self.arg_calls.append(args)

        options = Boom()
        browser_runtime.apply_browser_proxy_option(options, "http://1.2.3.4:8080")
        self.assertEqual(options.arg_calls, [("--proxy-server=http://1.2.3.4:8080",)])
