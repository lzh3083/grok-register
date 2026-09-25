"""jwt_inspect 的单元测试。

xAI 的 bfs 标记判定是「key 存在即视为标记」，所以值为 0 或空串也必须
算命中 —— 这点容易写错（用真值判断会漏掉 bfs=0）。
"""
import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt_inspect  # noqa: E402


def make_jwt(payload):
    """构造一个仅用于测试的 JWT（签名段是假的，我们不校验签名）。"""
    def seg(data):
        raw = json.dumps(data).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return "%s.%s.%s" % (seg({"alg": "RS256", "typ": "JWT"}), seg(payload), "fakesig")


class DecodeTests(unittest.TestCase):
    def test_decodes_payload(self):
        token = make_jwt({"sub": "abc", "email": "a@b.com"})
        payload = jwt_inspect.decode_jwt_payload(token)
        self.assertEqual(payload["sub"], "abc")
        self.assertEqual(payload["email"], "a@b.com")

    def test_handles_missing_padding(self):
        """base64url 去掉 padding 后仍要能解码。"""
        token = make_jwt({"sub": "x" * 3})  # 长度刻意不整齐
        self.assertEqual(jwt_inspect.decode_jwt_payload(token)["sub"], "xxx")

    def test_rejects_non_jwt(self):
        for bad in ["", "not-a-jwt", "only.two", None, 123, "a.b"]:
            self.assertEqual(jwt_inspect.decode_jwt_payload(bad), {})

    def test_rejects_invalid_base64(self):
        self.assertEqual(jwt_inspect.decode_jwt_payload("aaa.!!!invalid!!!.ccc"), {})

    def test_rejects_non_dict_payload(self):
        raw = base64.urlsafe_b64encode(b'"just-a-string"').decode().rstrip("=")
        self.assertEqual(jwt_inspect.decode_jwt_payload("aaa.%s.ccc" % raw), {})


class BfsTests(unittest.TestCase):
    def test_detects_bfs(self):
        self.assertEqual(jwt_inspect.bfs_value({"bfs": 2}), 2)

    def test_bfs_zero_still_counts_as_flagged(self):
        """值为 0 也算标记 —— 判定依据是 key 存在，不是真值。"""
        self.assertEqual(jwt_inspect.bfs_value({"bfs": 0}), 0)
        info = jwt_inspect.inspect_token(make_jwt({"bfs": 0}))
        self.assertTrue(info["bfs"])

    def test_bfs_empty_string_still_counts(self):
        self.assertEqual(jwt_inspect.bfs_value({"bfs": ""}), "")
        self.assertTrue(jwt_inspect.inspect_token(make_jwt({"bfs": ""}))["bfs"])

    def test_bfs_case_insensitive(self):
        for key in ["BFS", "Bfs", "bFs"]:
            self.assertIsNotNone(jwt_inspect.bfs_value({key: 1}), key)

    def test_no_bfs_returns_none(self):
        self.assertIsNone(jwt_inspect.bfs_value({"sub": "abc"}))
        self.assertFalse(jwt_inspect.inspect_token(make_jwt({"sub": "abc"}))["bfs"])

    def test_handles_non_dict(self):
        self.assertIsNone(jwt_inspect.bfs_value(None))
        self.assertIsNone(jwt_inspect.bfs_value("bfs"))

    def test_inspect_reports_subject_and_email(self):
        token = make_jwt({"sub": "user-1", "email": "a@b.com", "bfs": 2})
        info = jwt_inspect.inspect_token(token)
        self.assertEqual(info["subject"], "user-1")
        self.assertEqual(info["email"], "a@b.com")
        self.assertEqual(info["bfs_value"], 2)

    def test_inspect_broken_token(self):
        info = jwt_inspect.inspect_token("garbage")
        self.assertFalse(info["bfs"])
        self.assertEqual(info["subject"], "")


if __name__ == "__main__":
    unittest.main()
