"""Regression coverage for Turnstile waiting and interactive completion (issue #79)."""

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import registration_browser


class Cancelled(Exception):
    pass


def _state(status, token=""):
    return {
        "state": status,
        "present": status != registration_browser.TURNSTILE_ABSENT,
        "token": token,
        "token_length": len(token),
        "widget_present": status in {
            registration_browser.TURNSTILE_WAITING,
            registration_browser.TURNSTILE_SOLVED,
            registration_browser.TURNSTILE_FAILED,
        },
        "iframe_present": status == registration_browser.TURNSTILE_WAITING,
        "script_present": status != registration_browser.TURNSTILE_ABSENT,
        "visible": status == registration_browser.TURNSTILE_WAITING,
    }


class TurnstileRegressionTests(unittest.TestCase):
    def test_existing_short_token_is_accepted_immediately(self):
        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_SOLVED, "ok"),
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(registration_browser, "page", object()):
            token = registration_browser.getTurnstileToken(timeout=5)

        self.assertEqual(token, "ok")

    def test_loading_can_finish_automatically(self):
        clock = {"now": 0.0}
        states = [
            _state(registration_browser.TURNSTILE_LOADING),
            _state(registration_browser.TURNSTILE_LOADING),
            _state(registration_browser.TURNSTILE_SOLVED, "ready"),
        ]

        def now():
            return clock["now"]

        def sleep(_seconds, _cancel=None):
            clock["now"] += 1.0

        with patch.object(
            registration_browser, "_read_turnstile_state", side_effect=states
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(
            registration_browser, "sleep_with_cancel", side_effect=sleep, create=True
        ), patch.object(
            registration_browser.time, "time", side_effect=now
        ), patch.object(
            registration_browser, "page", object()
        ):
            token = registration_browser.getTurnstileToken(timeout=10)

        self.assertEqual(token, "ready")

    def test_interactive_wait_can_finish_after_user_completion(self):
        clock = {"now": 0.0}
        logs = []
        states = [
            _state(registration_browser.TURNSTILE_WAITING),
            _state(registration_browser.TURNSTILE_WAITING),
            _state(registration_browser.TURNSTILE_WAITING),
            _state(registration_browser.TURNSTILE_SOLVED, "ready"),
        ]

        def now():
            return clock["now"]

        def sleep(_seconds, _cancel=None):
            clock["now"] += 2.0

        with patch.object(
            registration_browser, "_read_turnstile_state", side_effect=states
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(
            registration_browser, "sleep_with_cancel", side_effect=sleep, create=True
        ), patch.object(
            registration_browser.time, "time", side_effect=now
        ), patch.object(
            registration_browser, "page", object()
        ):
            token = registration_browser.getTurnstileToken(
                timeout=20,
                log_callback=logs.append,
            )

        self.assertEqual(token, "ready")
        self.assertTrue(any("请在当前浏览器窗口完成验证" in item for item in logs))

    def test_absent_challenge_returns_to_page_re_evaluation(self):
        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_ABSENT),
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(registration_browser, "page", object()):
            token = registration_browser.getTurnstileToken(timeout=5)

        self.assertEqual(token, "")

    def test_challenge_disappearing_while_waiting_returns_for_re_evaluation(self):
        clock = {"now": 0.0}
        states = [
            _state(registration_browser.TURNSTILE_WAITING),
            _state(registration_browser.TURNSTILE_ABSENT),
        ]

        def now():
            return clock["now"]

        def sleep(_seconds, _cancel=None):
            clock["now"] += 1.0

        with patch.object(
            registration_browser, "_read_turnstile_state", side_effect=states
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(
            registration_browser, "sleep_with_cancel", side_effect=sleep, create=True
        ), patch.object(
            registration_browser.time, "time", side_effect=now
        ), patch.object(
            registration_browser, "page", object()
        ):
            token = registration_browser.getTurnstileToken(timeout=5)

        self.assertEqual(token, "")

    def test_explicit_failed_state_stops_immediately(self):
        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_FAILED),
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(registration_browser, "page", object()):
            with self.assertRaisesRegex(Exception, "Cloudflare 人机验证失败"):
                registration_browser.getTurnstileToken(timeout=5)

    def test_interactive_wait_times_out_deterministically(self):
        clock = {"now": 0.0}

        def now():
            return clock["now"]

        def sleep(seconds, _cancel=None):
            clock["now"] += max(float(seconds), 1.0)

        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_WAITING),
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(
            registration_browser, "sleep_with_cancel", side_effect=sleep, create=True
        ), patch.object(
            registration_browser.time, "time", side_effect=now
        ), patch.object(
            registration_browser, "page", object()
        ):
            with self.assertRaisesRegex(Exception, "Turnstile 验证超时"):
                registration_browser.getTurnstileToken(timeout=2)

    def test_turnstile_wait_honors_cancel(self):
        with patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_WAITING),
        ), patch.object(
            registration_browser, "raise_if_cancelled", side_effect=Cancelled(), create=True
        ), patch.object(registration_browser, "page", object()):
            with self.assertRaises(Cancelled):
                registration_browser.getTurnstileToken(timeout=5)

    def test_state_reader_distinguishes_interactive_widget(self):
        class FakePage:
            def run_js(self, *_args):
                return json.dumps(
                    {
                        "state": "WAITING",
                        "token": "",
                        "widget_present": True,
                        "iframe_present": True,
                        "script_present": True,
                        "visible": True,
                    }
                )

        with patch.object(registration_browser, "page", FakePage()):
            state = registration_browser._read_turnstile_state()

        self.assertEqual(state["state"], registration_browser.TURNSTILE_WAITING)
        self.assertTrue(state["widget_present"])
        self.assertTrue(state["iframe_present"])
        self.assertTrue(state["visible"])
        self.assertEqual(state["token_length"], 0)

    def test_final_sso_page_uses_same_turnstile_waiter(self):
        class FakePage:
            def __init__(self):
                self.states = iter(["final-page", "not-final-page"])

            def run_js(self, *_args):
                return next(self.states, "not-final-page")

            def cookies(self, **_kwargs):
                return [{"name": "sso", "value": "sso-token"}]

        waiter = Mock(return_value="ready")
        with patch.object(registration_browser, "page", FakePage()), patch.object(
            registration_browser, "refresh_active_page", return_value=None
        ), patch.object(
            registration_browser, "raise_if_cancelled", return_value=None, create=True
        ), patch.object(
            registration_browser,
            "_read_turnstile_state",
            return_value=_state(registration_browser.TURNSTILE_WAITING),
        ), patch.object(
            registration_browser, "getTurnstileToken", waiter
        ):
            token = registration_browser.wait_for_sso_cookie(timeout=2)

        self.assertEqual(token, "sso-token")
        waiter.assert_called_once()

    def test_script_only_is_not_treated_as_an_active_challenge(self):
        source = Path(registration_browser.__file__).read_text(encoding="utf-8")
        reader = source[
            source.index("def _read_turnstile_state("):
            source.index("def _wait_for_turnstile(")
        ]
        self.assertIn("else if (widget) state = 'LOADING';", reader)
        self.assertNotIn("else if (scriptPresent) state = 'LOADING';", reader)

    def test_registration_path_contains_no_legacy_turnstile_interference(self):
        source = Path(registration_browser.__file__).read_text(encoding="utf-8")
        profile = source[
            source.index("def fill_profile_and_submit("):
            source.index("def wait_for_sso_cookie(")
        ]
        sso = source[source.index("def wait_for_sso_cookie("):]

        self.assertNotIn("turnstile.reset", source)
        self.assertNotIn("MouseEvent.prototype", source)
        self.assertNotIn("二次复用 Turnstile", source)
        self.assertNotIn("token.length >= 80", source)
        self.assertNotIn("nativeSetter.call(cfInput", source)

        self.assertIn("_read_turnstile_state()", profile)
        self.assertIn("getTurnstileToken(", profile)
        self.assertIn("_read_turnstile_state()", sso)
        self.assertIn("getTurnstileToken(", sso)

        self.assertNotIn("cf-turnstile-response", profile)
        self.assertNotIn("cf-turnstile-response", sso)
        self.assertNotIn("wait-cloudflare", profile)
        self.assertNotIn("final-page-wait-cf", sso)
        self.assertNotIn('script[src*="turnstile"]', profile)
        self.assertNotIn('script[src*="turnstile"]', sso)


