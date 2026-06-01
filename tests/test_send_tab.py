"""Tests for tabs/send_tab.py."""
from __future__ import annotations

import os
import sys
import datetime
import tkinter as tk
from tkinter import ttk
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tabs.base_tab import TabCallbacks
from tabs.send_tab import SendTab


@pytest.fixture(scope="session")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def _make_tab_callbacks(root, config_dict=None, coordinator=None):
    cfg = config_dict.copy() if config_dict else {}
    coordinator = coordinator or MagicMock()

    def _config_getter():
        return cfg

    save_config_mock = MagicMock()

    def _config_setter_side_effect(new_cfg):
        snapshot = dict(new_cfg)
        cfg.clear()
        cfg.update(snapshot)

    save_config_mock.side_effect = _config_setter_side_effect

    return TabCallbacks(
        root=root,
        config=_config_getter,
        save_config=save_config_mock,
        log=MagicMock(),
        record_download=MagicMock(),
        coordinator=coordinator,
        get_weekday_names=MagicMock(return_value=["周一", "周二", "周三", "周四", "周五", "周六", "周日"]),
    )


def _make_send_tab(tk_root, config_dict=None, coordinator=None):
    cb = _make_tab_callbacks(root=tk_root, config_dict=config_dict, coordinator=coordinator)
    parent = ttk.Frame(tk_root)
    tab = SendTab(parent, cb)
    return tab, cb


class TestSendTabInit:
    def test_init_creates_variables(self, tk_root):
        tab, cb = _make_send_tab(tk_root)

        assert isinstance(tab.var_smtp_ssl, tk.BooleanVar)
        assert tab.var_smtp_ssl.get() is True
        assert isinstance(tab.var_skip_ssl_smtp, tk.BooleanVar)
        assert tab.var_skip_ssl_smtp.get() is False
        assert isinstance(tab.var_att_mode, tk.StringVar)
        assert tab.var_att_mode.get() == "single"
        assert isinstance(tab.var_sd_enabled, tk.BooleanVar)
        assert tab.var_sd_enabled.get() is False
        assert len(tab.send_day_vars) == 7

    def test_init_creates_widgets(self, tk_root):
        tab, cb = _make_send_tab(tk_root)

        assert isinstance(tab.entry_smtp_server, ttk.Entry)
        assert isinstance(tab.entry_smtp_port, ttk.Entry)
        assert isinstance(tab.entry_send_user, ttk.Entry)
        assert isinstance(tab.entry_send_pass, ttk.Entry)
        assert isinstance(tab.entry_send_to, ttk.Entry)
        assert isinstance(tab.entry_send_subject, ttk.Entry)
        assert isinstance(tab.text_send_body, tk.Text)
        assert isinstance(tab.entry_send_attachment, ttk.Entry)
        assert isinstance(tab.spin_send_hour, ttk.Spinbox)
        assert isinstance(tab.spin_send_min, ttk.Spinbox)
        assert isinstance(tab.spin_send_sec, ttk.Spinbox)

    def test_init_loads_config_to_ui(self, tk_root):
        config = {
            "smtp_server": "smtp.test.com",
            "smtp_port": 25,
            "smtp_ssl": False,
            "skip_ssl_smtp": True,
            "send_user": "sender@test.com",
            "send_pass": "secret",
            "send_to": "recv@test.com",
            "send_subject": "Hello",
            "send_body": "Test body",
            "send_attachment_mode": "single",
            "send_attachment": "/tmp/file.pdf",
            "send_attachment_list": [],
            "schedule_send": {
                "days": [1, 3, 5],
                "hour": 14,
                "minute": 30,
                "second": 15,
                "enabled": True,
            },
        }
        tab, cb = _make_send_tab(tk_root, config_dict=config)

        assert tab.entry_smtp_server.get() == "smtp.test.com"
        assert tab.entry_smtp_port.get() == "25"
        assert tab.var_smtp_ssl.get() is False
        assert tab.var_skip_ssl_smtp.get() is True
        assert tab.entry_send_user.get() == "sender@test.com"
        assert tab.entry_send_pass.get() == "secret"
        assert tab.entry_send_to.get() == "recv@test.com"
        assert tab.entry_send_subject.get() == "Hello"
        assert tab.text_send_body.get(1.0, tk.END).strip() == "Test body"
        assert tab.var_att_mode.get() == "single"
        assert tab.entry_send_attachment.get() == "/tmp/file.pdf"
        assert tab.var_sd_enabled.get() is True
        assert tab.spin_send_hour.get() == "14"
        assert tab.spin_send_min.get() == "30"
        assert tab.spin_send_sec.get() == "15"
        assert tab.send_day_vars[0].get() is True
        assert tab.send_day_vars[2].get() is True
        assert tab.send_day_vars[4].get() is True

    def test_init_loads_config_multi_attachment(self, tk_root):
        config = {
            "send_attachment_mode": "multi",
            "send_attachment_list": ["/tmp/a.pdf", "/tmp/b.txt"],
        }
        tab, cb = _make_send_tab(tk_root, config_dict=config)

        assert tab.var_att_mode.get() == "multi"
        assert tab.entry_send_attachment.get() == "/tmp/a.pdf; /tmp/b.txt"


