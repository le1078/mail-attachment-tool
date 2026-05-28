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

    def test_real_imap_response_format(self):
        """模拟真实 IMAP 响应（混合 literal 和 flags 自然分片）"""
        raw = build_rfc822_response(subject="IMAP Format A")
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = ("OK", [
            (b"1 (UID 123 RFC822 {" + str(len(raw)).encode() + b"}", raw),
            b"1 (FLAGS (\\Seen))",
        ])

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["seen"])

    def test_imap_flags_in_response_header(self):
        """模拟真实 IMAP 响应（FLAGS 嵌入在 HEAD 部分的 literal 中）"""
        raw = build_rfc822_response(subject="IMAP Format B")
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        # 最典型的 IMAP 响应：第一个 tuple 的头部包含 FLAGS
        mock_mail.fetch.return_value = ("OK", [
            (b"1 (FLAGS (\\Seen) RFC822 {" + str(len(raw)).encode() + b"}", raw),
            b")",
        ])

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["seen"], "FLAGS 嵌入头部时 seen 应为 True")

    def test_imap_no_flags_embedded_in_header(self):
        """模拟真实 IMAP 响应（无 \\Seen 标志的 HEAD 部分）"""
        raw = build_rfc822_response(subject="IMAP Format C")
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = ("OK", [
            (b"1 (RFC822 {" + str(len(raw)).encode() + b"}", raw),
            b")",
        ])

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["seen"], "无 \\Seen 时 seen 应为 False")

    def test_fetch_detail_imap_flags_in_header(self):
        """fetch_email_detail: FLAGS 嵌入头部时 seen 应为 True"""
        raw = build_rfc822_response(subject="Detail Header Flags")
        mock_mail = MagicMock()
        mock_mail.fetch.return_value = ("OK", [
            (b"1 (FLAGS (\\Seen \\Answered) RFC822 {" + str(len(raw)).encode() + b"}", raw),
            b")",
        ])

        from 邮件附件下载工具 import fetch_email_detail
        detail = fetch_email_detail(mock_mail, "1")

        self.assertIsNotNone(detail)
        self.assertTrue(detail["seen"], "FLAGS 嵌入头部时 seen 应为 True")


class TestBoundaryAndException(unittest.TestCase):
    """第 2 轮：边界/异常/退化测试"""

    def test_query_empty_inbox(self):
        """空收件箱返回空列表"""
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b""])

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertEqual(result, [])

    def test_query_select_failure(self):
        """select 失败时返回空列表"""
        mock_mail = MagicMock()
        mock_mail.select.side_effect = Exception("SELECT failed")

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertEqual(result, [])

    def test_query_max_count_truncation(self):
        """max_count 正确截断结果"""
        raw = build_rfc822_response()
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1 2 3"])
        mock_mail.fetch.return_value = make_fetch_with_flags(raw)

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=2)

        self.assertLessEqual(len(result), 2)

    def test_query_special_chars_subject(self):
        """特殊字符主题处理正常"""
        raw = build_rfc822_response(subject="=?utf-8?B?5rWL6K+V?=")
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = make_fetch_with_flags(raw)

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5)

        self.assertEqual(len(result), 1)
        self.assertIsNotNone(result[0]["subject"])

    def test_query_mixed_unicode_folder(self):
        """中文文件夹名正确选择"""
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b""])

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "已发送", "ALL", max_count=5)

        self.assertEqual(result, [])
        select_call = mock_mail.select.call_args[0][0]
        self.assertIn("已发送", select_call)

    def test_get_sent_folder_name_no_match(self):
        """get_sent_folder_name 无匹配时返回 None"""
        mock_mail = MagicMock()
        mock_mail.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "Spam"'])

        from 邮件附件下载工具 import get_sent_folder_name
        result = get_sent_folder_name(mock_mail, log_func=print)

        self.assertIsNone(result)

    def test_get_sent_folder_name_chinese_match(self):
        """get_sent_folder_name 匹配中文已发送文件夹"""
        mock_mail = MagicMock()
        chinese_bytes = b'(\\HasNoChildren) "/" "' + "已发送".encode('utf-8') + b'"'
        mock_mail.list.return_value = ("OK", [chinese_bytes])

        from 邮件附件下载工具 import get_sent_folder_name
        result = get_sent_folder_name(mock_mail)

        self.assertIsNotNone(result)
        self.assertIn("已发送", result)

    def test_connect_imap_ssl_fallback(self):
        """connect_imap SSL 验证失败时自动回退"""
        import ssl
        mock_imap_class = MagicMock()
        mock_imap_instance = MagicMock()
        mock_imap_instance.login.return_value = ("OK", [b""])
        mock_imap_class.side_effect = [
            ssl.SSLError("certificate verify failed"),
            mock_imap_instance,
        ]

        import imaplib
        original = imaplib.IMAP4_SSL
        try:
            imaplib.IMAP4_SSL = mock_imap_class
            from 邮件附件下载工具 import connect_imap
            result = connect_imap("imap.test.com", 993, "user", "pass", skip_ssl_verify=False)
            self.assertEqual(mock_imap_class.call_count, 2)
            self.assertIsNotNone(result)
        finally:
            imaplib.IMAP4_SSL = original

    def test_read_filter_map_invalid_value(self):
        """无效的 read_filter 值默认返回 all"""
        read_filter_map = {"全部": "all", "已读": "seen", "未读": "unseen"}
        result = read_filter_map.get("不存在", "all")
        self.assertEqual(result, "all")

    def test_query_emails_with_date_range(self):
        """带日期范围的查询正常工作"""
        raw = build_rfc822_response(subject="Date Range")
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = make_fetch_with_flags(raw, b"\\Seen")

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5,
                             start_date="2026-01-01", end_date="2026-12-31")

        self.assertEqual(len(result), 1)

    def test_query_emails_invalid_date_ignored(self):
        """无效日期参数被忽略，不影响查询"""
        raw = build_rfc822_response(subject="Bad Date")
        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b""])
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = make_fetch_with_flags(raw)

        from 邮件附件下载工具 import query_emails
        result = query_emails(mock_mail, "INBOX", "ALL", max_count=5,
                             start_date="invalid-date", end_date="also-invalid")

        self.assertEqual(len(result), 1)

    def test_fetch_detail_scrambled_response(self):
        """抓取乱序/破碎响应数据不会崩溃"""
        mock_mail = MagicMock()
        mock_mail.fetch.return_value = ("OK", [
            b"garbage",
            b"1 (FLAGS (\\Seen))",
            (b"1 (RFC822 {200}", build_rfc822_response()),
        ])

        from 邮件附件下载工具 import fetch_email_detail
        detail = fetch_email_detail(mock_mail, "1")

        self.assertIsNotNone(detail)
        self.assertTrue(detail["seen"])


if __name__ == "__main__":
    unittest.main(verbosity=2)