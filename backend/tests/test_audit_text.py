"""审计用文本脱敏（无 ORM 依赖）。"""
import unittest

from app.core.audit_text import summarize_text_for_audit


class TestSummarizeTextForAudit(unittest.TestCase):
    def test_truncate(self):
        long = "a" * 300
        out = summarize_text_for_audit(long, max_chars=50)
        self.assertLessEqual(len(out), 50)
        self.assertTrue(out.endswith("…"))

    def test_phone_email_id(self):
        s = "联系 13812345678 或 user@example.com 证件 110105199001011234"
        out = summarize_text_for_audit(s)
        self.assertNotIn("13812345678", out)
        self.assertIn("手机已脱敏", out)
        self.assertIn("邮箱已脱敏", out)
        self.assertIn("证件号已脱敏", out)


if __name__ == "__main__":
    unittest.main()
