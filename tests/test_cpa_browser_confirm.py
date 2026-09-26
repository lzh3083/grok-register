"""验证 CPA 设备授权能识别 Chromium 网络错误页并立即失败。

实测踩过的坑：住宅节点失效后网关拿明文 HTTP 回 TLS 握手，授权页停在
ERR_SSL_PROTOCOL_ERROR。旧实现只认认证类错误（密码错、Cloudflare 拦截），
把错误页当普通页面继续找按钮，静默空转 300 秒才报
"browser confirm timeout phase=consent login_attempts=0" —— 报错完全不指向
真正原因，而且每个账号白等 5 分钟。
"""

import time
import unittest

from cpa_xai.browser_confirm import (
    BrowserConfirmError,
    _detect_network_error_page,
    approve_device_code,
)
from proxy_pool_v3 import is_proxy_transport_exception


class FakePage:
    """最小页面替身：只提供 _page_url / _visible_text 用到的接口。"""

    def __init__(self, url="", text=""):
        self.url = url
        self._text = text

    def run_js(self, script, *args, **kwargs):
        return self._text

    def ele(self, *args, **kwargs):
        return None

    def get(self, *args, **kwargs):
        return None


class NetworkErrorPageDetectionTests(unittest.TestCase):
    def test_detects_chromium_error_codes(self):
        cases = (
            "This site can't provide a secure connection accounts.x.ai sent an invalid response. ERR_SSL_PROTOCOL_ERROR Reload",
            "This site can't be reached ERR_TUNNEL_CONNECTION_FAILED",
            "ERR_CONNECTION_TIMED_OUT",
            "ERR_CONNECTION_RESET",
            "ERR_SOCKS_CONNECTION_FAILED",
            "ERR_PROXY_CONNECTION_FAILED",
            "ERR_NAME_NOT_RESOLVED",
        )
        for text in cases:
            with self.subTest(text=text[:40]):
                self.assertTrue(_detect_network_error_page(text, "https://accounts.x.ai/x"))

    def test_detects_chrome_error_url(self):
        self.assertTrue(_detect_network_error_page("", "chrome-error://chromewebdata/"))

    def test_normal_pages_are_not_flagged(self):
        normal = (
            ("Create Your Grok Account | Grok", "https://accounts.x.ai/sign-up?redirect=grok-com"),
            ("Authorize Grok Build Read your profile Allow", "https://accounts.x.ai/oauth2/device/consent?user_code=X"),
            ("Device Authorized You can close this window", "https://accounts.x.ai/oauth2/device/done"),
        )
        for text, url in normal:
            with self.subTest(url=url):
                self.assertIsNone(_detect_network_error_page(text, url))

    def test_cloudflare_block_is_not_a_network_error(self):
        """Cloudflare 拦截页是站点返回的 HTML，不是网络层故障。"""
        self.assertIsNone(_detect_network_error_page(
            "Attention Required! | Cloudflare Sorry, you have been blocked",
            "https://accounts.x.ai/sign-up",
        ))

    def test_reported_error_maps_to_transport_failure(self):
        """识别出的错误必须被上层判成传输失败，否则不会换节点重试。"""
        for text, url in (
            ("ERR_SSL_PROTOCOL_ERROR", "https://accounts.x.ai/x"),
            ("ERR_TUNNEL_CONNECTION_FAILED", "https://accounts.x.ai/x"),
            ("ERR_CONNECTION_TIMED_OUT", "https://accounts.x.ai/x"),
            ("", "chrome-error://chromewebdata/"),
        ):
            detail = _detect_network_error_page(text, url)
            with self.subTest(detail=detail):
                message = "auth failed: network error page: %s" % detail
                self.assertTrue(is_proxy_transport_exception(message))


class ApproveDeviceCodeTests(unittest.TestCase):
    def _approve(self, page, timeout_sec=240.0):
        return approve_device_code(
            page,
            verification_uri_complete="https://accounts.x.ai/oauth2/device?user_code=AAAA-BBBB",
            email="a@example.com",
            password="pw",
            user_code="AAAA-BBBB",
            timeout_sec=timeout_sec,
        )

    def test_network_error_page_fails_fast_instead_of_spinning(self):
        page = FakePage(
            url="https://accounts.x.ai/oauth2/device?user_code=AAAA-BBBB",
            text="This site can't provide a secure connection ERR_SSL_PROTOCOL_ERROR Reload",
        )
        started = time.time()
        with self.assertRaises(BrowserConfirmError) as ctx:
            self._approve(page, timeout_sec=240.0)
        elapsed = time.time() - started
        # 关键：不能空转到 240 秒超时
        self.assertLess(elapsed, 30.0, "错误页应立刻失败，实测耗时 %.1fs" % elapsed)
        self.assertIn("network error page", str(ctx.exception))
        self.assertIn("ERR_SSL_PROTOCOL_ERROR", str(ctx.exception))

    def test_chrome_error_url_fails_fast(self):
        page = FakePage(url="chrome-error://chromewebdata/", text="")
        started = time.time()
        with self.assertRaises(BrowserConfirmError) as ctx:
            self._approve(page, timeout_sec=240.0)
        self.assertLess(time.time() - started, 30.0)
        self.assertIn("network error page", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