class TestSaveSendConfig:
    def test_save_send_config_writes_keys(self, tk_root):
        config = {"smtp_server": "", "email_user": "base@test.com"}
        tab, cb = _make_send_tab(tk_root, config_dict=config)

        tab.entry_smtp_server.delete(0, tk.END)
        tab.entry_smtp_server.insert(0, "smtp.qq.com")
        tab.entry_smtp_port.delete(0, tk.END)
        tab.entry_smtp_port.insert(0, "465")
        tab.entry_send_to.delete(0, tk.END)
        tab.entry_send_to.insert(0, "to@x.com")
        tab.entry_send_subject.delete(0, tk.END)
        tab.entry_send_subject.insert(0, "Test")
        tab.text_send_body.delete(1.0, tk.END)
        tab.text_send_body.insert(1.0, "Body")

        tab._save_send_config()

        cb.save_config.assert_called_once()
        saved = cb.save_config.call_args[0][0]
        assert saved["smtp_server"] == "smtp.qq.com"
        assert saved["smtp_port"] == 465
        assert saved["send_to"] == "to@x.com"
        assert saved["send_subject"] == "Test"
        assert saved["send_body"] == "Body"

    def test_save_send_config_preserves_other_keys(self, tk_root):
        config = {
            "imap_server": "imap.x.com",
            "smtp_server": "smtp.x.com",
            "email_user": "u@x.com",
        }
        tab, cb = _make_send_tab(tk_root, config_dict=config)

        tab._save_send_config()

        saved = cb.save_config.call_args[0][0]
        assert saved["imap_server"] == "imap.x.com"
        assert saved["email_user"] == "u@x.com"
        assert saved["smtp_server"] == "smtp.x.com"


class TestClearSendConfig:
    def test_clear_resets_widgets(self, tk_root):
        config = {"smtp_server": "smtp.old.com", "send_user": "old", "send_to": "old@t.com"}
        tab, cb = _make_send_tab(tk_root, config_dict=config)

        tab._clear_send_config()

        assert tab.entry_smtp_server.get() == ""
        assert tab.entry_smtp_port.get() == "465"
        assert tab.var_smtp_ssl.get() is True
        assert tab.var_skip_ssl_smtp.get() is False
        assert tab.entry_send_user.get() == ""
        assert tab.entry_send_pass.get() == ""
        assert tab.entry_send_to.get() == ""
        assert tab.entry_send_subject.get() == ""
        assert tab.text_send_body.get(1.0, tk.END).strip() == ""
        assert tab.entry_send_attachment.get() == ""
        assert tab.var_att_mode.get() == "single"
        assert tab.var_sd_enabled.get() is False
        assert all(not v.get() for v in tab.send_day_vars)
        assert tab.spin_send_hour.get() == "8"

    def test_clear_persists_empty_config(self, tk_root):
        config = {"smtp_server": "old", "send_user": "old"}
        tab, cb = _make_send_tab(tk_root, config_dict=config)

        tab._clear_send_config()

        saved = cb.save_config.call_args[0][0]
        assert saved["smtp_server"] == ""
        assert saved["smtp_port"] == 465
        assert saved["send_user"] == ""


class TestAttachmentHelpers:
    def test_get_attachment_list_empty(self, tk_root):
        tab, cb = _make_send_tab(tk_root)
        tab.entry_send_attachment.delete(0, tk.END)

        result = tab._get_attachment_list_from_ui()
        assert result == []

    def test_get_attachment_list_single(self, tk_root):
        tab, cb = _make_send_tab(tk_root)
        tab.entry_send_attachment.delete(0, tk.END)
        tab.entry_send_attachment.insert(0, "/tmp/file.pdf")

        result = tab._get_attachment_list_from_ui()
        assert result == ["/tmp/file.pdf"]

    def test_get_attachment_list_multi(self, tk_root):
        tab, cb = _make_send_tab(tk_root)
        tab.entry_send_attachment.delete(0, tk.END)
        tab.entry_send_attachment.insert(0, "/tmp/a.pdf; /tmp/b.txt")

        result = tab._get_attachment_list_from_ui()
        assert result == ["/tmp/a.pdf", "/tmp/b.txt"]

    def test_resolve_attachment_paths_single_valid(self, tk_root):
        tab, cb = _make_send_tab(tk_root)

        with patch("os.path.isfile", return_value=True):
            result = tab._resolve_attachment_paths({
                "send_attachment_mode": "single",
                "send_attachment": "/tmp/file.pdf",
            })
        assert result == ["/tmp/file.pdf"]

    def test_resolve_attachment_paths_single_missing(self, tk_root):
        tab, cb = _make_send_tab(tk_root)

        with patch("os.path.isfile", return_value=False):
            result = tab._resolve_attachment_paths({
                "send_attachment_mode": "single",
                "send_attachment": "/tmp/missing.pdf",
            })
        assert result == []

    def test_resolve_attachment_paths_multi(self, tk_root):
        tab, cb = _make_send_tab(tk_root)

        with patch("os.path.isfile", side_effect=[True, False, True]):
            result = tab._resolve_attachment_paths({
                "send_attachment_mode": "multi",
                "send_attachment_list": ["/tmp/a.pdf", "/tmp/b.txt", "/tmp/c.pdf"],
            })
        assert result == ["/tmp/a.pdf", "/tmp/c.pdf"]

    def test_resolve_attachment_paths_folder(self, tk_root):
        tab, cb = _make_send_tab(tk_root)

        with patch("os.path.isdir", return_value=True), \
             patch("os.listdir", return_value=["a.pdf", "b.txt", "subdir"]), \
             patch("os.path.isfile", side_effect=[True, True, False]), \
             patch("os.path.join", side_effect=lambda a, b: f"{a}/{b}"):
            result = tab._resolve_attachment_paths({
                "send_attachment_mode": "folder",
                "send_attachment_list": ["/tmp/mydir"],
            })
        assert len(result) == 2
        assert "/tmp/mydir/a.pdf" in result
        assert "/tmp/mydir/b.txt" in result

    def test_resolve_attachment_paths_folder_not_exists(self, tk_root):
        tab, cb = _make_send_tab(tk_root)

        with patch("os.path.isdir", return_value=False):
            result = tab._resolve_attachment_paths({
                "send_attachment_mode": "folder",
                "send_attachment_list": ["/tmp/nodir"],
            })
        assert result == []


