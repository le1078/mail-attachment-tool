"""imap_operations 模块单元测试。

覆盖 download_attachments 函数。
"""
import os
import sys
from unittest.mock import patch

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from imap_operations import download_attachments


# ==================== download_attachments 测试 ====================

class TestDownloadAttachments:
    """download_attachments 函数测试。"""

    def test_downloads_and_marks_read(self, mock_mail, mock_log):
        """验证下载成功后标记已读——正常流程。"""
        config = {
            "sender_filter_list": ["test@example.com"],
            "save_folder": "/tmp/save",
            "download_keyword_filter": "",
            "download_read_status": "all",
            "download_filter_days": [],
            "download_filter_time_enabled": False,
            "download_filter_time_start": "00:00",
            "download_filter_time_end": "23:59",
            "download_filter_date_enabled": False,
            "download_filter_date_start": "",
            "download_filter_date_end": "",
        }

        def _fake_fetch(mail, sender_filter_list, save_folder, log_func,
                        keyword_filter="", read_status="all",
                        filter_days=None, filter_time_enabled=False,
                        filter_time_start="00:00", filter_time_end="23:59",
                        filter_date_enabled=False, filter_date_start="",
                        filter_date_end="", record_func=None):
            # 模拟 record_func 被调用，记录 email_uid
            if record_func:
                record_func({
                    "filename": "file1.pdf",
                    "subject": "Test",
                    "sender": "test@example.com",
                    "save_path": "/tmp/save/file1.pdf",
                    "size": 1024,
                    "status": "success",
                    "email_uid": "101",
                })
                record_func({
                    "filename": "file2.pdf",
                    "subject": "Test2",
                    "sender": "test@example.com",
                    "save_path": "/tmp/save/file2.pdf",
                    "size": 2048,
                    "status": "success",
                    "email_uid": "102",
                })
            return 2

        with patch("imap_operations.fetch_attachments", side_effect=_fake_fetch):
            with patch("imap_operations.mark_email_as_read") as mock_mark:
                count = download_attachments(mock_mail, config, mock_log)

        assert count == 2
        assert mock_mark.call_count == 2
        mock_mark.assert_any_call(mock_mail, "INBOX", "101", mock_log)
        mock_mark.assert_any_call(mock_mail, "INBOX", "102", mock_log)

    def test_mark_read_failure_does_not_interrupt(self, mock_mail, mock_log):
        """验证标记已读失败不中断下载流程。"""
        config = {
            "sender_filter_list": [],
            "save_folder": "/tmp/save",
            "download_keyword_filter": "",
            "download_read_status": "all",
            "download_filter_days": [],
            "download_filter_time_enabled": False,
            "download_filter_time_start": "00:00",
            "download_filter_time_end": "23:59",
            "download_filter_date_enabled": False,
            "download_filter_date_start": "",
            "download_filter_date_end": "",
        }

        def _fake_fetch(mail, sender_filter_list, save_folder, log_func,
                        keyword_filter="", read_status="all",
                        filter_days=None, filter_time_enabled=False,
                        filter_time_start="00:00", filter_time_end="23:59",
                        filter_date_enabled=False, filter_date_start="",
                        filter_date_end="", record_func=None):
            if record_func:
                record_func({
                    "filename": "file.pdf",
                    "subject": "Test",
                    "sender": "test@example.com",
                    "save_path": "/tmp/save/file.pdf",
                    "size": 1024,
                    "status": "success",
                    "email_uid": "201",
                })
            return 1

        with patch("imap_operations.fetch_attachments", side_effect=_fake_fetch):
            with patch("imap_operations.mark_email_as_read",
                       side_effect=Exception("Network error")) as mock_mark:
                count = download_attachments(mock_mail, config, mock_log)

        assert count == 1
        mock_mark.assert_called_once()
        # 确认警告日志被记录
        assert any("[警告] 标记已读失败" in m for m in mock_log.messages)

    def test_no_downloads_no_mark(self, mock_mail, mock_log):
        """验证无附件下载时不触发标记已读。"""
        config = {
            "sender_filter_list": [],
            "save_folder": "/tmp/save",
            "download_keyword_filter": "",
            "download_read_status": "all",
            "download_filter_days": [],
            "download_filter_time_enabled": False,
            "download_filter_time_start": "00:00",
            "download_filter_time_end": "23:59",
            "download_filter_date_enabled": False,
            "download_filter_date_start": "",
            "download_filter_date_end": "",
        }

        def _fake_fetch(mail, sender_filter_list, save_folder, log_func,
                        keyword_filter="", read_status="all",
                        filter_days=None, filter_time_enabled=False,
                        filter_time_start="00:00", filter_time_end="23:59",
                        filter_date_enabled=False, filter_date_start="",
                        filter_date_end="", record_func=None):
            return 0

        with patch("imap_operations.fetch_attachments", side_effect=_fake_fetch):
            with patch("imap_operations.mark_email_as_read") as mock_mark:
                count = download_attachments(mock_mail, config, mock_log)

        assert count == 0
        mock_mark.assert_not_called()

    def test_record_func_passthrough(self, mock_mail, mock_log):
        """验证 record_func 正确透传——原始回调被调用。"""
        config = {
            "sender_filter_list": [],
            "save_folder": "/tmp/save",
            "download_keyword_filter": "",
            "download_read_status": "all",
            "download_filter_days": [],
            "download_filter_time_enabled": False,
            "download_filter_time_start": "00:00",
            "download_filter_time_end": "23:59",
            "download_filter_date_enabled": False,
            "download_filter_date_start": "",
            "download_filter_date_end": "",
        }
        recorded = []

        def _user_record_func(record_dict):
            recorded.append(record_dict)

        def _fake_fetch(mail, sender_filter_list, save_folder, log_func,
                        keyword_filter="", read_status="all",
                        filter_days=None, filter_time_enabled=False,
                        filter_time_start="00:00", filter_time_end="23:59",
                        filter_date_enabled=False, filter_date_start="",
                        filter_date_end="", record_func=None):
            if record_func:
                record_func({
                    "filename": "f.pdf",
                    "email_uid": "301",
                    "status": "success",
                })
            return 1

        with patch("imap_operations.fetch_attachments", side_effect=_fake_fetch):
            with patch("imap_operations.mark_email_as_read"):
                count = download_attachments(mock_mail, config, mock_log,
                                             record_func=_user_record_func)

        assert count == 1
        assert len(recorded) == 1
        assert recorded[0]["filename"] == "f.pdf"

    def test_filter_days_passed_correctly(self, mock_mail, mock_log):
        """验证 filter_days 为非空列表时正确传递。"""
        config = {
            "sender_filter_list": [],
            "save_folder": "/tmp/save",
            "download_keyword_filter": "",
            "download_read_status": "all",
            "download_filter_days": [1, 3, 5],
            "download_filter_time_enabled": False,
            "download_filter_time_start": "00:00",
            "download_filter_time_end": "23:59",
            "download_filter_date_enabled": False,
            "download_filter_date_start": "",
            "download_filter_date_end": "",
        }
        call_args = []

        def _fake_fetch(*args, **kwargs):
            call_args.append(kwargs)
            return 0

        with patch("imap_operations.fetch_attachments", side_effect=_fake_fetch):
            with patch("imap_operations.mark_email_as_read"):
                download_attachments(mock_mail, config, mock_log)

        assert call_args[0]["filter_days"] == [1, 3, 5]

    def test_empty_filter_days_passed_as_none(self, mock_mail, mock_log):
        """验证 filter_days 为空列表时传递 None。"""
        config = {
            "sender_filter_list": [],
            "save_folder": "/tmp/save",
            "download_keyword_filter": "",
            "download_read_status": "all",
            "download_filter_days": [],
            "download_filter_time_enabled": False,
            "download_filter_time_start": "00:00",
            "download_filter_time_end": "23:59",
            "download_filter_date_enabled": False,
            "download_filter_date_start": "",
            "download_filter_date_end": "",
        }
        call_args = []

        def _fake_fetch(*args, **kwargs):
            call_args.append(kwargs)
            return 0

        with patch("imap_operations.fetch_attachments", side_effect=_fake_fetch):
            with patch("imap_operations.mark_email_as_read"):
                download_attachments(mock_mail, config, mock_log)

        assert call_args[0]["filter_days"] is None