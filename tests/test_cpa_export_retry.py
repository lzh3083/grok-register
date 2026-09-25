"""验证 CPA 导出的代理传输失败重试。

背景（实测）
------------
住宅代理节点会在注册过程中失效。注册本身耗时较长（打开页面、拉验证码、
过 Cloudflare、拿 SSO），等走到 CPA 导出这一步时，当初那个节点可能已经
死了，上游返回固定文案 "msg: connect proxy error"。

实测一次 3 账号跑批里就有 2 个栽在这里：注册成功、token 有效、账号已
入库，但 CPA 凭证没导出。换一个健康节点重试就能救回来。

这里钉住两件事：
1. "connect proxy error" 必须被识别为传输失败（它的词序和分类器里已有
   的 "connect error" 不同，早期版本因此漏判，失效节点不被剔除）。
2. 导出遇到传输失败时会换新租约重试一次，且无论成败都归还租约。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import grok_register_ttk  # noqa: E402
from cpa_xai import browser_confirm  # noqa: E402
from proxy_pool_v3 import classify_proxy_network_error, is_proxy_transport_exception  # noqa: E402


class TransportClassificationTests(unittest.TestCase):
    def test_novproxy_error_text_is_transport(self):
        """这是本次事故的直接原因：词序不同导致漏判。"""
        for text in [
            "msg: connect proxy error",
            "connect proxy error",
            "proxy connect error",
            "xAI discovery failed HTTP 403: msg: connect proxy error",
        ]:
            self.assertTrue(is_proxy_transport_exception(text), text)
            self.assertEqual(classify_proxy_network_error(text), "hard_transport", text)

    def test_ssl_error_still_transport(self):
        self.assertTrue(is_proxy_transport_exception("ERR_SSL_PROTOCOL_ERROR"))

    def test_application_error_not_transport(self):
        for text in ["账号已存在", "invalid verification code", "some random error"]:
            self.assertFalse(is_proxy_transport_exception(text), text)


class ExportFailureDetectionTests(unittest.TestCase):
    def test_ok_result_is_not_failure(self):
        self.assertFalse(grok_register_ttk._cpa_export_is_transport_failure({"ok": True}))

    def test_transport_error_in_result_is_detected(self):
        result = {"ok": False, "error": "xAI discovery failed HTTP 403: msg: connect proxy error"}
        self.assertTrue(grok_register_ttk._cpa_export_is_transport_failure(result))

    def test_exception_argument_is_detected(self):
        exc = RuntimeError("msg: connect proxy error")
        self.assertTrue(grok_register_ttk._cpa_export_is_transport_failure(
            {"ok": False, "error": str(exc)}, exc))

    def test_application_failure_is_not_retried(self):
        result = {"ok": False, "error": "auth failed: invalid credentials"}
        self.assertFalse(grok_register_ttk._cpa_export_is_transport_failure(result))

    def test_non_dict_result_is_safe(self):
        self.assertFalse(grok_register_ttk._cpa_export_is_transport_failure(None))
        self.assertFalse(grok_register_ttk._cpa_export_is_transport_failure("boom"))


class _FakeLease:
    def __init__(self, node_id):
        self.node_id = node_id


class FreshLeaseTests(unittest.TestCase):
    """_with_fresh_lease 必须在取新租约前释放旧租约，并保证归还。"""

    def _patch_pool(self, lease, acquire_result="new-lease"):
        calls = {"end": 0, "begin": 0}
        state = {"lease": lease}

        def fake_current():
            return state["lease"]

        def fake_end(success=False, transport_error=None):
            calls["end"] += 1
            state["lease"] = None

        def fake_begin(**kwargs):
            calls["begin"] += 1
            if acquire_result == "raise":
                raise RuntimeError("no node available")
            return acquire_result

        patches = [
            patch("proxy_pool_v3.current_proxy_lease", side_effect=fake_current),
            patch("proxy_pool_v3.end_registration_slot", side_effect=fake_end),
            patch("proxy_pool_v3.begin_registration_slot", side_effect=fake_begin),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return calls

    def test_releases_old_lease_before_acquiring(self):
        """旧租约不释放的话 begin_registration_slot 会直接拒绝。"""
        calls = self._patch_pool(_FakeLease("dead-node"))
        ran = {"n": 0}

        def work():
            ran["n"] += 1
            return {"ok": True}

        result = grok_register_ttk._with_fresh_lease(work, lambda _m: None)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(ran["n"], 1)
        self.assertEqual(calls["end"], 2)   # 释放旧租约 + 归还新租约
        self.assertEqual(calls["begin"], 1)

    def test_no_existing_lease_skips_release(self):
        calls = self._patch_pool(None)
        grok_register_ttk._with_fresh_lease(lambda: {"ok": True}, lambda _m: None)
        self.assertEqual(calls["end"], 1)   # 只归还新租约
        self.assertEqual(calls["begin"], 1)

    def test_acquire_failure_returns_none(self):
        self._patch_pool(None, acquire_result="raise")
        self.assertIsNone(grok_register_ttk._with_fresh_lease(lambda: {"ok": True}, lambda _m: None))

    def test_lease_returned_even_when_work_raises(self):
        calls = self._patch_pool(None)

        def boom():
            raise RuntimeError("export blew up")

        with self.assertRaises(RuntimeError):
            grok_register_ttk._with_fresh_lease(boom, lambda _m: None)
        self.assertEqual(calls["end"], 1)


class MaybeExportRetryTests(unittest.TestCase):
    """端到端：首次传输失败 → 换节点重试成功。"""

    def setUp(self):
        self.logs = []
        self.cfg = dict(grok_register_ttk.config)
        self.addCleanup(self._restore_config)
        grok_register_ttk.config["cpa_export_enabled"] = True

    def _restore_config(self):
        grok_register_ttk.config.clear()
        grok_register_ttk.config.update(self.cfg)

    def _run(self, side_effects):
        calls = {"n": 0}

        def fake_export(**_kwargs):
            index = min(calls["n"], len(side_effects) - 1)
            calls["n"] += 1
            item = side_effects[index]
            if isinstance(item, Exception):
                raise item
            return item

        with patch("cpa_export.export_cpa_xai_for_account", side_effect=fake_export), \
             patch.object(grok_register_ttk, "_with_fresh_lease",
                          side_effect=lambda fn, _log, cancel_callback=None: fn()):
            result = grok_register_ttk.maybe_export_cpa_xai_after_success(
                email="a@example.com", password="pw", sso="sso-value",
                log_callback=self.logs.append)
        return result, calls["n"]

    def test_transport_failure_triggers_retry_and_succeeds(self):
        result, attempts = self._run([
            RuntimeError("xAI discovery failed HTTP 403: msg: connect proxy error"),
            {"ok": True, "path": "/tmp/xai-a@example.com.json"},
        ])
        self.assertTrue(result.get("ok"))
        self.assertEqual(attempts, 2)
        self.assertTrue(any("换一个健康节点重试" in line for line in self.logs), self.logs)
        self.assertTrue(any("换节点后 CPA OIDC 导出成功" in line for line in self.logs), self.logs)

    def test_application_failure_does_not_retry(self):
        result, attempts = self._run([RuntimeError("auth failed: bad credentials")])
        self.assertFalse(result.get("ok"))
        self.assertEqual(attempts, 1)

    def test_both_attempts_fail_keeps_account(self):
        result, attempts = self._run([
            RuntimeError("msg: connect proxy error"),
            RuntimeError("msg: connect proxy error"),
        ])
        self.assertFalse(result.get("ok"))
        self.assertEqual(attempts, 2)
        self.assertTrue(any("账号已保留" in line for line in self.logs), self.logs)

    def test_success_first_try_no_retry(self):
        result, attempts = self._run([{"ok": True, "path": "/tmp/ok.json"}])
        self.assertTrue(result.get("ok"))
        self.assertEqual(attempts, 1)

    def test_disabled_export_short_circuits(self):
        grok_register_ttk.config["cpa_export_enabled"] = False
        result = grok_register_ttk.maybe_export_cpa_xai_after_success(
            email="a@example.com", password="pw")
        self.assertTrue(result.get("skipped"))


if __name__ == "__main__":
    unittest.main()


class WaitPageReadyTests(unittest.TestCase):
    """设备授权前的页面就绪等待。

    实测事故：cookie 注入后只 sleep 1 秒就继续，页面偶尔还没渲染完
    （DOM 为空），后续关 cookie 横幅、点 Continue/Allow 全部找不到元素，
    最后表现为 authorization_pending 一直轮询到超时 —— 报错信息完全
    不指向真正原因。同一账号补跑就成功，说明是时序而非账号问题。
    """

    def _page(self, states):
        import json as _json

        class FakePage:
            def __init__(self):
                self.states = list(states)
                self.calls = 0

            def run_js(self, _script):
                value = self.states[min(self.calls, len(self.states) - 1)]
                self.calls += 1
                return _json.dumps(value)

        return FakePage()

    def test_ready_immediately(self):
        page = self._page([{"ready": "complete", "length": 120}])
        self.assertTrue(browser_confirm._wait_page_ready(page, lambda _m: None))
        self.assertEqual(page.calls, 1)

    def test_waits_through_slow_load(self):
        page = self._page([
            {"ready": "loading", "length": 0},
            {"ready": "loading", "length": 0},
            {"ready": "complete", "length": 80},
        ])
        self.assertTrue(browser_confirm._wait_page_ready(page, lambda _m: None))
        self.assertEqual(page.calls, 3)

    def test_complete_but_empty_body_is_not_ready(self):
        """readyState=complete 但正文为空，仍不算就绪 —— 这正是踩过的坑。"""
        page = self._page([{"ready": "complete", "length": 0}])
        self.assertFalse(browser_confirm._wait_page_ready(page, lambda _m: None, timeout_sec=1.5))

    def test_timeout_logs_and_returns_false(self):
        logs = []
        page = self._page([{"ready": "loading", "length": 0}])
        self.assertFalse(browser_confirm._wait_page_ready(page, logs.append, timeout_sec=1.5))
        self.assertTrue(any("等待就绪超时" in line for line in logs), logs)

    def test_survives_run_js_exception(self):
        class Boom:
            def run_js(self, _script):
                raise RuntimeError("page gone")

        self.assertFalse(browser_confirm._wait_page_ready(Boom(), lambda _m: None, timeout_sec=1.5))


if __name__ == "__main__":
    unittest.main()