if __name__ == "__main__":
    unittest.main()


class TurnstileCaptchaFallbackTests(unittest.TestCase):
    """打码回退：自动等待无果后才上打码，且失败不得影响主流程。

    背景：注册流程原先只会被动等待，Cloudflare 静默挂起（不发放 token、
    也不报错）时只能干等到超时，实测约 10% 的账号因此失败。
    """

    def _run(self, states, solver_result, *, auto_wait=0, timeout=6, extra_patches=()):
        """跑一次 _wait_for_turnstile，返回 (异常, 打码调用次数)。"""
        seq = list(states)

        def _reader():
            return seq.pop(0) if len(seq) > 1 else seq[0]

        calls = []

        def _solver(log_callback=None):
            calls.append(1)
            return solver_result

        patches = [
            patch.object(registration_browser, "_read_turnstile_state", side_effect=_reader),
            patch.object(registration_browser, "raise_if_cancelled", return_value=None, create=True),
            patch.object(registration_browser, "sleep_with_cancel", return_value=None, create=True),
            patch.object(registration_browser, "page", object()),
            patch.object(registration_browser, "_try_solve_turnstile_via_solver", side_effect=_solver),
            patch("captcha_solver.auto_wait_sec", return_value=auto_wait),
        ]
        patches.extend(extra_patches)
        started = [0.0]

        def _clock():
            started[0] += 1.0
            return started[0]

        patches.append(patch.object(registration_browser.time, "time", side_effect=_clock))
        exc = None
        for p in patches:
            p.start()
        try:
            registration_browser._wait_for_turnstile(timeout=timeout)
        except Exception as err:  # noqa: BLE001 - 测试就是要区分是否抛异常
            exc = err
        finally:
            for p in reversed(patches):
                p.stop()
        return exc, len(calls)

    def test_auto_completion_never_calls_solver(self):
        """Cloudflare 自己 5 秒就过时，不该浪费一次打码。"""
        exc, calls = self._run(
            [_state(registration_browser.TURNSTILE_WAITING),
             _state(registration_browser.TURNSTILE_SOLVED, "tok")],
            solver_result=True, auto_wait=10, timeout=60)
        self.assertIsNone(exc)
        self.assertEqual(calls, 0)

    def test_solver_called_after_auto_wait_elapses(self):
        """等待超过阈值仍无 token 时必须交给打码。"""
        exc, calls = self._run(
            [_state(registration_browser.TURNSTILE_WAITING),
             _state(registration_browser.TURNSTILE_SOLVED, "tok")],
            solver_result=True, auto_wait=2, timeout=60)
        self.assertIsNone(exc)
        self.assertEqual(calls, 1)

    def test_solver_attempted_only_once(self):
        """反复打码既烧点数又拖时间，失败后应退回被动等待。"""
        exc, calls = self._run(
            [_state(registration_browser.TURNSTILE_WAITING)],
            solver_result=False, auto_wait=1, timeout=8)
        self.assertIsNotNone(exc)
        self.assertIn("超时", str(exc))
        self.assertEqual(calls, 1)

    def test_solver_failure_does_not_break_flow(self):
        """打码失败要能优雅退回等待，而不是把异常抛穿主流程。"""
        exc, calls = self._run(
            [_state(registration_browser.TURNSTILE_WAITING)],
            solver_result=False, auto_wait=1, timeout=5)
        self.assertEqual(calls, 1)
        # 最终仍以「超时」收场（与打码无关），而不是打码异常
        self.assertIsNotNone(exc)
        self.assertIn("Turnstile", str(exc))

    def test_solver_success_still_waits_for_token_in_page(self):
        """注入成功不代表页面状态已更新，必须重新读状态拿到 token。"""
        exc, calls = self._run(
            [_state(registration_browser.TURNSTILE_WAITING),
             _state(registration_browser.TURNSTILE_SOLVED, "injected-token")],
            solver_result=True, auto_wait=1, timeout=60)
        self.assertIsNone(exc)
        self.assertEqual(calls, 1)

    def test_zero_auto_wait_fires_solver_immediately(self):
        """阈值配 0 时第一轮就该打码（给「Cloudflare 必挂」的场景用）。"""
        exc, calls = self._run(
            [_state(registration_browser.TURNSTILE_WAITING),
             _state(registration_browser.TURNSTILE_SOLVED, "tok")],
            solver_result=True, auto_wait=0, timeout=60)
        self.assertIsNone(exc)
        self.assertEqual(calls, 1)
