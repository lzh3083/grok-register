"""美国环境一致性：时区按代理真实出口地区自动对齐。

背景：住宅代理的实际落地州无法预先指定（实测 MooProxy 的 state 参数
不生效，请求 New York 会分到 Nevada/Texas）。若沿用固定时区，就会出现
「IP 在加州、时区却是纽约」这类自相矛盾特征 —— 正是本模块要消除的东西。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import us_consistency  # noqa: E402


class TimezoneForRegionTests(unittest.TestCase):
    def test_full_state_names_map_to_timezones(self):
        cases = {
            "New York": "America/New_York",
            "California": "America/Los_Angeles",
            "Texas": "America/Chicago",
            "Nevada": "America/Los_Angeles",
            "Florida": "America/New_York",
            "Colorado": "America/Denver",
            "Hawaii": "Pacific/Honolulu",
            "Massachusetts": "America/New_York",
        }
        for region, expected in cases.items():
            with self.subTest(region=region):
                self.assertEqual(us_consistency.timezone_for_region(region), expected)

    def test_state_abbreviations_are_accepted(self):
        self.assertEqual(us_consistency.timezone_for_region("NY"), "America/New_York")
        self.assertEqual(us_consistency.timezone_for_region("ca"), "America/Los_Angeles")
        self.assertEqual(us_consistency.timezone_for_region("TX"), "America/Chicago")

    def test_name_matching_is_case_insensitive(self):
        self.assertEqual(us_consistency.timezone_for_region("new york"), "America/New_York")
        self.assertEqual(us_consistency.timezone_for_region("CALIFORNIA"), "America/Los_Angeles")

    def test_unknown_region_returns_empty(self):
        for value in ("", None, "Ontario", "Bavaria", "Unknown"):
            with self.subTest(value=value):
                self.assertEqual(us_consistency.timezone_for_region(value), "")


class AlignTimezoneTests(unittest.TestCase):
    def setUp(self):
        self._saved_tz = os.environ.get("TZ")
        self.config = {"us_consistency_enabled": True, "us_consistency_timezone": "America/New_York"}
        us_consistency.configure(self.config)

    def tearDown(self):
        if self._saved_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._saved_tz

    def test_apply_region_timezone_rewrites_config_and_env(self):
        zone = us_consistency.apply_region_timezone("California")
        self.assertEqual(zone, "America/Los_Angeles")
        self.assertEqual(self.config["us_consistency_timezone"], "America/Los_Angeles")
        self.assertEqual(os.environ.get("TZ"), "America/Los_Angeles")

    def test_apply_region_timezone_ignores_unknown_region(self):
        zone = us_consistency.apply_region_timezone("Bavaria")
        self.assertEqual(zone, "")
        # 未识别时必须保持原值，不能把时区清空或改坏。
        self.assertEqual(self.config["us_consistency_timezone"], "America/New_York")

    def test_align_uses_probe_result(self):
        original = us_consistency.probe_exit_region
        us_consistency.probe_exit_region = lambda *a, **k: {
            "ip": "1.2.3.4", "country": "US", "region": "Texas",
        }
        try:
            zone = us_consistency.align_timezone_with_proxy("http://127.0.0.1:1")
        finally:
            us_consistency.probe_exit_region = original
        self.assertEqual(zone, "America/Chicago")
        self.assertEqual(self.config["us_consistency_timezone"], "America/Chicago")

    def test_align_refuses_when_country_mismatches(self):
        """出口不是目标国家时不得对齐，否则会把时区改成错误地区。"""
        original = us_consistency.probe_exit_region
        us_consistency.probe_exit_region = lambda *a, **k: {
            "ip": "5.6.7.8", "country": "BR", "region": "São Paulo",
        }
        try:
            zone = us_consistency.align_timezone_with_proxy("http://127.0.0.1:1", expect_country="US")
        finally:
            us_consistency.probe_exit_region = original
        self.assertEqual(zone, "")
        self.assertEqual(self.config["us_consistency_timezone"], "America/New_York")

    def test_align_tolerates_probe_failure(self):
        original = us_consistency.probe_exit_region
        us_consistency.probe_exit_region = lambda *a, **k: {}
        try:
            zone = us_consistency.align_timezone_with_proxy("http://127.0.0.1:1")
        finally:
            us_consistency.probe_exit_region = original
        self.assertEqual(zone, "")
        self.assertEqual(self.config["us_consistency_timezone"], "America/New_York")


class ProbeExitRegionTests(unittest.TestCase):
    def test_probe_returns_empty_dict_on_failure(self):
        # 端口 1 上不会有代理，探测必须安静失败而不是抛异常。
        result = us_consistency.probe_exit_region("http://127.0.0.1:1", timeout=1)
        self.assertEqual(result, {})

    def test_probe_parses_successful_response(self):
        import json as _json
        import urllib.request

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return _json.dumps({
                    "status": "success", "query": "9.9.9.9", "countryCode": "US",
                    "regionName": "Ohio", "city": "Columbus", "isp": "Test ISP",
                    "hosting": False, "proxy": False,
                }).encode()

        class _Opener:
            def open(self, request, timeout=None):
                return _Response()

        original = urllib.request.build_opener
        urllib.request.build_opener = lambda *a, **k: _Opener()
        try:
            result = us_consistency.probe_exit_region("http://127.0.0.1:9999")
        finally:
            urllib.request.build_opener = original
        self.assertEqual(result["country"], "US")
        self.assertEqual(result["region"], "Ohio")
        self.assertFalse(result["hosting"])


if __name__ == "__main__":
    unittest.main()
