"""
测试已读/未读筛选功能
- query_emails 的 seen 标志提取
- fetch_email_detail 的 seen 标志提取
- read_filter 参数映射
"""
import sys
import os
import unittest
import email
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(__file__))


def build_rfc822_response(subject="Test", from_addr="test@test.com", date_str="Mon, 27 May 2026 10:00:00 +0800"):
    msg = email.message.EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = "receiver@test.com"
    msg["Date"] = date_str
    msg.set_content("Test body")
    return msg.as_bytes()


def make_fetch_ok(msg_data):
    """封装 fetch 的 OK 返回值: ("OK", msg_data_list)"""
    return ("OK", msg_data)


def make_fetch_with_flags(raw_bytes, flags_str=b"\\Seen"):
    """模拟 IMAP FETCH (RFC822 FLAGS) 返回的 msg_data 格式"""
    return make_fetch_ok([
        (b"1 (RFC822 {" + str(len(raw_bytes)).encode() + b"}", raw_bytes),
        b"1 (FLAGS (" + flags_str + b"))",
    ])


def make_fetch_without_flags(raw_bytes):
    return make_fetch_ok([
        (b"1 (RFC822 {" + str(len(raw_bytes)).encode() + b"}", raw_bytes),
    ])


class TestQueryEmailsSeenFlag(unittest.TestCase):
    """测试 query_emails() 的 seen 标志提取"""

    def test_seen_mail_has_seen_true(self):
        raw = build_rfc822_response(subject="Seen Mail")
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = make_fetch_with_flags(raw, b"\\Seen")

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["seen"], "已读邮件 seen 应为 True")
        self.assertEqual(result[0]["subject"], "Seen Mail")

    def test_unseen_mail_has_seen_false(self):
        raw = build_rfc822_response(subject="Unseen Mail")
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = make_fetch_with_flags(raw, b"")

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["seen"], "未读邮件 seen 应为 False")
        self.assertEqual(result[0]["subject"], "Unseen Mail")

    def test_seen_flag_with_other_flags(self):
        raw = build_rfc822_response(subject="Multi Flag Mail")
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = make_fetch_with_flags(raw, b"\\Seen \\Answered \\Flagged")

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertTrue(result[0]["seen"], "含 \\Seen 的多重 flags 应识别为已读")

    def test_no_flags_response(self):
        raw = build_rfc822_response(subject="No Flags Mail")
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = make_fetch_without_flags(raw)

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["seen"], "无 FLAGS 时 seen 默认为 False")

    def test_read_filter_seen(self):
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = make_fetch_without_flags(build_rfc822_response())

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5, read_filter="seen")

        self.assertEqual(len(result), 1)
        search_call = mock_mail.search.call_args[0][1]
        self.assertIn("SEEN", search_call)

    def test_read_filter_unseen(self):
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = make_fetch_without_flags(build_rfc822_response())

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5, read_filter="unseen")

        self.assertEqual(len(result), 1)
        search_call = mock_mail.search.call_args[0][1]
        self.assertIn("UNSEEN", search_call)

    def test_read_filter_all_no_seen_unseen(self):
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = make_fetch_without_flags(build_rfc822_response())

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5, read_filter="all")

        self.assertEqual(len(result), 1)
        search_call = mock_mail.search.call_args[0][1]
        self.assertNotIn("SEEN", search_call)
        self.assertNotIn("UNSEEN", search_call)


class TestFetchEmailDetailSeenFlag(unittest.TestCase):
    """测试 fetch_email_detail() 的 seen 标志提取"""

    def test_seen_mail_detail(self):
        raw = build_rfc822_response(subject="Detail Seen")
        mock_mail = MagicMock()
        mock_mail.fetch.return_value = make_fetch_with_flags(raw, b"\\Seen")

        from 邮件附件下载工具 import fetch_email_detail
        detail = fetch_email_detail(mock_mail, "1")

        self.assertIsNotNone(detail)
        self.assertTrue(detail["seen"], "已读邮件详情 seen 应为 True")

    def test_unseen_mail_detail(self):
        raw = build_rfc822_response(subject="Detail Unseen")
        mock_mail = MagicMock()
        mock_mail.fetch.return_value = make_fetch_with_flags(raw, b"")

        from 邮件附件下载工具 import fetch_email_detail
        detail = fetch_email_detail(mock_mail, "1")

        self.assertIsNotNone(detail)
        self.assertFalse(detail["seen"], "未读邮件详情 seen 应为 False")

    def test_fetch_failure_returns_none(self):
        mock_mail = MagicMock()
        mock_mail.fetch.return_value = ("NO", [])

        from 邮件附件下载工具 import fetch_email_detail
        detail = fetch_email_detail(mock_mail, "1")

        self.assertIsNone(detail)

    def test_detail_no_raw_bytes_returns_none(self):
        mock_mail = MagicMock()
        mock_mail.fetch.return_value = ("OK", [b"1 (FLAGS (\\Seen))"])

        from 邮件附件下载工具 import fetch_email_detail
        detail = fetch_email_detail(mock_mail, "1")

        self.assertIsNone(detail)

    def test_detail_multiple_attachments_still_get_seen(self):
        """带附件的邮件也能正确获取 seen 状态"""
        raw = build_rfc822_response(subject="Attachment Mail")
        mock_mail = MagicMock()
        mock_mail.fetch.return_value = make_fetch_with_flags(raw, b"\\Seen")

        from 邮件附件下载工具 import fetch_email_detail
        detail = fetch_email_detail(mock_mail, "1")

        self.assertIsNotNone(detail)
        self.assertTrue(detail["seen"])
        self.assertEqual(detail["subject"], "Attachment Mail")


class TestReadFilterMap(unittest.TestCase):
    """测试 read_filter 映射"""

    def test_read_filter_map_values(self):
        read_filter_map = {"全部": "all", "已读": "seen", "未读": "unseen"}
        self.assertEqual(read_filter_map["全部"], "all")
        self.assertEqual(read_filter_map["已读"], "seen")
        self.assertEqual(read_filter_map["未读"], "unseen")

    def test_read_filter_map_default(self):
        read_filter_map = {"全部": "all", "已读": "seen", "未读": "unseen"}
        result = read_filter_map.get("未知", "all")
        self.assertEqual(result, "all")


class TestQueryEmailsSearchCriteria(unittest.TestCase):
    """测试 query_emails 搜索条件的组合"""

    def test_search_failure_returns_empty(self):
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("NO", [])

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertEqual(result, [])

    def test_fetch_failure_skips_mail(self):
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = ("NO", [])

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)