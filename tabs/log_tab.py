"""Log tab providing real-time log display, filtering, and scheduled export."""

from __future__ import annotations

import datetime
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from typing import Any

from config_manager import CONFIG_FILE, load_persisted_log, persist_log_entries, MAX_HISTORY_RECORDS
from tabs.base_tab import BaseTab, TabCallbacks

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
LOG_CATEGORIES = {"download": "[下载]", "send": "[发送]", "system": "[系统]", "query": "[查询]"}


class LogTab(BaseTab):
    """Tab widget for runtime log display, category filtering, and scheduled log export."""

    def __init__(self, parent: tk.Widget, callbacks: TabCallbacks) -> None:
        super().__init__(parent, callbacks)
        self._log_entries: list[dict[str, str]] = load_persisted_log()
        self._log_filter: str = "all"
        self._log_text: scrolledtext.ScrolledText | None = None
        self._var_export_enabled: tk.BooleanVar | None = None
        self._entry_export_folder: ttk.Entry | None = None
        self._spin_export_hour: ttk.Spinbox | None = None
        self._spin_export_min: ttk.Spinbox | None = None
        self._spin_export_sec: ttk.Spinbox | None = None
        self._export_day_vars: list[tk.BooleanVar] = []
        self.build_ui()
        self._render_persisted_log()
        self.load_config_to_ui()

    def build_ui(self) -> None:
        log_toolbar = ttk.Frame(self.frame)
        log_toolbar.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(log_toolbar, text="筛选:").pack(side=tk.LEFT)
        for label, cat in [("全部", "all"), ("下载", "download"), ("发送", "send"),
                           ("查询", "query"), ("系统", "system")]:
            ttk.Button(log_toolbar, text=label, width=6,
                       command=lambda c=cat: self._apply_log_filter(c)).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_toolbar, text="导出日志", width=10,
                   command=self._export_log).pack(side=tk.RIGHT, padx=2)
        ttk.Button(log_toolbar, text="清空日志", width=10,
                   command=self._clear_log).pack(side=tk.RIGHT, padx=2)

        self._log_text = scrolledtext.ScrolledText(
            self.frame, wrap=tk.WORD, font=("Consolas", 9), state=tk.DISABLED,
        )
        self._log_text.pack(fill=tk.BOTH, expand=True)

        export_frame = ttk.LabelFrame(self.frame, text="定时导出日志", padding=5)
        export_frame.pack(fill=tk.X, pady=(5, 0))

        er1 = ttk.Frame(export_frame)
        er1.pack(fill=tk.X, pady=2)
        self._var_export_enabled = tk.BooleanVar(master=self.frame, value=False)
        ttk.Checkbutton(er1, text="启用定时导出",
                        variable=self._var_export_enabled).pack(side=tk.LEFT)
        ttk.Label(er1, text="导出目录:").pack(side=tk.LEFT, padx=(15, 0))
        self._entry_export_folder = ttk.Entry(er1, width=28)
        self._entry_export_folder.pack(side=tk.LEFT, padx=3)
        ttk.Button(er1, text="浏览...", command=self._browse_export_folder, width=7).pack(side=tk.LEFT)

        er2 = ttk.Frame(export_frame)
        er2.pack(fill=tk.X, pady=2)
        ttk.Label(er2, text="日期:").pack(side=tk.LEFT)
        for name in WEEKDAY_NAMES:
            var = tk.BooleanVar(master=self.frame)
            self._export_day_vars.append(var)
            ttk.Checkbutton(er2, text=name, variable=var).pack(side=tk.LEFT, padx=2)
        ttk.Label(er2, text="  时间: 时").pack(side=tk.LEFT, padx=(10, 0))
        self._spin_export_hour = ttk.Spinbox(er2, from_=0, to=23, width=4, justify=tk.CENTER)
        self._spin_export_hour.pack(side=tk.LEFT, padx=(2, 3)); self._spin_export_hour.set("23")
        ttk.Label(er2, text="分").pack(side=tk.LEFT)
        self._spin_export_min = ttk.Spinbox(er2, from_=0, to=59, width=4, justify=tk.CENTER)
        self._spin_export_min.pack(side=tk.LEFT, padx=(2, 3)); self._spin_export_min.set("59")
        ttk.Label(er2, text="秒").pack(side=tk.LEFT)
        self._spin_export_sec = ttk.Spinbox(er2, from_=0, to=59, width=4, justify=tk.CENTER)
        self._spin_export_sec.pack(side=tk.LEFT, padx=(2, 3)); self._spin_export_sec.set("0")

        er3 = ttk.Frame(export_frame)
        er3.pack(fill=tk.X, pady=(2, 0))
        ttk.Button(er3, text="保存设置", command=self._save_export_config).pack(side=tk.RIGHT)

    def load_config_to_ui(self) -> None:
        """Populate export settings widgets from the current config."""
        cfg = self.cb.config()
        self._var_export_enabled.set(cfg.get("log_export_enabled", False))
        self._entry_export_folder.delete(0, tk.END)
        self._entry_export_folder.insert(0, cfg.get("log_export_folder", ""))
        exp_days = cfg.get("log_export_days", [])
        for i in range(7):
            self._export_day_vars[i].set((i + 1) in exp_days)
        self._spin_export_hour.set(str(cfg.get("log_export_hour", 23)))
        self._spin_export_min.set(str(cfg.get("log_export_minute", 59)))
        self._spin_export_sec.set(str(cfg.get("log_export_second", 0)))

    def collect_export_config(self, target: dict[str, Any]) -> None:
        """Merge log-export settings into the supplied config dict."""
        exp_days = [i + 1 for i, var in enumerate(self._export_day_vars) if var.get()]
        target.update({
            "log_export_enabled": self._var_export_enabled.get(),
            "log_export_folder": self._entry_export_folder.get().strip(),
            "log_export_days": exp_days,
            "log_export_hour": int(self._spin_export_hour.get()),
            "log_export_minute": int(self._spin_export_min.get()),
            "log_export_second": int(self._spin_export_sec.get()),
        })

    def _save_export_config(self) -> None:
        cfg = self.cb.config()
        self.collect_export_config(cfg)
        self.cb.save_config(cfg)
        self.cb.log("日志导出设置已保存", "system")

    def _format_line(self, entry: dict[str, str]) -> str:
        cat = entry["cat"]
        if cat == "system":
            return entry["msg"]
        prefix = LOG_CATEGORIES.get(cat, "[系统]")
        return f"{prefix} {entry['time']}  {entry['msg']}"

    def add_log(self, msg: str, category: str = "system") -> None:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {"time": timestamp, "cat": category, "msg": str(msg)}
        self._log_entries.append(entry)
        if len(self._log_entries) > MAX_HISTORY_RECORDS:
            self._log_entries = self._log_entries[-MAX_HISTORY_RECORDS:]
        persist_log_entries(self._log_entries)
        if self._log_filter == "all" or self._log_filter == category:
            self._render_log_line(self._format_line(entry))

    def persist(self) -> None:
        persist_log_entries(self._log_entries)

    def _render_log_line(self, line: str) -> None:
        self._log_text.config(state=tk.NORMAL)
        self._log_text.insert(tk.END, line + "\n")
        self._log_text.see(tk.END)
        self._log_text.config(state=tk.DISABLED)

    def _render_persisted_log(self) -> None:
        for entry in self._log_entries:
            self._render_log_line(self._format_line(entry))

    def _clear_log(self) -> None:
        self._log_text.config(state=tk.NORMAL)
        self._log_text.delete(1.0, tk.END)
        self._log_text.config(state=tk.DISABLED)
        self._log_entries.clear()
        persist_log_entries([])

    def _apply_log_filter(self, category: str) -> None:
        self._log_filter = category
        self._log_text.config(state=tk.NORMAL)
        self._log_text.delete(1.0, tk.END)
        self._log_text.config(state=tk.DISABLED)
        for entry in self._log_entries:
            if category == "all" or entry["cat"] == category:
                self._render_log_line(self._format_line(entry))

    def _write_entries_to_file(self, entries: list[dict[str, str]], path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(self._format_line(entry) + "\n")

    def _export_log(self) -> None:
        entries = self._log_entries
        if self._log_filter != "all":
            entries = [e for e in self._log_entries if e["cat"] == self._log_filter]
        if not entries:
            messagebox.showinfo("提示", "当前筛选条件下无日志可导出")
            return
        path = filedialog.asksaveasfilename(
            title="导出日志", defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile=f"邮件工具日志_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        )
        if not path:
            return
        self._write_entries_to_file(entries, path)
        self.cb.log(f"日志已导出 -> {path}")
        messagebox.showinfo("导出完成", f"日志已保存到:\n{path}")

    def auto_export_log(self) -> None:
        cfg = self.cb.config()
        folder = cfg.get("log_export_folder", "")
        if not folder:
            folder = os.path.dirname(CONFIG_FILE)
        os.makedirs(folder, exist_ok=True)
        save_path = os.path.join(
            folder, f"邮件工具日志_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        )
        self._write_entries_to_file(self._log_entries, save_path)
        self.cb.log(f"日志已定时导出 -> {save_path}")

    def _browse_export_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择日志导出目录")
        if folder:
            self._entry_export_folder.delete(0, tk.END)
            self._entry_export_folder.insert(0, folder)