"""captcha_solver 的单元测试。

打码平台的响应格式多变（errorId/status/solution 组合），且 token 与 UA
绑定这个细节极易踩坑，所以这里把各种响应形态都钉住。
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import captcha_solver  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)


class FakeHttp:
    """按调用顺序返回预设响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "body": json})
        if not self.responses:
            raise AssertionError("FakeHttp 响应已用尽")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(responses, **kwargs):
    http = FakeHttp(responses)
    kwargs.setdefault("sleep", lambda _s: None)
    client = captcha_solver.YesCaptchaClient("test-key", http_post=http, **kwargs)
    return client, http


class ClientTests(unittest.TestCase):
    def test_solves_turnstile(self):
        client, http = make_client([
            FakeResponse({"errorId": 0, "taskId": "t-1"}),
            FakeResponse({"errorId": 0, "status": "processing"}),
            FakeResponse({"errorId": 0, "status": "ready",
                          "solution": {"token": "tok-abc", "userAgent": "UA-1"}}),
        ])
        out = client.solve_turnstile("https://x.ai/login", "site-key")
        self.assertEqual(out["token"], "tok-abc")
        self.assertEqual(out["user_agent"], "UA-1")
        # 第一次是 createTask，后面是 getTaskResult
        self.assertTrue(http.calls[0]["url"].endswith("/createTask"))
        self.assertTrue(http.calls[1]["url"].endswith("/getTaskResult"))
        self.assertEqual(http.calls[0]["body"]["task"]["type"], "TurnstileTaskProxyless")
        self.assertEqual(http.calls[0]["body"]["task"]["websiteKey"], "site-key")
        self.assertEqual(http.calls[0]["body"]["clientKey"], "test-key")

    def test_create_error_raises(self):
        client, _ = make_client([
            FakeResponse({"errorId": 1, "errorCode": "ERROR_KEY_DENIED",
                          "errorDescription": "key 无效"}),
        ])
        with self.assertRaises(captcha_solver.CaptchaError) as ctx:
            client.solve_turnstile("https://x.ai/", "k")
        self.assertIn("ERROR_KEY_DENIED", str(ctx.exception))

    def test_missing_task_id_raises(self):
        client, _ = make_client([FakeResponse({"errorId": 0})])
        with self.assertRaises(captcha_solver.CaptchaError):
            client.solve_turnstile("https://x.ai/", "k")

    def test_ready_without_token_raises(self):
        client, _ = make_client([
            FakeResponse({"errorId": 0, "taskId": "t"}),
            FakeResponse({"errorId": 0, "status": "ready", "solution": {}}),
        ])
        with self.assertRaises(captcha_solver.CaptchaError):
            client.solve_turnstile("https://x.ai/", "k")

    def test_result_error_raises(self):
        client, _ = make_client([
            FakeResponse({"errorId": 0, "taskId": "t"}),
            FakeResponse({"errorId": 1, "errorCode": "ERROR_NO_SUCH_TASK"}),
        ])
        with self.assertRaises(captcha_solver.CaptchaError):
            client.solve_turnstile("https://x.ai/", "k")

    def test_timeout_when_always_processing(self):
        # sleep 是假的，不会推进真实时间；用假时钟驱动 deadline，
        # 否则轮询会瞬间跑完预设响应而报出无关的「响应已用尽」。
        responses = [FakeResponse({"errorId": 0, "taskId": "t"})]
        responses += [FakeResponse({"errorId": 0, "status": "processing"})] * 50
        http = FakeHttp(responses)
        clock = {"t": 1000.0}

        def fake_sleep(seconds):
            clock["t"] += seconds

        original_time = captcha_solver.time.time
        captcha_solver.time.time = lambda: clock["t"]
        self.addCleanup(setattr, captcha_solver.time, "time", original_time)

        client = captcha_solver.YesCaptchaClient("k", http_post=http, sleep=fake_sleep)
        with self.assertRaises(captcha_solver.CaptchaError) as ctx:
            client.solve_turnstile("https://x.ai/", "k", timeout_sec=10)
        self.assertIn("超时", str(ctx.exception))

    def test_empty_client_key_rejected(self):
        for value in ["", "   ", None]:
            with self.assertRaises(captcha_solver.CaptchaError):
                captcha_solver.YesCaptchaClient(value)

    def test_missing_url_or_key_rejected(self):
        client, _ = make_client([])
        with self.assertRaises(captcha_solver.CaptchaError):
            client.solve_turnstile("", "k")
        with self.assertRaises(captcha_solver.CaptchaError):
            client.solve_turnstile("https://x.ai/", "")

    def test_http_error_status_raises(self):
        client, _ = make_client([FakeResponse("boom", status=502)])
        with self.assertRaises(captcha_solver.CaptchaError):
            client.solve_turnstile("https://x.ai/", "k")

    def test_network_exception_wrapped(self):
        client, _ = make_client([RuntimeError("connection reset")])
        with self.assertRaises(captcha_solver.CaptchaError) as ctx:
            client.solve_turnstile("https://x.ai/", "k")
        self.assertIn("connection reset", str(ctx.exception))

    def test_invalid_json_raises(self):
        client, _ = make_client([FakeResponse("<html>502</html>")])
        with self.assertRaises(captcha_solver.CaptchaError):
            client.solve_turnstile("https://x.ai/", "k")

    def test_custom_api_base(self):
        client, http = make_client([
            FakeResponse({"errorId": 0, "taskId": "t"}),
            FakeResponse({"errorId": 0, "status": "ready", "solution": {"token": "x"}}),
        ], api_base="https://cn.yescaptcha.com/")
        client.solve_turnstile("https://x.ai/", "k")
        self.assertTrue(http.calls[0]["url"].startswith("https://cn.yescaptcha.com/createTask"))

    def test_balance(self):
        client, _ = make_client([FakeResponse({"errorId": 0, "balance": 1234})])
        self.assertEqual(client.balance(), 1234)

    def test_balance_error_raises(self):
        client, _ = make_client([FakeResponse({"errorId": 1, "errorCode": "X"})])
        with self.assertRaises(captcha_solver.CaptchaError):
            client.balance()


