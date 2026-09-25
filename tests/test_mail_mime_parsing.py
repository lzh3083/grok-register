"""验证 MIME 原文解析：正文解码与邮件头隔离。

背景：cloudflare_temp_email 只返回整封 MIME 原文（raw 字段），不含
subject/text 字段，且正文常为 base64。若直接把 raw 当正文：
1. 正文取不到（拿到的是 base64 密文）；
2. 邮件头里的 Received/SPF 记录（如 mail-oo2-x2a.google.com）形态
   恰好符合 xAI 的 XXX-XXX 验证码格式，会被误提取成验证码。
"""

import base64
import unittest

import mail_service


def _mime(headers, body_b64):
    return headers + "\r\n\r\n" + body_b64


REAL_RAW = (
    "Received: from mail-oo2-x2a.google.com (2607:f8b0:4864:31::2a)\r\n"
    "\tby cloudflare-email.net (cloudflare) id cTrJaTSzecH2\r\n"
    "\tfor <cqcpw2jymc9@jgxjs.com>; Fri, 25 Sep 2026 12:31:51 +0000\r\n"
    "X-CF-SpamH-Score: 0\r\n"
    "Received: by mail-oo2-x2a.google.com with SMTP id 46e09a7af769-80\r\n"
    "Subject: =?UTF-8?B?5rWL6K+V?=\r\n"
    "To: cqcpw2jymc9@jgxjs.com\r\n"
    "Content-Type: multipart/alternative; boundary=\"00000000000094f7ea065c4de63c\"\r\n"
    "\r\n"
    "--00000000000094f7ea065c4de63c\r\n"
    "Content-Type: text/plain; charset=\"UTF-8\"\r\n"
    "Content-Transfer-Encoding: base64\r\n"
    "\r\n"
    "5rWL6K+VMTIzNA0K\r\n"
    "--00000000000094f7ea065c4de63c\r\n"
    "Content-Type: text/html; charset=\"UTF-8\"\r\n"
    "Content-Transfer-Encoding: base64\r\n"
    "\r\n"
    "PGRpdiBkaXI9Imx0ciI+5rWL6K+VMTIzNDwvZGl2Pg0K\r\n"
    "--00000000000094f7ea065c4de63c--\r\n"
)


class RawMimeParsingTests(unittest.TestCase):
    def test_body_decodes_base64_and_drops_headers(self):
        body = mail_service.extract_mail_body(REAL_RAW)
        self.assertIn("测试1234", body)
        # 邮件头噪声必须被隔离，否则会污染验证码提取。
        self.assertNotIn("mail-oo2-x2a", body)
        self.assertNotIn("Received:", body)
        self.assertNotIn("5rWL6K+V", body)

    def test_subject_is_decoded_from_raw(self):
        self.assertEqual(mail_service.extract_mail_subject(REAL_RAW), "测试")

    def test_mail_subject_prefers_explicit_field(self):
        self.assertEqual(
            mail_service.mail_subject({"subject": "plain", "raw": REAL_RAW}),
            "plain",
        )
        self.assertEqual(mail_service.mail_subject({"raw": REAL_RAW}), "测试")
        self.assertEqual(mail_service.mail_subject({}), "")

    def test_no_false_positive_from_header_noise(self):
        """回归：修复前会从 mail-oo2-x2a.google.com 误提取 oo2-x2a。"""
        body = mail_service.normalize_mail_body({"raw": REAL_RAW})
        code = mail_service.extract_verification_code(body, mail_service.mail_subject({"raw": REAL_RAW}))
        self.assertIsNone(code)
        self.assertNotIn("oo2-x2a", body)

    def test_xai_code_is_extracted_from_real_body(self):
        """真实 xAI 邮件（明文 base64 正文）应能提取到 XXX-XXX 验证码。"""
        plain = "Your xAI verification code is: ABC-123\r\n"
        raw = _mime(
            "Received: from mail-oo2-x2a.google.com\r\n"
            "Subject: ABC-123 xAI confirmation code\r\n"
            "Content-Type: text/plain; charset=\"UTF-8\"\r\n"
            "Content-Transfer-Encoding: base64",
            base64.b64encode(plain.encode()).decode(),
        )
        body = mail_service.normalize_mail_body({"raw": raw})
        subject = mail_service.mail_subject({"raw": raw})
        self.assertEqual(mail_service.extract_verification_code(body, subject), "ABC-123")

    def test_quoted_printable_body(self):
        raw = (
            "Subject: t\r\n"
            "Content-Type: text/plain; charset=\"UTF-8\"\r\n"
            "Content-Transfer-Encoding: quoted-printable\r\n"
            "\r\n"
            "code is ABC-123=0Aand more\r\n"
        )
        body = mail_service.extract_mail_body(raw)
        self.assertIn("ABC-123", body)
        self.assertEqual(mail_service.extract_verification_code(body), "ABC-123")

    def test_plain_body_without_multipart(self):
        raw = "Subject: t\r\nContent-Type: text/plain\r\n\r\nplain text here\r\n"
        self.assertIn("plain text here", mail_service.extract_mail_body(raw))

    def test_malformed_input_is_tolerated(self):
        self.assertEqual(mail_service.extract_mail_body(""), "")
        self.assertEqual(mail_service.extract_mail_body(None), "")
        self.assertEqual(mail_service.extract_mail_body("no blank line"), "")
        self.assertEqual(mail_service.extract_mail_subject(None), "")
        # 非 base64 内容不应抛异常。
        self.assertIn("not base64!!", mail_service.extract_mail_body(
            "Content-Type: text/plain\r\nContent-Transfer-Encoding: base64\r\n\r\nnot base64!!"
        ))

    def test_normalize_keeps_other_provider_fields(self):
        body = mail_service.normalize_mail_body({"text": "hello", "html": "<b>world</b>"})
        self.assertIn("hello", body)
        self.assertIn("world", body)


if __name__ == "__main__":
    unittest.main()
