import tkinter as tk
from unittest.mock import MagicMock

import pytest

from tabs.base_tab import TabCallbacks
from tabs.download_tab import DownloadTab


def _make_tab_callbacks(root=None, config_dict=None, coordinator=None):
    if root is None:
        root = tk.Tk()
    if config_dict is None:
        config_dict = {
            "imap_server": "imap.test.com",
            "imap_port": 993,
            "email_user": "test@test.com",
            "email_pass": "secret",
            "sender_filter": "",
            "sender_filter_list": ["boss@corp.com"],
            "download_keyword_filter": "report",
            "download_read_status": "unseen",
            "save_folder": "C:/downloads",
            "skip_ssl_verify": True,
            "schedule_download": {
                "days": [1, 3, 5],
                "hour": 10,
                "minute": 30,
                "second": 0,
                "enabled": True,
            },
            "download_filter_days": [1, 2],
            "download_filter_time_enabled": True,
            "download_filter_time_start": "08:00",
            "download_filter_time_end": "18:00",
            "download_filter_date_enabled": False,
            "download_filter_date_start": "",
            "download_filter_date_end": "",
        }
    if coordinator is None:
        coordinator = MagicMock()

    config_store = dict(config_dict)
    log_messages = []

    def config_getter():
        return config_store.copy()

    def config_setter(cfg):
        config_store.update(cfg)

    def logger(msg, category="system"):
        log_messages.append((msg, category))

    def record_download(record):
        pass

    def get_weekday_names():
        return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    return TabCallbacks(
        root,
        config_getter,
        config_setter,
        logger,
        record_download,
        coordinator,
        get_weekday_names,
    ), log_messages, config_store, coordinator


