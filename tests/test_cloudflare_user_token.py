"""验证 Cloudflare 临时邮箱 x-user-token（普通用户）模式。

背景：cloudflare_temp_email 实例开启 disableAnonymousUserCreateEmail 后，
/api/new_address 拒绝匿名调用，只认登录用户的 x-user-token。
本模式用普通账号自助注册+登录拿 JWT，无需 admin 密码。
"""

import unittest
from unittest.mock import patch

import mail_service


class DummyResponse:
    def __init__(self, payload, text=""):
        self._payload = payload
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class CloudflareUserTokenTests(unittest.TestCase):
    def setUp(self):
        self.original_config = mail_service.config
        self.original_token = mail_service._cf_user_token
        self.original_expiry = mail_service._cf_user_token_expiry
        mail_service.config = dict(mail_service.config or {})
        mail_service._cf_user_token = ""
        mail_service._cf_user_token_expiry = 0.0
        # http_post / http_get 由 bind_runtime 注入，测试环境需先占位。
        mail_service.bind_runtime({"http_post": None, "http_get": None})

    def tearDown(self):
        mail_service.config = self.original_config
        mail_service._cf_user_token = self.original_token
        mail_service._cf_user_token_expiry = self.original_expiry
        mail_service.bind_runtime({"http_post": None, "http_get": None})

    def _configure(self, **overrides):
        config = {
            "cloudflare_api_base": "https://mail.example.com",
            "cloudflare_api_key": "user@example.com:secret",
            "cloudflare_auth_mode": "x-user-token",
        }
        config.update(overrides)
        mail_service.config.update(config)

    def test_build_headers_uses_x_user_token(self):
        self._configure()
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if url.endswith("/user_api/register"):
                return DummyResponse({"success": True})
            return DummyResponse({"jwt": "user-jwt"})

        with patch.object(mail_service, "http_post", side_effect=fake_post):
            headers = mail_service.cloudflare_build_headers(content_type=True)

        self.assertEqual(headers["x-user-token"], "user-jwt")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn("x-admin-auth", headers)
        self.assertNotIn("Authorization", headers)
        self.assertIn("https://mail.example.com/user_api/register", calls)
        self.assertIn("https://mail.example.com/user_api/login", calls)

    def test_login_result_is_cached(self):
        self._configure()
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if url.endswith("/user_api/register"):
                return DummyResponse({"success": True})
            return DummyResponse({"jwt": "cached-jwt"})

        with patch.object(mail_service, "http_post", side_effect=fake_post):
            first = mail_service.cloudflare_user_token()
            second = mail_service.cloudflare_user_token()

        self.assertEqual(first, "cached-jwt")
        self.assertEqual(second, "cached-jwt")
        # 第二次应命中缓存，不再发起登录请求。
        self.assertEqual(len([u for u in calls if u.endswith("/user_api/login")]), 1)

    def test_register_failure_is_tolerated_for_existing_user(self):
        self._configure()
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if url.endswith("/user_api/register"):
                raise Exception("user already exists")
            return DummyResponse({"jwt": "existing-jwt"})

        with patch.object(mail_service, "http_post", side_effect=fake_post):
            token = mail_service.cloudflare_user_token()

        self.assertEqual(token, "existing-jwt")

    def test_missing_credentials_raises(self):
        self._configure(cloudflare_api_key="")
        with self.assertRaises(Exception) as ctx:
            mail_service.cloudflare_user_token()
        self.assertIn("cloudflare_api_key", str(ctx.exception))

    def test_malformed_credentials_raises(self):
        self._configure(cloudflare_api_key="no-colon-here")
        with self.assertRaises(Exception) as ctx:
            mail_service.cloudflare_user_token()
        self.assertIn("邮箱:密码", str(ctx.exception))

    def test_login_without_jwt_raises(self):
        self._configure()

        def fake_post(url, **kwargs):
            if url.endswith("/user_api/register"):
                return DummyResponse({"success": True})
            return DummyResponse({"error": "bad password"})

        with patch.object(mail_service, "http_post", side_effect=fake_post):
            with self.assertRaises(Exception) as ctx:
                mail_service.cloudflare_user_token()
        self.assertIn("未返回 jwt", str(ctx.exception))

    def test_domains_fall_back_to_public_settings(self):
        self._configure()
        requested = []

        def fake_get(url, **kwargs):
            requested.append(url)
            if url.endswith("/api/domains"):
                raise Exception("Invalid address credential")
            return DummyResponse({
                "defaultDomains": ["jgxjs.com"],
                "domains": ["jgxjs.com", "alt.example.com"],
            })

        def fake_post(url, **kwargs):
            if url.endswith("/user_api/register"):
                return DummyResponse({"success": True})
            return DummyResponse({"jwt": "user-jwt"})

        with patch.object(mail_service, "http_get", side_effect=fake_get), \
                patch.object(mail_service, "http_post", side_effect=fake_post):
            domains = mail_service.cloudflare_get_domains("https://mail.example.com")

        names = [d.get("domain") for d in domains]
        self.assertEqual(names, ["jgxjs.com", "alt.example.com"])
        self.assertTrue(all(d.get("isVerified") for d in domains))
        self.assertIn("https://mail.example.com/open_api/settings", requested)

    def test_non_user_token_modes_are_unchanged(self):
        self._configure(cloudflare_auth_mode="x-admin-auth", cloudflare_api_key="admin-secret")
        headers = mail_service.cloudflare_build_headers(content_type=True)
        self.assertEqual(headers["x-admin-auth"], "admin-secret")
        self.assertNotIn("x-user-token", headers)


