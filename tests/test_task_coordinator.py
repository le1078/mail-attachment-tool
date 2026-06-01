"""TaskCoordinator 单元测试模块。

覆盖初始化、连接测试、下载/发送/查询、附件解析、
错误处理、回调传递等所有场景。
"""

from unittest.mock import MagicMock, patch, call

import pytest

from task_coordinator import TaskCoordinator


# ------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------

SAMPLE_CONFIG = {
    "imap_server": "imap.example.com",
    "imap_port": 993,
    "email_user": "user@example.com",
    "email_pass": "password123",
    "skip_ssl_verify": False,
    "sender_filter_list": ["sender@test.com"],
    "save_folder": "/tmp/attachments",
    "download_keyword_filter": "report",
    "download_read_status": "all",
    "download_filter_days": [],
    "download_filter_time_enabled": False,
    "download_filter_time_start": "00:00",
    "download_filter_time_end": "23:59",
    "download_filter_date_enabled": False,
    "download_filter_date_start": "",
    "download_filter_date_end": "",
    "smtp_server": "smtp.example.com",
    "smtp_port": 465,
    "smtp_ssl": True,
    "skip_ssl_smtp": False,
    "send_user": "sender@example.com",
    "send_pass": "sender_pass",
    "send_to": "to@example.com",
    "send_subject": "Test Subject",
    "send_body": "Test Body",
    "send_attachment_mode": "single",
    "send_attachment": "/tmp/test.pdf",
    "send_attachment_list": [],
}


@pytest.fixture
def mock_root() -> MagicMock:
    """创建 mock tkinter root。"""
    return MagicMock()


@pytest.fixture
def mock_log_func() -> MagicMock:
    """创建 mock 日志函数。"""
    return MagicMock()


@pytest.fixture
def coordinator(mock_root: MagicMock, mock_log_func: MagicMock) -> TaskCoordinator:
    """创建 TaskCoordinator 实例。"""
    return TaskCoordinator(mock_root, SAMPLE_CONFIG.copy(), mock_log_func)


# ------------------------------------------------------------------
# 初始化测试
# ------------------------------------------------------------------


class TestTaskCoordinatorInit:
    """TaskCoordinator 初始化测试。"""

    def test_init_stores_all_attributes(
        self, mock_root: MagicMock, mock_log_func: MagicMock
    ) -> None:
        """验证 __init__ 正确存储 root、config、log_func。"""
        coordinator = TaskCoordinator(mock_root, SAMPLE_CONFIG, mock_log_func)

        assert coordinator.root is mock_root
        assert coordinator.config is SAMPLE_CONFIG
        assert coordinator.log_func is mock_log_func

    def test_init_config_is_independent_copy(
        self, mock_root: MagicMock, mock_log_func: MagicMock
    ) -> None:
        """验证传入的 config 与内部存储保持一致（引用）。"""
        config = SAMPLE_CONFIG.copy()
        coordinator = TaskCoordinator(mock_root, config, mock_log_func)

        assert coordinator.config == config


# ------------------------------------------------------------------
# test_imap 测试
# ------------------------------------------------------------------


