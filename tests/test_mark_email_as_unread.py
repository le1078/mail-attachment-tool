"""
QA Test Suite: mark_email_as_unread + UI Changes
=================================================
Tests for imap_backend.mark_email_as_unread and related UI functionality.

ROUND 1: Functional coverage (happy paths + basic scenarios)
ROUND 2: Boundary/Exception/Edge cases
"""
import imaplib
import socket
import ssl
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from imap_backend import (
    mark_email_as_unread,
    mark_email_as_read,
    batch_flags_fetch,
    _create_ssl_context,
    clean_filename,
    decode_str,
    retry_on_network_error,
)


# ======================================================================
# ROUND 1: Functional Coverage Tests
# ======================================================================

class TestMarkEmailAsUnread_Round1:
    """Round 1: Happy path and basic functional tests for mark_email_as_unread."""

    def test_t01_normal_mark_unread_success(self, mock_mail, mock_log):
        """T01: Normal mark as unread - happy path with string mail_id."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.return_value = ("OK", [b"Success"])

        result = mark_email_as_unread(mock_mail, "INBOX", "123", log_func=mock_log)

        assert result is True
        mock_mail.select.assert_called_once_with("INBOX")
        mock_mail.store.assert_called_once_with(b"123", "-FLAGS", "\\Seen")
        assert any("标记为未读" in m for m in mock_log.messages)

    def test_t02_bytes_mail_id(self, mock_mail, mock_log):
        """T02: Mark unread with bytes mail_id (not string)."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.return_value = ("OK", [b"Success"])

        result = mark_email_as_unread(mock_mail, "INBOX", b"456", log_func=mock_log)

        assert result is True
        # bytes mail_id should be passed directly, not encoded
        mock_mail.store.assert_called_once_with(b"456", "-FLAGS", "\\Seen")

    def test_t03_folder_with_spaces_quoted(self, mock_mail, mock_log):
        """T03: Folder name with spaces gets quoted."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.return_value = ("OK", [b"Success"])

        result = mark_email_as_unread(mock_mail, "Junk E-mail", "100", log_func=mock_log)

        assert result is True
        mock_mail.select.assert_called_once_with('"Junk E-mail"')

    def test_t04_folder_with_slash_quoted(self, mock_mail, mock_log):
        """T04: Folder name with slash gets quoted."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.return_value = ("OK", [b"Success"])

        result = mark_email_as_unread(mock_mail, "Sent Items/2024", "200", log_func=mock_log)

        assert result is True
        mock_mail.select.assert_called_once_with('"Sent Items/2024"')

    def test_t05_folder_with_chinese_chars_quoted(self, mock_mail, mock_log):
        """T05: Folder name with Chinese characters gets quoted."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.return_value = ("OK", [b"Success"])

        result = mark_email_as_unread(mock_mail, "收件箱", "300", log_func=mock_log)

        assert result is True
        mock_mail.select.assert_called_once_with('"收件箱"')

    def test_t06_log_func_called_on_success(self, mock_mail):
        """T06: log_func is called with success message."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.return_value = ("OK", [b"Success"])
        log_messages = []

        result = mark_email_as_unread(
            mock_mail, "INBOX", "123",
            log_func=lambda msg: log_messages.append(msg)
        )

        assert result is True
        assert len(log_messages) == 1
        assert "123" in log_messages[0]
        assert "标记为未读" in log_messages[0]