class FakePage:
    """最小页面桩：run_js 按脚本内容返回预设值。"""

    def __init__(self, sitekey="", ua="UA-BROWSER", inject_result=None, url="https://x.ai/login"):
        self.sitekey = sitekey
        self.ua = ua
        self.inject_result = inject_result if inject_result is not None else {"filled": 1, "called": 0}
        self.url = url
        self.injected = None

    def run_js(self, script, *args):
        if "navigator.userAgent" in script:
            return self.ua
        if args:
            self.injected = args[0]
            return json.dumps(self.inject_result)
        if "data-sitekey" in script or "sitekey" in script:
            return self.sitekey
        return ""


class ExtractKeyTests(unittest.TestCase):
    def test_extracts_sitekey(self):
        self.assertEqual(captcha_solver.extract_turnstile_key(FakePage("0xABC123")), "0xABC123")

    def test_returns_empty_when_absent(self):
        self.assertEqual(captcha_solver.extract_turnstile_key(FakePage("")), "")

    def test_survives_run_js_exception(self):
        class Boom:
            def run_js(self, *_a, **_k):
                raise RuntimeError("no js")
        self.assertEqual(captcha_solver.extract_turnstile_key(Boom()), "")


class InjectTests(unittest.TestCase):
    def test_inject_reports_counts(self):
        page = FakePage(inject_result={"filled": 2, "called": 1})
        out = captcha_solver.inject_turnstile_token(page, "tok")
        self.assertEqual(out["filled"], 2)
        self.assertEqual(page.injected, "tok")

    def test_inject_survives_exception(self):
        class Boom:
            def run_js(self, *_a, **_k):
                raise RuntimeError("gone")
        out = captcha_solver.inject_turnstile_token(Boom(), "tok")
        self.assertEqual(out["filled"], 0)
        self.assertIn("error", out)


class SolveAndInjectTests(unittest.TestCase):
    def _patch_client(self, responses):
        http = FakeHttp(responses)
        original = captcha_solver.YesCaptchaClient

        def factory(client_key, **kwargs):
            kwargs.pop("http_post", None)
            return original(client_key, http_post=http, sleep=lambda _s: None, **kwargs)

        captcha_solver.YesCaptchaClient = factory
        self.addCleanup(setattr, captcha_solver, "YesCaptchaClient", original)
        return http

    def test_happy_path(self):
        self._patch_client([
            FakeResponse({"errorId": 0, "taskId": "t"}),
            FakeResponse({"errorId": 0, "status": "ready",
                          "solution": {"token": "tok-1", "userAgent": "UA-BROWSER"}}),
        ])
        page = FakePage("0xKEY", ua="UA-BROWSER")
        out = captcha_solver.solve_and_inject(page, "k")
        self.assertTrue(out["ok"])
        self.assertEqual(out["token_len"], 5)
        self.assertTrue(out["ua_matched"])

    def test_no_sitekey_is_reported_not_raised(self):
        out = captcha_solver.solve_and_inject(FakePage(""), "k")
        self.assertFalse(out["ok"])
        self.assertIn("sitekey", out["reason"])

    def test_solver_failure_is_reported_not_raised(self):
        self._patch_client([FakeResponse({"errorId": 1, "errorCode": "NO_MONEY"})])
        out = captcha_solver.solve_and_inject(FakePage("0xKEY"), "k")
        self.assertFalse(out["ok"])
        self.assertIn("NO_MONEY", out["reason"])

    def test_ua_mismatch_flagged(self):
        """token 与解题 UA 绑定；UA 不一致必须标出来，否则会静默失败。"""
        self._patch_client([
            FakeResponse({"errorId": 0, "taskId": "t"}),
            FakeResponse({"errorId": 0, "status": "ready",
                          "solution": {"token": "tok", "userAgent": "UA-OTHER"}}),
        ])
        page = FakePage("0xKEY", ua="UA-BROWSER")
        out = captcha_solver.solve_and_inject(page, "k")
        self.assertFalse(out["ua_matched"])

    def test_missing_client_key_reported(self):
        out = captcha_solver.solve_and_inject(FakePage("0xKEY"), "")
        self.assertFalse(out["ok"])
        self.assertIn("clientKey", out["reason"])

    def test_inject_failure_reported(self):
        self._patch_client([
            FakeResponse({"errorId": 0, "taskId": "t"}),
            FakeResponse({"errorId": 0, "status": "ready",
                          "solution": {"token": "tok", "userAgent": "UA-BROWSER"}}),
        ])
        page = FakePage("0xKEY", ua="UA-BROWSER", inject_result={"filled": 0, "called": 0})
        out = captcha_solver.solve_and_inject(page, "k")
        self.assertFalse(out["ok"])
        self.assertIn("注入", out["reason"])


if __name__ == "__main__":
    unittest.main()