class TestTestImap:
    """test_imap() 测试。"""

    def test_creates_background_worker(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 test_imap 创建 BackgroundWorker 并调用 run。"""
        on_complete = MagicMock()
        on_error = MagicMock()

        with patch("task_coordinator.BackgroundWorker") as MockBW:
            mock_worker = MagicMock()
            MockBW.return_value = mock_worker

            coordinator.test_imap(on_complete=on_complete, on_error=on_error)

            MockBW.assert_called_once_with(
                coordinator.root,
                on_complete=on_complete,
                on_error=on_error,
            )
            mock_worker.run.assert_called_once()

    def test_passes_correct_imap_params(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 test_imap 传递正确的 IMAP 连接参数。"""
        with patch("task_coordinator.BackgroundWorker") as MockBW:
            mock_worker = MagicMock()
            MockBW.return_value = mock_worker

            coordinator.test_imap()

            call_args = mock_worker.run.call_args
            # run(test_imap_connection, server, port, user, pass, skip_ssl)
            from imap_backend import test_imap_connection
            assert call_args[0][0] is test_imap_connection
            assert call_args[0][1] == "imap.example.com"
            assert call_args[0][2] == 993
            assert call_args[0][3] == "user@example.com"
            assert call_args[0][4] == "password123"
            assert call_args[0][5] is False  # skip_ssl_verify

    def test_skip_ssl_verify_true(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 skip_ssl_verify=True 时正确传递。"""
        coordinator.config["skip_ssl_verify"] = True

        with patch("task_coordinator.BackgroundWorker") as MockBW:
            mock_worker = MagicMock()
            MockBW.return_value = mock_worker

            coordinator.test_imap()

            call_args = mock_worker.run.call_args
            assert call_args[0][5] is True


# ------------------------------------------------------------------
# test_smtp 测试
# ------------------------------------------------------------------


class TestTestSmtp:
    """test_smtp() 测试。"""

    def test_creates_background_worker(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 test_smtp 创建 BackgroundWorker。"""
        on_complete = MagicMock()
        on_error = MagicMock()

        with patch("task_coordinator.BackgroundWorker") as MockBW:
            mock_worker = MagicMock()
            MockBW.return_value = mock_worker

            coordinator.test_smtp(on_complete=on_complete, on_error=on_error)

            MockBW.assert_called_once_with(
                coordinator.root,
                on_complete=on_complete,
                on_error=on_error,
            )
            mock_worker.run.assert_called_once()

    def test_passes_correct_smtp_params(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 test_smtp 传递正确的 SMTP 连接参数。"""
        with patch("task_coordinator.BackgroundWorker") as MockBW:
            mock_worker = MagicMock()
            MockBW.return_value = mock_worker

            coordinator.test_smtp()

            call_args = mock_worker.run.call_args
            from smtp_backend import test_smtp_connection
            assert call_args[0][0] is test_smtp_connection
            assert call_args[0][1] == "smtp.example.com"
            assert call_args[0][2] == 465
            assert call_args[0][3] is True  # smtp_ssl
            assert call_args[0][4] == "sender@example.com"  # send_user
            assert call_args[0][5] == "sender_pass"  # send_pass
            assert call_args[0][6] is False  # skip_ssl_smtp

    def test_fallback_to_email_user_when_send_user_empty(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 send_user 为空时回退到 email_user。"""
        coordinator.config["send_user"] = ""
        coordinator.config["send_pass"] = ""

        with patch("task_coordinator.BackgroundWorker") as MockBW:
            mock_worker = MagicMock()
            MockBW.return_value = mock_worker

            coordinator.test_smtp()

            call_args = mock_worker.run.call_args
            assert call_args[0][4] == "user@example.com"  # 回退到 email_user
            assert call_args[0][5] == "password123"  # 回退到 email_pass


# ------------------------------------------------------------------
# run_download_once 测试
# ------------------------------------------------------------------


class TestRunDownloadOnce:
    """run_download_once() 测试。"""

    def test_creates_background_worker(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 run_download_once 创建 BackgroundWorker。"""
        on_complete = MagicMock()
        on_error = MagicMock()

        with patch("task_coordinator.BackgroundWorker") as MockBW:
            mock_worker = MagicMock()
            MockBW.return_value = mock_worker

            coordinator.run_download_once(
                on_complete=on_complete, on_error=on_error
            )

            MockBW.assert_called_once_with(
                coordinator.root,
                on_complete=on_complete,
                on_error=on_error,
            )
            mock_worker.run.assert_called_once_with(coordinator._do_download)

    def test_do_download_connects_and_fetches(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 _do_download 连接 IMAP 并调用 fetch_attachments。"""
        mock_mail = MagicMock()

        with patch("task_coordinator.connect_imap", return_value=mock_mail) as mock_connect, \
             patch("task_coordinator.download_attachments", return_value=5) as mock_fetch:
            count = coordinator._do_download()

            mock_connect.assert_called_once_with(
                "imap.example.com", 993, "user@example.com", "password123",
                skip_ssl_verify=False,
            )
            mock_fetch.assert_called_once()
            mock_mail.logout.assert_called_once()
            assert count == 5

    def test_do_download_logs_out_on_error(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 _do_download 异常时仍然执行 logout。"""
        mock_mail = MagicMock()

        with patch("task_coordinator.connect_imap", return_value=mock_mail):
            with patch(
                "task_coordinator.download_attachments",
                side_effect=RuntimeError("download failed"),
            ):
                with pytest.raises(RuntimeError):
                    coordinator._do_download()

            mock_mail.logout.assert_called_once()

    def test_do_download_logout_error_suppressed(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 logout 抛出异常时不影响主流程。"""
        mock_mail = MagicMock()
        mock_mail.logout.side_effect = OSError("connection lost")

        with patch("task_coordinator.connect_imap", return_value=mock_mail):
            with patch("task_coordinator.download_attachments", return_value=3):
                count = coordinator._do_download()

            assert count == 3
            mock_mail.logout.assert_called_once()


# ------------------------------------------------------------------
# run_send_once 测试
# ------------------------------------------------------------------


class TestRunSendOnce:
    """run_send_once() 测试。"""

    def test_creates_background_worker(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 run_send_once 创建 BackgroundWorker。"""
        on_complete = MagicMock()
        on_error = MagicMock()

        with patch("task_coordinator.BackgroundWorker") as MockBW:
            mock_worker = MagicMock()
            MockBW.return_value = mock_worker

            coordinator.run_send_once(
                on_complete=on_complete, on_error=on_error
            )

            MockBW.assert_called_once_with(
                coordinator.root,
                on_complete=on_complete,
                on_error=on_error,
            )
            mock_worker.run.assert_called_once_with(coordinator._do_send)

    def test_do_send_calls_send_email(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 _do_send 调用 send_email 并传递正确参数。"""
        with patch("task_coordinator.send_email") as mock_send:
            coordinator._do_send()

            mock_send.assert_called_once()
            call_args = mock_send.call_args

            assert call_args[0][0] == "smtp.example.com"
            assert call_args[0][1] == 465
            assert call_args[0][2] is True  # smtp_ssl
            assert call_args[0][3] == "sender@example.com"  # send_user
            assert call_args[0][4] == "sender_pass"  # send_pass
            assert call_args[0][5] == "to@example.com"  # send_to
            assert call_args[0][6] == "Test Subject"
            assert call_args[0][7] == "Test Body"
            assert call_args[0][8] == ["/tmp/test.pdf"]  # attachment_paths
            assert call_args[0][9] is False  # skip_ssl_smtp
            # log_func callback
            assert callable(call_args[0][10])
            assert call_args[1]["config"] is coordinator.config

    def test_do_send_fallback_credentials(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 send_user 为空时回退到 email_user。"""
        coordinator.config["send_user"] = ""
        coordinator.config["send_pass"] = ""

        with patch("task_coordinator.send_email") as mock_send:
            coordinator._do_send()

            call_args = mock_send.call_args
            assert call_args[0][3] == "user@example.com"
            assert call_args[0][4] == "password123"


# ------------------------------------------------------------------
# _resolve_send_attachments 测试
# ------------------------------------------------------------------


class TestResolveSendAttachments:
    """_resolve_send_attachments() 测试。"""

    def test_single_mode_with_path(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 single 模式返回单个路径列表。"""
        coordinator.config["send_attachment_mode"] = "single"
        coordinator.config["send_attachment"] = "/tmp/file.pdf"

        result = coordinator._resolve_send_attachments()

        assert result == ["/tmp/file.pdf"]

    def test_single_mode_empty_path(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 single 模式路径为空时返回空列表。"""
        coordinator.config["send_attachment_mode"] = "single"
        coordinator.config["send_attachment"] = ""

        result = coordinator._resolve_send_attachments()

        assert result == []

    def test_multi_mode_returns_list(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 multi 模式返回附件列表。"""
        coordinator.config["send_attachment_mode"] = "multi"
        coordinator.config["send_attachment_list"] = ["/tmp/a.pdf", "/tmp/b.pdf"]

        result = coordinator._resolve_send_attachments()

        assert result == ["/tmp/a.pdf", "/tmp/b.pdf"]

    def test_multi_mode_empty_list(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 multi 模式列表为空时返回空列表。"""
        coordinator.config["send_attachment_mode"] = "multi"
        coordinator.config["send_attachment_list"] = []

        result = coordinator._resolve_send_attachments()

        assert result == []

    def test_folder_mode_lists_files(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 folder 模式列出文件夹内所有文件。"""
        import os

        coordinator.config["send_attachment_mode"] = "folder"
        coordinator.config["send_attachment"] = "/tmp/folder"

        with patch("os.listdir", return_value=["a.pdf", "c.txt"]):
            with patch("os.path.isfile", return_value=True):
                result = coordinator._resolve_send_attachments()

                expected = [
                    os.path.join("/tmp/folder", "a.pdf"),
                    os.path.join("/tmp/folder", "c.txt"),
                ]
                assert result == expected

    def test_folder_mode_empty_path(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 folder 模式路径为空时返回空列表。"""
        coordinator.config["send_attachment_mode"] = "folder"
        coordinator.config["send_attachment"] = ""

        result = coordinator._resolve_send_attachments()

        assert result == []

    def test_unknown_mode_returns_empty(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证未知模式返回空列表。"""
        coordinator.config["send_attachment_mode"] = "unknown"

        result = coordinator._resolve_send_attachments()

        assert result == []


# ------------------------------------------------------------------
# run_query 测试
# ------------------------------------------------------------------


class TestRunQuery:
    """run_query() 测试。"""

    def test_creates_background_worker(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 run_query 创建 BackgroundWorker。"""
        on_complete = MagicMock()
        on_error = MagicMock()

        with patch("task_coordinator.BackgroundWorker") as MockBW:
            mock_worker = MagicMock()
            MockBW.return_value = mock_worker

            coordinator.run_query(
                folder="INBOX",
                criteria="ALL",
                on_complete=on_complete,
                on_error=on_error,
            )

            MockBW.assert_called_once_with(
                coordinator.root,
                on_complete=on_complete,
                on_error=on_error,
            )
            mock_worker.run.assert_called_once_with(
                coordinator._do_query, "INBOX", "ALL"
            )

    def test_do_query_connects_and_queries(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 _do_query 连接 IMAP 并调用 query_emails。"""
        mock_mail = MagicMock()
        expected_results = [
            {"id": "1", "from": "a@b.com", "subject": "Test"},
        ]

        with patch("task_coordinator.connect_imap", return_value=mock_mail) as mock_connect, \
             patch("task_coordinator.query_emails", return_value=expected_results) as mock_query:
            results = coordinator._do_query("INBOX", "UNSEEN")

            mock_connect.assert_called_once_with(
                "imap.example.com", 993, "user@example.com", "password123",
                skip_ssl_verify=False,
            )
            mock_query.assert_called_once()
            assert mock_query.call_args[0][0] is mock_mail
            assert mock_query.call_args[0][1] == "INBOX"
            assert mock_query.call_args[0][2] == "UNSEEN"
            assert results == expected_results

    def test_do_query_logs_out_on_error(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 _do_query 异常时仍然执行 logout。"""
        mock_mail = MagicMock()

        with patch("task_coordinator.connect_imap", return_value=mock_mail):
            with patch(
                "task_coordinator.query_emails",
                side_effect=RuntimeError("query failed"),
            ):
                with pytest.raises(RuntimeError):
                    coordinator._do_query("INBOX", "ALL")

            mock_mail.logout.assert_called_once()

    def test_do_query_logout_error_suppressed(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 _do_query 中 logout 异常被抑制。"""
        mock_mail = MagicMock()
        mock_mail.logout.side_effect = OSError("connection lost")

        with patch("task_coordinator.connect_imap", return_value=mock_mail):
            with patch("task_coordinator.query_emails", return_value=[]):
                results = coordinator._do_query("Sent", "ALL")

            assert results == []
            mock_mail.logout.assert_called_once()


# ------------------------------------------------------------------
# 回调传递测试
# ------------------------------------------------------------------


class TestCallbackPropagation:
    """回调传递集成测试。"""

    def test_test_imap_callback_none_ok(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 test_imap 回调为 None 时不崩溃。"""
        with patch("task_coordinator.BackgroundWorker") as MockBW:
            mock_worker = MagicMock()
            MockBW.return_value = mock_worker

            coordinator.test_imap(on_complete=None, on_error=None)

            MockBW.assert_called_once_with(
                coordinator.root,
                on_complete=None,
                on_error=None,
            )

    def test_run_download_callback_none_ok(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 run_download_once 回调为 None 时不崩溃。"""
        with patch("task_coordinator.BackgroundWorker") as MockBW:
            MockBW.return_value = MagicMock()

            coordinator.run_download_once(on_complete=None, on_error=None)

    def test_run_send_callback_none_ok(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 run_send_once 回调为 None 时不崩溃。"""
        with patch("task_coordinator.BackgroundWorker") as MockBW:
            MockBW.return_value = MagicMock()

            coordinator.run_send_once(on_complete=None, on_error=None)

    def test_run_query_callback_none_ok(
        self, coordinator: TaskCoordinator
    ) -> None:
        """验证 run_query 回调为 None 时不崩溃。"""
        with patch("task_coordinator.BackgroundWorker") as MockBW:
            MockBW.return_value = MagicMock()

            coordinator.run_query(
                "INBOX", "ALL", on_complete=None, on_error=None
            )