class CloudflareFixedAddressTests(unittest.TestCase):
    """固定邮箱模式：实例关闭建址时复用既有地址与其地址级 JWT。"""

    def setUp(self):
        self.original_config = mail_service.config
        mail_service.config = dict(mail_service.config or {})
        mail_service.bind_runtime({"http_post": None, "http_get": None})

    def tearDown(self):
        mail_service.config = self.original_config
        mail_service.bind_runtime({"http_post": None, "http_get": None})

    def _configure(self, **overrides):
        config = {
            "email_provider": "cloudflare",
            "cloudflare_api_base": "https://mail.example.com",
            "cloudflare_auth_mode": "none",
            "cloudflare_api_key": "",
            "cloudflare_fixed_address": "",
            "cloudflare_fixed_jwt": "",
        }
        config.update(overrides)
        mail_service.config.update(config)

    def test_fixed_address_skips_creation(self):
        self._configure(
            cloudflare_fixed_address="fixed@example.com",
            cloudflare_fixed_jwt="address-jwt",
        )
        called = []

        def fake_post(url, **kwargs):
            called.append(url)
            return DummyResponse({"address": "new@example.com", "jwt": "new-jwt"})

        with patch.object(mail_service, "http_post", side_effect=fake_post):
            address, jwt = mail_service.get_email_and_token()

        self.assertEqual(address, "fixed@example.com")
        self.assertEqual(jwt, "address-jwt")
        # 固定模式下不应触达建址接口。
        self.assertEqual(called, [])

    def test_empty_fixed_address_keeps_auto_create(self):
        self._configure()
        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            return DummyResponse({"address": "auto@example.com", "jwt": "auto-jwt"})

        with patch.object(mail_service, "http_post", side_effect=fake_post):
            address, jwt = mail_service.get_email_and_token()

        self.assertEqual(address, "auto@example.com")
        self.assertEqual(jwt, "auto-jwt")
        self.assertTrue(captured["url"].endswith("/api/new_address"))

    def test_fixed_address_getters_strip_whitespace(self):
        self._configure(
            cloudflare_fixed_address="  spaced@example.com  ",
            cloudflare_fixed_jwt="  spaced-jwt  ",
        )
        self.assertEqual(mail_service.get_cloudflare_fixed_address(), "spaced@example.com")
        self.assertEqual(mail_service.get_cloudflare_fixed_jwt(), "spaced-jwt")


if __name__ == "__main__":
    unittest.main()
