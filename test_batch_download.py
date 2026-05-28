import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import socket

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from imap_backend import batch_fetch_email_details


class TestBatchFetchEmailDetails(unittest.TestCase):

    def setUp(self):
        self.log_messages = []

    def log_func(self, msg):
        self.log_messages.append(msg)

    def test_empty_mail_ids_returns_empty_list(self):
        mail = MagicMock()
        results, failed = batch_fetch_email_details(mail, [])
        self.assertEqual(results, [])
        self.assertEqual(failed, 0)

    @patch("imap_backend.fetch_email_detail")
    def test_batch_fetch_multiple_ids(self, mock_fetch):
        mock_fetch.side_effect = [
            {"subject": "Test1", "attachments": [{"filename": "a.pdf", "payload": b"data1"}]},
            {"subject": "Test2", "attachments": [{"filename": "b.pdf", "payload": b"data2"}]},
        ]
        mail = MagicMock()
        results, failed = batch_fetch_email_details(mail, ["1", "2"], log_func=self.log_func)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["subject"], "Test1")
        self.assertEqual(results[1]["subject"], "Test2")
        self.assertEqual(failed, 0)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("imap_backend.fetch_email_detail")
    def test_batch_fetch_skips_failed_mails(self, mock_fetch):
        mock_fetch.side_effect = [
            {"subject": "Good", "attachments": []},
            None,
            {"subject": "Also Good", "attachments": [{"filename": "c.txt", "payload": b"data3"}]},
        ]
        mail = MagicMock()
        results, failed = batch_fetch_email_details(mail, ["a", "b", "c"], log_func=self.log_func)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["subject"], "Good")
        self.assertEqual(results[1]["subject"], "Also Good")
        self.assertEqual(failed, 1)

    @patch("imap_backend.fetch_email_detail")
    def test_batch_fetch_network_error_retries(self, mock_fetch):
        mock_fetch.side_effect = socket.error("network error")
        mail = MagicMock()
        with self.assertRaises(socket.error):
            batch_fetch_email_details(mail, ["1"])
        self.assertEqual(mock_fetch.call_count, 3)

    @patch("imap_backend.fetch_email_detail")
    def test_batch_fetch_single_mail(self, mock_fetch):
        mock_fetch.return_value = {
            "subject": "Single",
            "attachments": [{"filename": "test.xlsx", "payload": b"excel"}],
        }
        mail = MagicMock()
        results, failed = batch_fetch_email_details(mail, ["42"], log_func=self.log_func)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["subject"], "Single")
        self.assertEqual(len(results[0]["attachments"]), 1)
        self.assertEqual(failed, 0)

    @patch("imap_backend.fetch_email_detail")
    def test_batch_fetch_with_none_detail(self, mock_fetch):
        mock_fetch.return_value = None
        mail = MagicMock()
        results, failed = batch_fetch_email_details(mail, ["x", "y", "z"], log_func=self.log_func)
        self.assertEqual(results, [])
        self.assertEqual(failed, 3)

    def test_batch_fetch_without_log_func(self):
        mail = MagicMock()
        results, failed = batch_fetch_email_details(mail, [])
        self.assertEqual(results, [])
        self.assertEqual(failed, 0)

    @patch("imap_backend.fetch_email_detail")
    def test_batch_fetch_large_batch(self, mock_fetch):
        mock_fetch.return_value = {"subject": "Mail", "attachments": []}
        mail = MagicMock()
        mail_ids = [str(i) for i in range(100)]
        results, failed = batch_fetch_email_details(mail, mail_ids)
        self.assertEqual(len(results), 100)
        self.assertEqual(failed, 0)
        self.assertEqual(mock_fetch.call_count, 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)