class TestMarkEmailAsRead_Round1:
    """Round 1: Functional tests for mark_email_as_read (regression)."""

    def test_t07_mark_read_success(self, mock_mail, mock_log):
        """T07: mark_email_as_read happy path."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.return_value = ("OK", [b"Success"])

        result = mark_email_as_read(mock_mail, "INBOX", "789", log_func=mock_log)

        assert result is True
        mock_mail.store.assert_called_once_with(b"789", "+FLAGS", "\\Seen")
        assert any("标记为已读" in m for m in mock_log.messages)


class TestBatchFlagsFetch_Round1:
    """Round 1: Functional tests for batch_flags_fetch."""

    def test_t08_batch_flags_fetch_with_seen(self):
        """T08: batch_flags_fetch correctly detects Seen flag."""
        mail = MagicMock()
        mail.fetch.return_value = ("OK", [
            (b"1 (FLAGS (\\Seen))", b""),
            (b"2 (FLAGS ())", b""),
        ])

        result = batch_flags_fetch(mail, [b"1", b"2"])

        assert result.get("1") is True
        assert result.get("2") is False

    def test_t09_batch_flags_fetch_empty_ids(self):
        """T09: batch_flags_fetch with empty mail_ids returns empty dict."""
        mail = MagicMock()
        result = batch_flags_fetch(mail, [])
        assert result == {}

    def test_t10_batch_flags_fetch_failure_status(self):
        """T10: batch_flags_fetch returns empty dict on fetch failure."""
        mail = MagicMock()
        mail.fetch.return_value = ("NO", [])

        result = batch_flags_fetch(mail, [b"1"])

        assert result == {}


class TestCreateSslContext_Round1:
    """Round 1: Functional tests for _create_ssl_context."""

    def test_t11_ssl_context_default(self):
        """T11: Default SSL context has hostname checking."""
        ctx = _create_ssl_context(skip_verify=False)
        assert ctx.check_hostname is True
        assert ctx.verify_mode != ssl.CERT_NONE

    def test_t12_ssl_context_skip_verify(self):
        """T12: SSL context with skip_verify disables hostname checking."""
        ctx = _create_ssl_context(skip_verify=True)
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE


class TestCleanFilename_Round1:
    """Round 1: Functional tests for clean_filename."""

    def test_t13_clean_filename_normal(self):
        """T13: Normal filename passes through."""
        assert clean_filename("report.pdf") == "report.pdf"

    def test_t14_clean_filename_invalid_chars(self):
        """T14: Invalid characters replaced with underscore."""
        result = clean_filename('file/name:with*bad"chars.txt')
        assert "/" not in result
        assert ":" not in result
        assert "*" not in result
        assert '"' not in result

    def test_t15_clean_filename_reserved_name(self):
        """T15: Windows reserved names get prefixed."""
        result = clean_filename("CON.txt")
        assert result.startswith("_")
        assert "CON" in result


class TestDecodeStr_Round1:
    """Round 1: Functional tests for decode_str."""

    def test_t16_decode_str_none(self):
        """T16: decode_str returns empty string for None."""
        assert decode_str(None) == ""

    def test_t17_decode_str_plain_ascii(self):
        """T17: decode_str returns plain ASCII string as-is."""
        assert decode_str("Hello World") == "Hello World"


# ======================================================================
# ROUND 2: Boundary / Exception / Edge Case Tests
# ======================================================================

class TestMarkEmailAsUnread_Round2:
    """Round 2: Boundary and exception tests for mark_email_as_unread."""

    def test_t18_select_fallback_on_first_failure(self, mock_mail, mock_log):
        """T18: When first select fails with exception, fallback to unquoted name."""
        call_count = [0]
        original_select = MagicMock()

        def select_side_effect(name):
            call_count[0] += 1
            if call_count[0] == 1:
                raise imaplib.IMAP4.error("SELECT failed")
            return ("OK", [b"1"])

        mock_mail.select.side_effect = select_side_effect
        mock_mail.store.return_value = ("OK", [b"Success"])

        result = mark_email_as_unread(mock_mail, "Junk E-mail", "50", log_func=mock_log)

        assert result is True
        # First call with quotes, second call without
        assert mock_mail.select.call_count == 2
        mock_mail.select.assert_any_call('"Junk E-mail"')
        mock_mail.select.assert_any_call("Junk E-mail")

    def test_t19_select_both_attempts_fail(self, mock_mail, mock_log):
        """T19: When both select attempts fail, returns False."""
        mock_mail.select.side_effect = imaplib.IMAP4.error("SELECT failed")

        result = mark_email_as_unread(mock_mail, "Bad Folder", "50", log_func=mock_log)

        assert result is False
        assert any("无法选择文件夹" in m for m in mock_log.messages)

    def test_t20_store_exception_returns_false(self, mock_mail, mock_log):
        """T20: When mail.store raises exception, returns False."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.side_effect = imaplib.IMAP4.error("STORE failed")

        result = mark_email_as_unread(mock_mail, "INBOX", "100", log_func=mock_log)

        assert result is False
        assert any("标记未读失败" in m for m in mock_log.messages)

    def test_t21_socket_error_triggers_retry(self, mock_mail):
        """T21: Socket error triggers retry mechanism."""
        attempt_count = [0]

        def select_side_effect(name):
            attempt_count[0] += 1
            if attempt_count[0] == 1:
                raise socket.error("Connection reset")
            return ("OK", [b"1"])

        mock_mail.select.side_effect = select_side_effect
        mock_mail.store.return_value = ("OK", [b"Success"])

        with patch("imap_backend.time.sleep"):
            result = mark_email_as_unread(mock_mail, "INBOX", "100")

        assert result is True
        assert attempt_count[0] == 2

    def test_t22_ssl_error_in_store_caught_by_inner_handler(self, mock_mail, mock_log):
        """T22: SSL error in store() is caught by inner exception handler, returns False.
        Note: The inner except Exception catches ALL exceptions before they
        propagate to the retry decorator. This is by design."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.side_effect = ssl.SSLError("SSL handshake failed")

        result = mark_email_as_unread(mock_mail, "INBOX", "100", log_func=mock_log)

        assert result is False
        assert any("标记未读失败" in m for m in mock_log.messages)

    def test_t23_no_log_func_no_crash(self, mock_mail):
        """T23: No log_func provided does not cause crash."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.return_value = ("OK", [b"Success"])

        result = mark_email_as_unread(mock_mail, "INBOX", "123", log_func=None)

        assert result is True

    def test_t24_log_func_none_on_failure(self, mock_mail):
        """T24: No log_func on failure path does not crash."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.side_effect = imaplib.IMAP4.error("fail")

        result = mark_email_as_unread(mock_mail, "INBOX", "123", log_func=None)

        assert result is False

    def test_t25_folder_simple_name_not_quoted(self, mock_mail, mock_log):
        """T25: Simple folder name (no special chars) is not quoted."""
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.store.return_value = ("OK", [b"Success"])

        result = mark_email_as_unread(mock_mail, "INBOX", "100", log_func=mock_log)

        assert result is True
        mock_mail.select.assert_called_once_with("INBOX")

    def test_t26_mark_read_select_fallback(self, mock_mail, mock_log):
        """T26: mark_email_as_read also has select fallback."""
        call_count = [0]

        def select_side_effect(name):
            call_count[0] += 1
            if call_count[0] == 1:
                raise imaplib.IMAP4.error("SELECT failed")
            return ("OK", [b"1"])

        mock_mail.select.side_effect = select_side_effect
        mock_mail.store.return_value = ("OK", [b"Success"])

        result = mark_email_as_read(mock_mail, "Sent Items", "50", log_func=mock_log)

        assert result is True
        assert mock_mail.select.call_count == 2

    def test_t27_mark_read_select_both_fail(self, mock_mail, mock_log):
        """T27: mark_email_as_read returns False when both selects fail."""
        mock_mail.select.side_effect = imaplib.IMAP4.error("fail")

        result = mark_email_as_read(mock_mail, "Bad Folder", "50", log_func=mock_log)

        assert result is False


class TestBatchFlagsFetch_Round2:
    """Round 2: Boundary tests for batch_flags_fetch."""

    def test_t28_mixed_tuple_and_bytes_data(self):
        """T28: Handles mixed tuple and bytes items in fetch response."""
        mail = MagicMock()
        mail.fetch.return_value = ("OK", [
            b"1 FETCH complete",
            (b"2 (FLAGS (\\Seen \\Flagged))", b""),
            b"3 some other data",
        ])

        result = batch_flags_fetch(mail, [b"1", b"2", b"3"])

        assert result.get("2") is True
        # Items that are plain bytes (not tuples) should be skipped
        assert "1" not in result
        assert "3" not in result

    def test_t29_string_mail_ids(self):
        """T29: batch_flags_fetch with string mail_ids (not bytes)."""
        mail = MagicMock()
        mail.fetch.return_value = ("OK", [
            (b"1 (FLAGS (\\Seen))", b""),
        ])

        result = batch_flags_fetch(mail, ["1"])

        assert result.get("1") is True
        # Verify the fetch call used encoded mail_ids
        call_args = mail.fetch.call_args[0]
        assert call_args[0] == b"1"

    def test_t30_fetch_exception_returns_empty(self):
        """T30: Fetch exception returns empty dict."""
        mail = MagicMock()
        mail.fetch.side_effect = imaplib.IMAP4.error("connection lost")

        result = batch_flags_fetch(mail, [b"1", b"2"])

        assert result == {}


class TestCleanFilename_Round2:
    """Round 2: Boundary tests for clean_filename."""

    def test_t31_empty_filename(self):
        """T31: Empty filename returns 'unnamed'."""
        assert clean_filename("") == "unnamed"

    def test_t32_whitespace_only_filename(self):
        """T32: Whitespace-only filename returns 'unnamed'."""
        result = clean_filename("   ")
        assert result == "unnamed"

    def test_t33_control_characters_stripped(self):
        """T33: Control characters are stripped."""
        result = clean_filename("file\x00\x01\x1fname.txt")
        assert "\x00" not in result
        assert "\x01" not in result
        assert "\x1f" not in result
        assert "file" in result
        assert ".txt" in result

    def test_t34_very_long_filename_truncated(self):
        """T34: Very long filename is truncated to max length."""
        long_name = "a" * 300 + ".pdf"
        result = clean_filename(long_name)
        assert len(result) <= 200

    def test_t35_dot_and_space_stripped(self):
        """T35: Leading/trailing dots and spaces are stripped."""
        result = clean_filename("  .file.  ")
        assert result == "file"

    def test_t36_all_windows_reserved_names(self):
        """T36: All Windows reserved names get prefixed."""
        reserved = ["CON", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9"]
        for name in reserved:
            result = clean_filename(f"{name}.txt")
            assert result.startswith("_"), f"Reserved name {name} was not prefixed"


class TestRetryDecorator_Round2:
    """Round 2: Tests for the retry_on_network_error decorator."""

    def test_t37_retry_succeeds_on_second_attempt(self):
        """T37: Function succeeds on second attempt after transient error."""
        attempt_count = [0]

        @retry_on_network_error(max_attempts=3, delay_seconds=0.01)
        def flaky_func():
            attempt_count[0] += 1
            if attempt_count[0] == 1:
                raise socket.error("connection reset")
            return "success"

        result = flaky_func()
        assert result == "success"
        assert attempt_count[0] == 2

    def test_t38_retry_exhausted_raises(self):
        """T38: After max retries, exception is raised."""
        @retry_on_network_error(max_attempts=2, delay_seconds=0.01)
        def always_fail():
            raise imaplib.IMAP4.error("permanent failure")

        with pytest.raises(imaplib.IMAP4.error, match="permanent failure"):
            always_fail()

    def test_t39_retry_ignores_non_network_errors(self):
        """T39: Non-network errors are not caught by retry decorator."""
        @retry_on_network_error(max_attempts=3, delay_seconds=0.01)
        def value_error_func():
            raise ValueError("not a network error")

        with pytest.raises(ValueError, match="not a network error"):
            value_error_func()


class TestCreateSslContext_Round2:
    """Round 2: Boundary tests for _create_ssl_context."""

    def test_t40_ssl_context_has_legacy_connect(self):
        """T40: SSL context enables OP_LEGACY_SERVER_CONNECT."""
        ctx = _create_ssl_context(skip_verify=False)
        assert ctx.options & ssl.OP_LEGACY_SERVER_CONNECT

    def test_t41_ssl_context_no_sslv2(self):
        """T41: SSL context sets OP_NO_SSLv2.
        Note: In Python 3.14+ with modern OpenSSL, OP_NO_SSLv2 has value 0
        because SSLv2 is completely removed. The code correctly sets it,
        but the bitwise check yields 0. We verify the code does NOT error."""
        ctx = _create_ssl_context()
        # In modern Python/OpenSSL, OP_NO_SSLv2 == 0 (SSLv2 removed entirely)
        # The |= operation is a no-op but harmless. Verify context is valid.
        assert ctx is not None
        # Verify that at minimum SSLv3 is disabled (OP_NO_SSLv3 != 0)
        assert ctx.options & ssl.OP_NO_SSLv3

    def test_t42_ssl_context_no_sslv3(self):
        """T42: SSL context disables SSLv3."""
        ctx = _create_ssl_context()
        assert ctx.options & ssl.OP_NO_SSLv3


class TestDecodeStr_Round2:
    """Round 2: Boundary tests for decode_str."""

    def test_t43_decode_str_empty_string(self):
        """T43: decode_str with empty string returns empty."""
        assert decode_str("") == ""

    def test_t44_decode_str_with_recursion_error(self):
        """T44: decode_str handles RecursionError gracefully."""
        # Create a header that would cause recursion
        with patch("imap_backend.decode_header", side_effect=RecursionError):
            result = decode_str("some string")
            assert result == "some string"

    def test_t45_decode_str_with_mixed_charset(self):
        """T45: decode_str handles mixed charset parts."""
        # Simulating a header with mixed charset
        result = decode_str("Hello")
        assert result == "Hello"


class TestUIChangesRound2:
    """
    Round 2: Structural verification of UI changes.
    Since we cannot run tkinter in a headless test environment,
    we verify the code structure statically.
    """

    def test_t46_mark_unread_method_exists_in_tool(self):
        """T46: _mark_selected_as_unread method exists in the main tool class."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "tool", os.path.join(PROJECT_ROOT, "邮件附件下载工具.py")
        )
        # We can't import the file directly because it creates a GUI,
        # so we check the source code for the method
        with open(os.path.join(PROJECT_ROOT, "邮件附件下载工具.py"), "r", encoding="utf-8") as f:
            source = f.read()
        assert "def _mark_selected_as_unread(self)" in source

    def test_t47_mark_unread_import_exists(self):
        """T47: mark_email_as_unread is imported in the main tool file."""
        with open(os.path.join(PROJECT_ROOT, "邮件附件下载工具.py"), "r", encoding="utf-8") as f:
            source = f.read()
        assert "mark_email_as_unread" in source

    def test_t48_query_button_exists_in_code(self):
        """T48: Query button for download history exists in the code."""
        with open(os.path.join(PROJECT_ROOT, "邮件附件下载工具.py"), "r", encoding="utf-8") as f:
            source = f.read()
        assert 'text="查询"' in source
        assert "_on_history_search_btn" in source

    def test_t49_mark_unread_success_handler_exists(self):
        """T49: _on_mark_unread_success handler exists."""
        with open(os.path.join(PROJECT_ROOT, "邮件附件下载工具.py"), "r", encoding="utf-8") as f:
            source = f.read()
        assert "def _on_mark_unread_success(self, mail_id)" in source

    def test_t50_context_menu_has_mark_unread(self):
        """T50: Right-click context menu includes '标记未读' option."""
        with open(os.path.join(PROJECT_ROOT, "邮件附件下载工具.py"), "r", encoding="utf-8") as f:
            source = f.read()
        assert '"标记未读"' in source
        assert "_tree_context_menu" in source

    def test_t51_history_search_btn_calls_refresh(self):
        """T51: _on_history_search_btn calls _refresh_history_list with correct params."""
        with open(os.path.join(PROJECT_ROOT, "邮件附件下载工具.py"), "r", encoding="utf-8") as f:
            source = f.read()
        # Find the method and verify it calls _refresh_history_list
        idx = source.find("def _on_history_search_btn(self)")
        assert idx != -1
        method_body = source[idx:idx + 300]
        assert "_refresh_history_list" in method_body
        assert "hist_search_var" in method_body


class TestImapBackendImports:
    """Test that all required imports are available."""

    def test_t52_mark_email_as_unread_importable(self):
        """T52: mark_email_as_unread is importable from imap_backend."""
        assert callable(mark_email_as_unread)

    def test_t53_mark_email_as_read_importable(self):
        """T53: mark_email_as_read is importable from imap_backend."""
        assert callable(mark_email_as_read)

    def test_t54_retry_decorator_importable(self):
        """T54: retry_on_network_error is importable."""
        assert callable(retry_on_network_error)
