"""降智检测的单元测试。

这里钉住几个实测得到的结论，避免以后被"顺手优化"改坏：

1. 判据是官方字段 `usage.completion_tokens_details.reasoning_tokens`，
   不是"有没有 thinking 字段"。实测 cli-chat-proxy 直连时
   reasoning_content / thinking 都不返回，但 reasoning_tokens 有值 ——
   若按字段存在性判定，会把所有健康账号误判成降智。
2. 取不到官方字段时回退用 total - prompt - completion 的差值。
3. HTTP 401/403 等属于"账号不可用"，必须与"降智"区分开，否则会把
   掉 token 的账号当成降智账号处理。
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import quality_probe as qp  # noqa: E402


class _FakeResponse:
    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _record(email="a@b.com", token="tok"):
    return {"email": email, "access_token": token, "base_url": "https://example.test/v1"}


class ExtractReasoningTokensTests(unittest.TestCase):
    def test_prefers_official_field(self):
        usage = {
            "prompt_tokens": 238, "completion_tokens": 3, "total_tokens": 6196,
            "completion_tokens_details": {"reasoning_tokens": 5581},
        }
        self.assertEqual(qp.extract_reasoning_tokens(usage), 5581)

    def test_falls_back_to_delta(self):
        """只有 total 时用差值推算（实测直连场景）。"""
        usage = {"prompt_tokens": 238, "completion_tokens": 3, "total_tokens": 6196}
        self.assertEqual(qp.extract_reasoning_tokens(usage), 5955)

    def test_zero_when_no_signal(self):
        self.assertEqual(qp.extract_reasoning_tokens({}), 0)
        self.assertEqual(qp.extract_reasoning_tokens(None), 0)

    def test_negative_delta_clamped_to_zero(self):
        """差值算出来是负数时按 0 处理，不能返回负数。"""
        usage = {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 520}
        self.assertEqual(qp.extract_reasoning_tokens(usage), 0)

    def test_camel_case_keys(self):
        usage = {"promptTokens": 10, "completionTokens": 5, "totalTokens": 1000}
        self.assertEqual(qp.extract_reasoning_tokens(usage), 985)


class ClassifyTests(unittest.TestCase):
    def test_zero_is_hard(self):
        """推理量为 0 = 降智。"""
        self.assertEqual(qp.classify(0), qp.VERDICT_HARD)

    def test_below_threshold_is_soft(self):
        self.assertEqual(qp.classify(20), qp.VERDICT_SOFT)
        self.assertEqual(qp.classify(49), qp.VERDICT_SOFT)
        self.assertEqual(qp.classify(150, soft_threshold=200), qp.VERDICT_SOFT)

    def test_healthy_range(self):
        """实测健康账号通常在数百至数千推理 token。"""
        for value in (50, 200, 3115, 5581, 8077):
            self.assertEqual(qp.classify(value), qp.VERDICT_HEALTHY, value)

    def test_garbage_is_hard(self):
        self.assertEqual(qp.classify(None), qp.VERDICT_HARD)
        self.assertEqual(qp.classify("abc"), qp.VERDICT_HARD)


class FailureClassificationTests(unittest.TestCase):
    def test_401_is_risk_not_hard(self):
        """token 失效属于账号不可用，不能算降智。"""
        self.assertEqual(qp.classify_failure(401, ""), qp.VERDICT_RISK)
        self.assertEqual(qp.classify_failure(403, ""), qp.VERDICT_RISK)
        self.assertEqual(qp.classify_failure(429, ""), qp.VERDICT_RISK)

    def test_marker_in_body(self):
        self.assertEqual(qp.classify_failure(200, "invalid token"), qp.VERDICT_RISK)

    def test_5xx_is_error(self):
        self.assertEqual(qp.classify_failure(502, ""), qp.VERDICT_ERROR)


class ProbeAccountTests(unittest.TestCase):
    def test_healthy_account(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse({
                "choices": [{"message": {"content": "58"}}],
                "usage": {
                    "prompt_tokens": 238, "completion_tokens": 3, "total_tokens": 6196,
                    "completion_tokens_details": {"reasoning_tokens": 5581},
                },
            })

        result = qp.probe_account(_record(), urlopen=fake_urlopen)
        self.assertEqual(result["verdict"], qp.VERDICT_HEALTHY)
        self.assertEqual(result["reasoning_tokens"], 5581)
        self.assertEqual(result["content"], "58")

    def test_degraded_account_has_no_reasoning(self):
        """降智账号：HTTP 200、正文正常，但推理量为 0。"""
        def fake_urlopen(request, timeout=None):
            return _FakeResponse({
                "choices": [{"message": {"content": "58"}}],
                "usage": {"prompt_tokens": 238, "completion_tokens": 3, "total_tokens": 241},
            })

        result = qp.probe_account(_record(), urlopen=fake_urlopen)
        self.assertEqual(result["verdict"], qp.VERDICT_HARD)
        self.assertEqual(result["reasoning_tokens"], 0)

    def test_missing_token_is_risk_without_request(self):
        called = []

        def fake_urlopen(request, timeout=None):
            called.append(1)
            return _FakeResponse({})

        result = qp.probe_account({"email": "x@y.com"}, urlopen=fake_urlopen)
        self.assertEqual(result["verdict"], qp.VERDICT_RISK)
        self.assertEqual(called, [], "缺 token 时不该发请求")

    def test_http_error_is_captured_not_raised(self):
        import urllib.error

        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                "https://example.test", 401, "Unauthorized", {}, None)

        result = qp.probe_account(_record(), urlopen=fake_urlopen)
        self.assertEqual(result["verdict"], qp.VERDICT_RISK)
        self.assertEqual(result["status"], 401)

    def test_transport_error_is_error_not_risk(self):
        def fake_urlopen(request, timeout=None):
            raise OSError("connection reset")

        result = qp.probe_account(_record(), urlopen=fake_urlopen)
        self.assertEqual(result["verdict"], qp.VERDICT_ERROR)

    def test_malformed_json_is_error(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(b"not json at all")

        result = qp.probe_account(_record(), urlopen=fake_urlopen)
        self.assertEqual(result["verdict"], qp.VERDICT_ERROR)

    def test_never_raises_on_weird_payload(self):
        """响应结构异常时不能抛异常，只能降级为 error。"""
        def fake_urlopen(request, timeout=None):
            return _FakeResponse({"choices": "not-a-list", "usage": []})

        result = qp.probe_account(_record(), urlopen=fake_urlopen)
        self.assertEqual(result["verdict"], qp.VERDICT_HARD)

    def test_client_identity_headers_sent(self):
        """必须带客户端身份头，否则 xAI 返回 426。"""
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen.update(dict(request.headers))
            return _FakeResponse({"choices": [{"message": {"content": "58"}}],
                                  "usage": {"completion_tokens_details": {"reasoning_tokens": 500}}})

        qp.probe_account(_record(), urlopen=fake_urlopen)
        self.assertEqual(seen.get("X-xai-token-auth"), "xai-grok-cli")
        self.assertIn("grok-pager", seen.get("User-agent", ""))

    def test_auth_header_uses_bearer(self):
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen.update(dict(request.headers))
            return _FakeResponse({"choices": [], "usage": {}})

        qp.probe_account(_record(token="my-token"), urlopen=fake_urlopen)
        self.assertEqual(seen.get("Authorization"), "Bearer my-token")

    def test_url_appends_chat_path(self):
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            return _FakeResponse({"choices": [], "usage": {}})

        qp.probe_account(_record(), urlopen=fake_urlopen)
        self.assertEqual(seen["url"], "https://example.test/v1/chat/completions")

    def test_url_not_double_appended(self):
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            return _FakeResponse({"choices": [], "usage": {}})

        record = _record()
        record["base_url"] = "https://example.test/v1/chat/completions"
        qp.probe_account(record, urlopen=fake_urlopen)
        self.assertEqual(seen["url"], "https://example.test/v1/chat/completions")


class SummarizeTests(unittest.TestCase):
    def test_counts_and_email_lists(self):
        results = [
            {"email": "a@x.com", "verdict": qp.VERDICT_HEALTHY},
            {"email": "b@x.com", "verdict": qp.VERDICT_HARD},
            {"email": "c@x.com", "verdict": qp.VERDICT_HARD},
            {"email": "d@x.com", "verdict": qp.VERDICT_RISK},
            {"email": "e@x.com", "verdict": qp.VERDICT_ERROR},
            {"email": "f@x.com", "verdict": qp.VERDICT_SOFT},
        ]
        summary = qp.summarize(results)
        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["healthy"], 1)
        self.assertEqual(summary["hard"], 2)
        self.assertEqual(summary["risk"], 1)
        self.assertEqual(summary["soft"], 1)
        self.assertEqual(summary["error"], 1)
        self.assertEqual(summary["degraded_emails"], ["b@x.com", "c@x.com"])
        self.assertEqual(summary["risk_emails"], ["d@x.com"])

    def test_unknown_verdict_counted_as_error(self):
        summary = qp.summarize([{"email": "a@x.com", "verdict": "weird"}])
        self.assertEqual(summary["error"], 1)

    def test_empty(self):
        summary = qp.summarize([])
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["degraded_emails"], [])


class ProbeAllTests(unittest.TestCase):
    def test_order_preserved(self):
        records = [_record(email="%d@x.com" % i) for i in range(5)]

        def fake_probe(record, **kwargs):
            return {"email": record["email"], "verdict": qp.VERDICT_HEALTHY}

        original = qp.probe_account
        qp.probe_account = fake_probe
        try:
            results = qp.probe_all(records, workers=3, log=None)
        finally:
            qp.probe_account = original
        self.assertEqual([r["email"] for r in results],
                         ["%d@x.com" % i for i in range(5)])

    def test_single_record_serial_path(self):
        records = [_record()]

        def fake_probe(record, **kwargs):
            return {"email": record["email"], "verdict": qp.VERDICT_HEALTHY}

        original = qp.probe_account
        qp.probe_account = fake_probe
        try:
            results = qp.probe_all(records, workers=1, log=None)
        finally:
            qp.probe_account = original
        self.assertEqual(len(results), 1)


class LoadCredentialsTests(unittest.TestCase):
    def test_reads_directory_and_backfills_email(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "xai-user@example.com.json"
            path.write_text(json.dumps({"access_token": "t"}), encoding="utf-8")
            records = qp.load_credentials(tmp)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["email"], "user@example.com")
        self.assertEqual(records[0]["access_token"], "t")

    def test_missing_directory_returns_empty(self):
        self.assertEqual(qp.load_credentials("/nonexistent/path/xyz"), [])

    def test_skips_broken_json(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bad.json").write_text("{not json", encoding="utf-8")
            records = qp.load_credentials(tmp)
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()


class TokenRefreshTests(unittest.TestCase):
    """过期凭据必须先刷新再探测。

    实测事故：凭据里的 access_token 只有 6 小时有效期，直接拿过期的去
    探测会得到 HTTP 401，看起来像"账号被封"，实际只是没刷新。
    """

    def test_expired_detection(self):
        self.assertTrue(qp._token_expired("2000-01-01T00:00:00Z"))
        self.assertFalse(qp._token_expired("2999-01-01T00:00:00Z"))
        # 无法解析时按未过期处理，避免误触发刷新
        self.assertFalse(qp._token_expired(""))
        self.assertFalse(qp._token_expired("not-a-date"))

    def test_refresh_returns_new_token(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse({"access_token": "fresh-token"})

        record = {"refresh_token": "r", "token_endpoint": "https://auth.test/token"}
        self.assertEqual(qp.refresh_access_token(record, urlopen=fake_urlopen), "fresh-token")

    def test_refresh_without_material_returns_none(self):
        self.assertIsNone(qp.refresh_access_token({}))
        self.assertIsNone(qp.refresh_access_token({"refresh_token": "r"}))

    def test_refresh_failure_returns_none_not_raise(self):
        def fake_urlopen(request, timeout=None):
            raise OSError("network down")

        record = {"refresh_token": "r", "token_endpoint": "https://auth.test/token"}
        self.assertIsNone(qp.refresh_access_token(record, urlopen=fake_urlopen))

    def test_expired_record_triggers_refresh_before_probe(self):
        """过期凭据应先用 refresh_token 换新 token 再发请求。"""
        calls = []

        def fake_urlopen(request, timeout=None):
            url = request.full_url
            calls.append(url)
            if "auth.test" in url:
                return _FakeResponse({"access_token": "fresh-token"})
            return _FakeResponse({
                "choices": [{"message": {"content": "58"}}],
                "usage": {"completion_tokens_details": {"reasoning_tokens": 4200}},
            })

        record = {
            "email": "a@b.com", "access_token": "stale",
            "expired": "2000-01-01T00:00:00Z",
            "refresh_token": "r", "token_endpoint": "https://auth.test/token",
            "base_url": "https://example.test/v1",
        }
        result = qp.probe_account(record, urlopen=fake_urlopen)
        self.assertTrue(result["refreshed"])
        self.assertEqual(result["verdict"], qp.VERDICT_HEALTHY)
        self.assertIn("https://auth.test/token", calls)

    def test_valid_token_skips_refresh(self):
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            return _FakeResponse({
                "choices": [{"message": {"content": "58"}}],
                "usage": {"completion_tokens_details": {"reasoning_tokens": 4200}},
            })

        record = {
            "email": "a@b.com", "access_token": "good",
            "expired": "2999-01-01T00:00:00Z",
            "refresh_token": "r", "token_endpoint": "https://auth.test/token",
            "base_url": "https://example.test/v1",
        }
        result = qp.probe_account(record, urlopen=fake_urlopen)
        self.assertFalse(result["refreshed"])
        self.assertEqual(len(calls), 1, "未过期时不该刷新")

    def test_refresh_can_be_disabled(self):
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            return _FakeResponse({
                "choices": [{"message": {"content": "58"}}],
                "usage": {"completion_tokens_details": {"reasoning_tokens": 4200}},
            })

        record = {
            "email": "a@b.com", "access_token": "stale",
            "expired": "2000-01-01T00:00:00Z",
            "refresh_token": "r", "token_endpoint": "https://auth.test/token",
            "base_url": "https://example.test/v1",
        }
        result = qp.probe_account(record, urlopen=fake_urlopen, allow_refresh=False)
        self.assertFalse(result["refreshed"])
        self.assertEqual(len(calls), 1)

    def test_refresh_uses_client_id(self):
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["body"] = request.data.decode()
            return _FakeResponse({"access_token": "fresh"})

        record = {"refresh_token": "r", "token_endpoint": "https://auth.test/token"}
        qp.refresh_access_token(record, urlopen=fake_urlopen)
        self.assertIn("client_id=" + qp.DEFAULT_CLIENT_ID, seen["body"])
        self.assertIn("grant_type=refresh_token", seen["body"])

    def test_refresh_persists_to_disk_if_path_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "xai-test@example.com.json"
            p.write_text(json.dumps({"email": "test@example.com", "access_token": "old"}), encoding="utf-8")
            record = {
                "email": "test@example.com",
                "refresh_token": "r",
                "token_endpoint": "https://auth.test/token",
                "_path": str(p),
            }

            def fake_urlopen(request, timeout=None):
                return _FakeResponse({"access_token": "new-tok", "expires_in": 3600})

            token = qp.refresh_access_token(record, urlopen=fake_urlopen)
            self.assertEqual(token, "new-tok")
            disk_data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(disk_data.get("access_token"), "new-tok")
            self.assertIn("expired", disk_data)
            self.assertNotIn("_path", disk_data)


class StreamResponseTests(unittest.TestCase):
    def test_stream_sse_collects_content_and_usage(self):
        class _FakeStreamResponse:
            def __init__(self, lines):
                self._lines = [line.encode("utf-8") if isinstance(line, str) else line for line in lines]
                self._idx = 0

            def readline(self):
                if self._idx < len(self._lines):
                    l = self._lines[self._idx]
                    self._idx += 1
                    return l
                return b""

            def __iter__(self):
                while self._idx < len(self._lines):
                    l = self._lines[self._idx]
                    self._idx += 1
                    yield l

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        lines = [
            'data: {"choices":[{"delta":{"content":"5"}}]}\n',
            'data: {"choices":[{"delta":{"content":"8"}}]}\n',
            'data: {"choices":[],"usage":{"prompt_tokens":200,"completion_tokens":2,"total_tokens":560,"completion_tokens_details":{"reasoning_tokens":358}}}\n',
            'data: [DONE]\n',
        ]

        def fake_urlopen(request, timeout=None):
            return _FakeStreamResponse(lines)

        rec = _record()
        res = qp.probe_account(rec, urlopen=fake_urlopen, stream=True)
        self.assertEqual(res["verdict"], qp.VERDICT_HEALTHY)
        self.assertEqual(res["content"], "58")
        self.assertEqual(res["reasoning_tokens"], 358)
        self.assertEqual(res["completion_tokens"], 2)
        self.assertEqual(res["total_tokens"], 560)