class TestFormatSubject:
    def test_format_subject_replaces_placeholders(self, tk_root):
        from task_coordinator import TaskCoordinator
        now = datetime.datetime.now()

        result = TaskCoordinator._format_subject("Report YYYY-MM-DD")
        assert str(now.year) in result
        assert str(now.month) in result
        assert str(now.day) in result

    def test_format_subject_no_placeholders(self, tk_root):
        from task_coordinator import TaskCoordinator

        result = TaskCoordinator._format_subject("Plain Subject")
        assert result == "Plain Subject"


class TestAttachmentModeChanged:
    def test_mode_changed_clears_entry(self, tk_root):
        tab, cb = _make_send_tab(tk_root)
        tab.entry_send_attachment.delete(0, tk.END)
        tab.entry_send_attachment.insert(0, "old_value")

        tab._on_send_attachment_mode_changed()

        assert tab.entry_send_attachment.get() == ""


class TestTestSmtpBtn:
    def test_missing_credentials_shows_error(self, tk_root):
        tab, cb = _make_send_tab(tk_root)

        with patch("tkinter.messagebox.showerror") as mock_err:
            tab._test_smtp_btn()

        mock_err.assert_called_once()
        assert "账号" in str(mock_err.call_args[0][1])

    def test_valid_credentials_calls_coordinator(self, tk_root):
        config = {
            "send_user": "u@x.com",
            "send_pass": "p",
            "email_user": "",
            "email_pass": "",
        }
        coordinator = MagicMock()
        tab, cb = _make_send_tab(tk_root, config_dict=config, coordinator=coordinator)

        with patch("tkinter.messagebox.showerror"):
            tab._test_smtp_btn()

        coordinator.test_smtp.assert_called_once()


class TestTestSend:
    def test_missing_send_user_shows_error(self, tk_root):
        tab, cb = _make_send_tab(tk_root)

        with patch("tkinter.messagebox.showerror") as mock_err:
            tab._test_send()

        calls = [c[0][1] for c in mock_err.call_args_list]
        assert any("发件人" in c for c in calls)

    def test_missing_recipient_shows_error(self, tk_root):
        config = {"send_user": "u@x.com", "send_pass": "p", "send_to": ""}
        tab, cb = _make_send_tab(tk_root, config_dict=config)

        with patch("tkinter.messagebox.showerror") as mock_err:
            tab._test_send()

        calls = [c[0][1] for c in mock_err.call_args_list]
        assert any("收件人" in c for c in calls)

    def test_valid_params_calls_coordinator(self, tk_root):
        config = {
            "send_user": "u@x.com",
            "send_pass": "p",
            "send_to": "to@x.com",
        }
        coordinator = MagicMock()
        tab, cb = _make_send_tab(tk_root, config_dict=config, coordinator=coordinator)

        with patch("tkinter.messagebox.showerror"):
            tab._test_send()

        coordinator.run_send_once.assert_called_once()


class TestBaseTabInterface:
    def test_base_tab_stores_cb(self, tk_root):
        tab, cb = _make_send_tab(tk_root)
        assert tab.cb is cb
        assert tab.parent is not None
        assert tab.frame is not None

    def test_cb_root_accessible(self, tk_root):
        tab, cb = _make_send_tab(tk_root)
        assert cb.root is tk_root

    def test_cb_config_roundtrip(self, tk_root):
        tab, cb = _make_send_tab(tk_root, config_dict={"key": "original"})
        cb.save_config({"key": "updated"})
        assert cb.config()["key"] == "updated"

    def test_cb_weekday_names(self, tk_root):
        tab, cb = _make_send_tab(tk_root)
        names = cb.get_weekday_names()
        assert len(names) == 7
        assert names[0] == "周一"