class TestDownloadTab:
    def test_tab_creation(self):
        root = tk.Tk()
        cb, _, _, _ = _make_tab_callbacks(root=root)
        parent = tk.Tk()
        tab = DownloadTab(parent, cb)
        assert tab is not None
        assert isinstance(tab, DownloadTab)
        assert tab.WEEKDAY_NAMES == ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        parent.destroy()
        root.destroy()

    def test_vars_initialized(self):
        parent = tk.Tk()
        cb, _, _, _ = _make_tab_callbacks()
        tab = DownloadTab(parent, cb)
        assert len(tab.day_vars) == 7
        assert len(tab.dl_email_day_vars) == 7
        assert isinstance(tab.var_skip_ssl, tk.BooleanVar)
        assert isinstance(tab.var_dl_enabled, tk.BooleanVar)
        assert isinstance(tab.var_dl_email_time, tk.BooleanVar)
        assert isinstance(tab.var_dl_email_date, tk.BooleanVar)
        parent.destroy()

    def test_widgets_exist(self):
        parent = tk.Tk()
        cb, _, _, _ = _make_tab_callbacks()
        tab = DownloadTab(parent, cb)
        assert tab.entry_server is not None
        assert tab.entry_port is not None
        assert tab.entry_user is not None
        assert tab.entry_pass is not None
        assert tab.entry_sender is not None
        assert tab.entry_keyword is not None
        assert tab.combo_read_status is not None
        assert tab.entry_folder is not None
        assert tab.spin_hour is not None
        assert tab.spin_min is not None
        assert tab.spin_sec is not None
        assert tab.spin_dl_email_h1 is not None
        assert tab.spin_dl_email_m1 is not None
        assert tab.spin_dl_email_h2 is not None
        assert tab.spin_dl_email_m2 is not None
        assert tab.entry_dl_email_date1 is not None
        assert tab.entry_dl_email_date2 is not None
        parent.destroy()

    def test_load_config_to_ui(self):
        parent = tk.Tk()
        cb, _, _, _ = _make_tab_callbacks()
        tab = DownloadTab(parent, cb)
        assert tab.entry_server.get() == "imap.test.com"
        assert tab.entry_port.get() == "993"
        assert tab.entry_user.get() == "test@test.com"
        assert tab.entry_pass.get() == "secret"
        assert tab.entry_sender.get() == "boss@corp.com"
        assert tab.entry_keyword.get() == "report"
        assert tab.combo_read_status.get() == "仅未读"
        assert tab.entry_folder.get() == "C:/downloads"
        assert tab.var_skip_ssl.get() is True
        assert tab.var_dl_enabled.get() is True
        assert tab.var_dl_email_time.get() is True
        assert tab.var_dl_email_date.get() is False
        assert tab.spin_hour.get() == "10"
        assert tab.spin_min.get() == "30"
        assert tab.spin_sec.get() == "0"
        assert tab.spin_dl_email_h1.get() == "08"
        assert tab.spin_dl_email_m1.get() == "00"
        assert tab.spin_dl_email_h2.get() == "18"
        assert tab.spin_dl_email_m2.get() == "00"
        parent.destroy()

    def test_load_config_to_ui_day_vars(self):
        parent = tk.Tk()
        cb, _, _, _ = _make_tab_callbacks()
        tab = DownloadTab(parent, cb)
        assert tab.day_vars[0].get() is True
        assert tab.day_vars[1].get() is False
        assert tab.day_vars[2].get() is True
        assert tab.day_vars[3].get() is False
        assert tab.day_vars[4].get() is True
        assert tab.day_vars[5].get() is False
        assert tab.day_vars[6].get() is False
        assert tab.dl_email_day_vars[0].get() is True
        assert tab.dl_email_day_vars[1].get() is True
        parent.destroy()

    def test_save_download_config(self):
        parent = tk.Tk()
        cb, logs, store, _ = _make_tab_callbacks()
        tab = DownloadTab(parent, cb)
        tab.entry_server.delete(0, tk.END)
        tab.entry_server.insert(0, "new.server.com")
        tab.entry_port.delete(0, tk.END)
        tab.entry_port.insert(0, "143")
        tab._save_download_config()
        cfg = store
        assert cfg["imap_server"] == "new.server.com"
        assert cfg["imap_port"] == 143
        assert logs[-1] == ("下载配置已保存", "download")
        parent.destroy()

    def test_save_download_config_filters(self):
        parent = tk.Tk()
        cb, _, store, _ = _make_tab_callbacks()
        tab = DownloadTab(parent, cb)
        tab.entry_sender.delete(0, tk.END)
        tab.entry_sender.insert(0, "hr@corp.com, ceo@corp.com")
        tab.entry_keyword.delete(0, tk.END)
        tab.entry_keyword.insert(0, "invoice")
        tab.combo_read_status.set("仅已读")
        tab._save_download_config()
        cfg = store
        assert cfg["sender_filter_list"] == ["hr@corp.com", "ceo@corp.com"]
        assert cfg["download_keyword_filter"] == "invoice"
        assert cfg["download_read_status"] == "seen"
        parent.destroy()

    def test_save_download_config_schedule(self):
        parent = tk.Tk()
        cb, _, store, _ = _make_tab_callbacks()
        tab = DownloadTab(parent, cb)
        tab.var_dl_enabled.set(False)
        tab.spin_hour.set("15")
        tab.spin_min.set("45")
        tab.spin_sec.set("30")
        tab._save_download_config()
        cfg = store
        schedule = cfg["schedule_download"]
        assert schedule["enabled"] is False
        assert schedule["hour"] == 15
        assert schedule["minute"] == 45
        assert schedule["second"] == 30
        parent.destroy()

    def test_clear_download_config(self):
        parent = tk.Tk()
        cb, logs, _, _ = _make_tab_callbacks()
        tab = DownloadTab(parent, cb)
        tab._clear_download_config()
        assert tab.entry_server.get() == ""
        assert tab.entry_port.get() == "993"
        assert tab.entry_user.get() == ""
        assert tab.entry_pass.get() == ""
        assert tab.entry_sender.get() == ""
        assert tab.entry_keyword.get() == ""
        assert tab.combo_read_status.get() == "全部邮件"
        assert tab.entry_folder.get() == ""
        assert tab.var_skip_ssl.get() is False
        assert tab.var_dl_enabled.get() is False
        assert tab.var_dl_email_time.get() is False
        assert tab.var_dl_email_date.get() is False
        assert tab.spin_hour.get() == "9"
        assert tab.spin_min.get() == "0"
        assert tab.spin_sec.get() == "0"
        for var in tab.day_vars:
            assert var.get() is False
        for var in tab.dl_email_day_vars:
            assert var.get() is False
        assert logs[-1] == ("下载配置已清除", "download")
        parent.destroy()

    def test_clear_download_config_time_defaults(self):
        parent = tk.Tk()
        cb, _, _, _ = _make_tab_callbacks()
        tab = DownloadTab(parent, cb)
        tab._clear_download_config()
        assert tab.spin_dl_email_h1.get() == "0"
        assert tab.spin_dl_email_m1.get() == "0"
        assert tab.spin_dl_email_h2.get() == "23"
        assert tab.spin_dl_email_m2.get() == "59"
        parent.destroy()

    def test_config_read_status_mapping(self):
        parent = tk.Tk()
        cb, _, store, _ = _make_tab_callbacks()
        tab = DownloadTab(parent, cb)
        tab.combo_read_status.set("仅已读")
        tab._save_download_config()
        assert store["download_read_status"] == "seen"
        tab.combo_read_status.set("仅未读")
        tab._save_download_config()
        assert store["download_read_status"] == "unseen"
        tab.combo_read_status.set("全部邮件")
        tab._save_download_config()
        assert store["download_read_status"] == "all"
        parent.destroy()

    def test_save_download_config_date_filter(self):
        parent = tk.Tk()
        cb, _, store, _ = _make_tab_callbacks()
        tab = DownloadTab(parent, cb)
        tab.var_dl_email_date.set(True)
        tab.entry_dl_email_date1.delete(0, tk.END)
        tab.entry_dl_email_date1.insert(0, "2026-01-01")
        tab.entry_dl_email_date2.delete(0, tk.END)
        tab.entry_dl_email_date2.insert(0, "2026-06-01")
        tab._save_download_config()
        cfg = store
        assert cfg["download_filter_date_enabled"] is True
        assert cfg["download_filter_date_start"] == "2026-01-01"
        assert cfg["download_filter_date_end"] == "2026-06-01"
        parent.destroy()

    def test_test_imap_btn_triggers_coordinator(self):
        parent = tk.Tk()
        coordinator = MagicMock()
        cb, _, _, coordinator = _make_tab_callbacks(coordinator=coordinator)
        tab = DownloadTab(parent, cb)
        tab._test_imap_btn()
        coordinator.test_imap.assert_called_once()
        parent.destroy()

    def test_test_and_run_triggers_coordinator(self):
        parent = tk.Tk()
        coordinator = MagicMock()
        cb, _, _, coordinator = _make_tab_callbacks(coordinator=coordinator)
        tab = DownloadTab(parent, cb)
        tab._test_and_run()
        coordinator.run_download_once.assert_called_once()
        parent.destroy()