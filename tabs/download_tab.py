import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from config_manager import save_config
from tabs.base_tab import BaseTab, TabCallbacks


class DownloadTab(BaseTab):
    WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    def __init__(self, parent: tk.Widget, callbacks: TabCallbacks) -> None:
        super().__init__(parent, callbacks)
        self._init_vars()
        self.build_ui()
        self._load_config_to_ui()

    def _init_vars(self) -> None:
        self.var_skip_ssl = tk.BooleanVar()
        self.var_dl_enabled = tk.BooleanVar(value=True)
        self.day_vars: list[tk.BooleanVar] = [tk.BooleanVar() for _ in range(7)]
        self.dl_email_day_vars: list[tk.BooleanVar] = [tk.BooleanVar() for _ in range(7)]
        self.var_dl_email_time = tk.BooleanVar()
        self.var_dl_email_date = tk.BooleanVar()

    def build_ui(self) -> None:
        dl_inner = ttk.Frame(self.frame)
        dl_inner.pack(fill=tk.BOTH, expand=True)
        dl_inner.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(dl_inner, text="IMAP服务器:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.entry_server = ttk.Entry(dl_inner, width=35)
        self.entry_server.grid(row=row, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        ttk.Label(dl_inner, text="(如: imap.qq.com / imap.163.com)", foreground="gray").grid(
            row=row, column=2, sticky=tk.W, pady=2, padx=5)
        row += 1

        ttk.Label(dl_inner, text="IMAP端口:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.entry_port = ttk.Entry(dl_inner, width=10)
        self.entry_port.grid(row=row, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        ttk.Label(dl_inner, text="(SSL: 993)", foreground="gray").grid(
            row=row, column=2, sticky=tk.W, pady=2, padx=5)
        row += 1

        ttk.Label(dl_inner, text="邮箱账号:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.entry_user = ttk.Entry(dl_inner, width=35)
        self.entry_user.grid(row=row, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        row += 1

        ttk.Label(dl_inner, text="密码/授权码:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.entry_pass = ttk.Entry(dl_inner, width=35, show="*")
        self.entry_pass.grid(row=row, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        row += 1

        self.cb_skip_ssl = ttk.Checkbutton(dl_inner, text="跳过SSL证书验证（内网自签名/Coremail须勾选）",
                                           variable=self.var_skip_ssl)
        self.cb_skip_ssl.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=2)
        row += 1

        ttk.Separator(dl_inner, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=3,
                                                            sticky=tk.EW, pady=8)
        row += 1

        ttk.Label(dl_inner, text="发件人筛选:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.entry_sender = ttk.Entry(dl_inner, width=35)
        self.entry_sender.grid(row=row, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        ttk.Label(dl_inner, text="(英文逗号分隔，支持邮箱或姓名模糊匹配)", foreground="gray").grid(
            row=row, column=2, sticky=tk.W, pady=2, padx=5)
        row += 1

        ttk.Label(dl_inner, text="主题关键词:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.entry_keyword = ttk.Entry(dl_inner, width=35)
        self.entry_keyword.grid(row=row, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        ttk.Label(dl_inner, text="(筛选主题含此关键词的邮件)", foreground="gray").grid(
            row=row, column=2, sticky=tk.W, pady=2, padx=5)
        row += 1

        ttk.Label(dl_inner, text="已读/未读:").grid(row=row, column=0, sticky=tk.W, pady=2)
        self.combo_read_status = ttk.Combobox(dl_inner, width=12, state="readonly",
                                              values=["全部邮件", "仅未读", "仅已读"])
        self.combo_read_status.grid(row=row, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        self.combo_read_status.set("全部邮件")
        ttk.Label(dl_inner, text="(筛选已读/未读状态的邮件)", foreground="gray").grid(
            row=row, column=2, sticky=tk.W, pady=2, padx=5)
        row += 1

        ttk.Label(dl_inner, text="保存目录:").grid(row=row, column=0, sticky=tk.W, pady=2)
        folder_frame = ttk.Frame(dl_inner)
        folder_frame.grid(row=row, column=1, columnspan=2, sticky=tk.EW, pady=2, padx=(5, 0))
        self.entry_folder = ttk.Entry(folder_frame, width=30)
        self.entry_folder.pack(side=tk.LEFT)
        ttk.Button(folder_frame, text="浏览...", command=self._on_browse_folder_btn,
                   width=8).pack(side=tk.LEFT, padx=5)
        row += 1

        ttk.Separator(dl_inner, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=3,
                                                            sticky=tk.EW, pady=8)
        row += 1

        ttk.Label(dl_inner, text="定时设置", font=("", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky=tk.W)
        row += 1

        self.cb_dl_enabled = ttk.Checkbutton(dl_inner, text="启用定时下载", variable=self.var_dl_enabled)
        self.cb_dl_enabled.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=2)
        row += 1

        dl_day_frame = ttk.Frame(dl_inner)
        dl_day_frame.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=2)
        for i, name in enumerate(self.WEEKDAY_NAMES):
            var = self.day_vars[i]
            ttk.Checkbutton(dl_day_frame, text=name, variable=var).pack(side=tk.LEFT, padx=4)
        row += 1

        dl_time_frame = ttk.Frame(dl_inner)
        dl_time_frame.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=2)
        ttk.Label(dl_time_frame, text="时").pack(side=tk.LEFT)
        self.spin_hour = ttk.Spinbox(dl_time_frame, from_=0, to=23, width=4, justify=tk.CENTER)
        self.spin_hour.pack(side=tk.LEFT, padx=(2, 5))
        self.spin_hour.set("9")
        ttk.Label(dl_time_frame, text="分").pack(side=tk.LEFT)
        self.spin_min = ttk.Spinbox(dl_time_frame, from_=0, to=59, width=4, justify=tk.CENTER)
        self.spin_min.pack(side=tk.LEFT, padx=(2, 5))
        self.spin_min.set("0")
        ttk.Label(dl_time_frame, text="秒").pack(side=tk.LEFT)
        self.spin_sec = ttk.Spinbox(dl_time_frame, from_=0, to=59, width=4, justify=tk.CENTER)
        self.spin_sec.pack(side=tk.LEFT, padx=(2, 5))
        self.spin_sec.set("0")
        row += 1

        ttk.Separator(dl_inner, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=3,
                                                            sticky=tk.EW, pady=8)
        row += 1
        ttk.Label(dl_inner, text="邮件筛选（可选：仅下载符合条件的邮件）",
                  font=("", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky=tk.W)
        row += 1

        ttk.Label(dl_inner, text="接收日:").grid(row=row, column=0, sticky=tk.W, pady=2)
        dl_email_day_frame = ttk.Frame(dl_inner)
        dl_email_day_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=2)
        for i, name in enumerate(self.WEEKDAY_NAMES):
            var = self.dl_email_day_vars[i]
            ttk.Checkbutton(dl_email_day_frame, text=name, variable=var).pack(side=tk.LEFT, padx=3)
        ttk.Label(dl_email_day_frame, text=" (空=不限)", foreground="gray").pack(side=tk.LEFT)
        row += 1

        ttk.Label(dl_inner, text="接收时间段:").grid(row=row, column=0, sticky=tk.W, pady=2)
        dl_email_time_frame = ttk.Frame(dl_inner)
        dl_email_time_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=2)
        ttk.Checkbutton(dl_email_time_frame, text="启用",
                        variable=self.var_dl_email_time).pack(side=tk.LEFT, padx=(0, 5))
        self.spin_dl_email_h1 = ttk.Spinbox(dl_email_time_frame, from_=0, to=23, width=3, justify=tk.CENTER)
        self.spin_dl_email_h1.pack(side=tk.LEFT)
        self.spin_dl_email_h1.set("0")
        ttk.Label(dl_email_time_frame, text=":").pack(side=tk.LEFT)
        self.spin_dl_email_m1 = ttk.Spinbox(dl_email_time_frame, from_=0, to=59, width=3, justify=tk.CENTER)
        self.spin_dl_email_m1.pack(side=tk.LEFT)
        self.spin_dl_email_m1.set("0")
        ttk.Label(dl_email_time_frame, text=" ~ ").pack(side=tk.LEFT)
        self.spin_dl_email_h2 = ttk.Spinbox(dl_email_time_frame, from_=0, to=23, width=3, justify=tk.CENTER)
        self.spin_dl_email_h2.pack(side=tk.LEFT)
        self.spin_dl_email_h2.set("23")
        ttk.Label(dl_email_time_frame, text=":").pack(side=tk.LEFT)
        self.spin_dl_email_m2 = ttk.Spinbox(dl_email_time_frame, from_=0, to=59, width=3, justify=tk.CENTER)
        self.spin_dl_email_m2.pack(side=tk.LEFT)
        self.spin_dl_email_m2.set("59")
        row += 1

        ttk.Label(dl_inner, text="接收日期范围:").grid(row=row, column=0, sticky=tk.W, pady=2)
        dl_email_date_frame = ttk.Frame(dl_inner)
        dl_email_date_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=2)
        ttk.Checkbutton(dl_email_date_frame, text="启用",
                        variable=self.var_dl_email_date).pack(side=tk.LEFT, padx=(0, 5))
        self.entry_dl_email_date1 = ttk.Entry(dl_email_date_frame, width=11)
        self.entry_dl_email_date1.pack(side=tk.LEFT)
        ttk.Label(dl_email_date_frame, text=" ~ ").pack(side=tk.LEFT)
        self.entry_dl_email_date2 = ttk.Entry(dl_email_date_frame, width=11)
        self.entry_dl_email_date2.pack(side=tk.LEFT)
        ttk.Label(dl_email_date_frame, text=" (YYYY-MM-DD)", foreground="gray").pack(side=tk.LEFT)
        row += 1

        dl_btn_frame = ttk.Frame(dl_inner)
        dl_btn_frame.grid(row=row, column=0, columnspan=3, pady=8)
        ttk.Button(dl_btn_frame, text="立即执行一次", command=self._test_and_run,
                   width=14).pack(side=tk.LEFT, padx=3)
        ttk.Button(dl_btn_frame, text="测试IMAP连接", command=self._test_imap_btn,
                   width=14).pack(side=tk.LEFT, padx=3)
        ttk.Button(dl_btn_frame, text="保存配置", command=self._save_download_config,
                   width=10).pack(side=tk.LEFT, padx=3)
        ttk.Button(dl_btn_frame, text="清除配置", command=self._clear_download_config,
                   width=10).pack(side=tk.LEFT, padx=3)

    def _load_config_to_ui(self) -> None:
        cfg = self.cb.config()
        self.entry_server.delete(0, tk.END)
        self.entry_server.insert(0, cfg.get("imap_server", ""))
        self.entry_port.delete(0, tk.END)
        self.entry_port.insert(0, str(cfg.get("imap_port", 993)))
        self.entry_user.delete(0, tk.END)
        self.entry_user.insert(0, cfg.get("email_user", ""))
        self.entry_pass.delete(0, tk.END)
        self.entry_pass.insert(0, cfg.get("email_pass", ""))
        filter_list = cfg.get("sender_filter_list", [])
        if not filter_list and cfg.get("sender_filter"):
            filter_list = [cfg["sender_filter"]]
        self.entry_sender.delete(0, tk.END)
        self.entry_sender.insert(0, ", ".join(filter_list))
        self.entry_keyword.delete(0, tk.END)
        self.entry_keyword.insert(0, cfg.get("download_keyword_filter", ""))
        rs = cfg.get("download_read_status", "all")
        rs_map = {"all": "全部邮件", "unseen": "仅未读", "seen": "仅已读"}
        self.combo_read_status.set(rs_map.get(rs, "全部邮件"))
        self.entry_folder.delete(0, tk.END)
        self.entry_folder.insert(0, cfg.get("save_folder", ""))
        self.var_skip_ssl.set(cfg.get("skip_ssl_verify", False))

        dl = cfg.get("schedule_download", {})
        self.var_dl_enabled.set(dl.get("enabled", False))
        days = dl.get("days", [])
        for i in range(7):
            self.day_vars[i].set((i + 1) in days)
        self.spin_hour.set(str(dl.get("hour", 9)))
        self.spin_min.set(str(dl.get("minute", 0)))
        self.spin_sec.set(str(dl.get("second", 0)))

        filter_days = cfg.get("download_filter_days", [])
        for i in range(7):
            self.dl_email_day_vars[i].set((i + 1) in filter_days)
        self.var_dl_email_time.set(cfg.get("download_filter_time_enabled", False))
        t_start = cfg.get("download_filter_time_start", "00:00")
        t_end = cfg.get("download_filter_time_end", "23:59")
        self.spin_dl_email_h1.set(t_start.split(":")[0])
        self.spin_dl_email_m1.set(t_start.split(":")[1])
        self.spin_dl_email_h2.set(t_end.split(":")[0])
        self.spin_dl_email_m2.set(t_end.split(":")[1])
        self.var_dl_email_date.set(cfg.get("download_filter_date_enabled", False))
        self.entry_dl_email_date1.delete(0, tk.END)
        self.entry_dl_email_date1.insert(0, cfg.get("download_filter_date_start", ""))
        self.entry_dl_email_date2.delete(0, tk.END)
        self.entry_dl_email_date2.insert(0, cfg.get("download_filter_date_end", ""))

    def _save_download_config(self) -> None:
        dl_days = [i + 1 for i, var in enumerate(self.day_vars) if var.get()]
        cfg = self.cb.config()
        cfg.update({
            "imap_server": self.entry_server.get().strip(),
            "imap_port": int(self.entry_port.get().strip() or "993"),
            "email_user": self.entry_user.get().strip(),
            "email_pass": self.entry_pass.get().strip(),
            "sender_filter": "",
            "sender_filter_list": [
                s.strip() for s in self.entry_sender.get().split(",") if s.strip()
            ],
            "download_keyword_filter": self.entry_keyword.get().strip(),
            "download_read_status": {
                "全部邮件": "all", "仅未读": "unseen", "仅已读": "seen"
            }.get(self.combo_read_status.get(), "all"),
            "save_folder": self.entry_folder.get().strip(),
            "skip_ssl_verify": self.var_skip_ssl.get(),
            "schedule_download": {
                "days": dl_days,
                "hour": int(self.spin_hour.get()),
                "minute": int(self.spin_min.get()),
                "second": int(self.spin_sec.get()),
                "enabled": self.var_dl_enabled.get(),
            },
            "download_filter_days": [i + 1 for i, var in enumerate(self.dl_email_day_vars) if var.get()],
            "download_filter_time_enabled": self.var_dl_email_time.get(),
            "download_filter_time_start": f"{int(self.spin_dl_email_h1.get()):02d}:{int(self.spin_dl_email_m1.get()):02d}",
            "download_filter_time_end": f"{int(self.spin_dl_email_h2.get()):02d}:{int(self.spin_dl_email_m2.get()):02d}",
            "download_filter_date_enabled": self.var_dl_email_date.get(),
            "download_filter_date_start": self.entry_dl_email_date1.get().strip(),
            "download_filter_date_end": self.entry_dl_email_date2.get().strip(),
        })
        self.cb.save_config(cfg)
        save_config(cfg)
        self.cb.log("下载配置已保存", "download")

    def _clear_download_config(self) -> None:
        self.entry_server.delete(0, tk.END)
        self.entry_port.delete(0, tk.END)
        self.entry_port.insert(0, "993")
        self.entry_user.delete(0, tk.END)
        self.entry_pass.delete(0, tk.END)
        self.entry_sender.delete(0, tk.END)
        self.entry_keyword.delete(0, tk.END)
        self.combo_read_status.set("全部邮件")
        self.entry_folder.delete(0, tk.END)
        self.var_skip_ssl.set(False)
        self.var_dl_enabled.set(False)
        for var in self.day_vars:
            var.set(False)
        self.spin_hour.set("9")
        self.spin_min.set("0")
        self.spin_sec.set("0")
        for var in self.dl_email_day_vars:
            var.set(False)
        self.var_dl_email_time.set(False)
        self.spin_dl_email_h1.set("0")
        self.spin_dl_email_m1.set("0")
        self.spin_dl_email_h2.set("23")
        self.spin_dl_email_m2.set("59")
        self.var_dl_email_date.set(False)
        self.entry_dl_email_date1.delete(0, tk.END)
        self.entry_dl_email_date2.delete(0, tk.END)
        self._save_download_config()
        self.cb.log("下载配置已清除", "download")

    def _on_browse_folder_btn(self) -> None:
        path = filedialog.askdirectory(title="选择附件保存目录")
        if path:
            self.entry_folder.delete(0, tk.END)
            self.entry_folder.insert(0, path)

    def _test_imap_btn(self) -> None:
        self._save_download_config()
        cfg = self.cb.config()
        if not cfg["email_user"] or not cfg["email_pass"]:
            messagebox.showerror("错误", "请先填写邮箱账号和密码/授权码")
            return

        self.cb.log("正在测试IMAP连接...", "download")

        def on_complete(_result):
            self.cb.log("IMAP连接测试成功！", "download")
            messagebox.showinfo("连接成功", "IMAP服务器连接成功！")

        def on_error(exc):
            self.cb.log(f"IMAP连接测试失败: {exc}", "download")
            messagebox.showerror("连接失败", f"IMAP连接失败:\n{exc}")

        self.cb.coordinator.test_imap(on_complete=on_complete, on_error=on_error)

    def _test_and_run(self) -> None:
        self._save_download_config()
        cfg = self.cb.config()
        if not cfg["email_user"] or not cfg["email_pass"]:
            messagebox.showerror("错误", "请先填写邮箱账号和密码/授权码")
            return
        filter_list = cfg.get("sender_filter_list", [])
        keyword_filter = cfg.get("download_keyword_filter", "").strip()
        if not filter_list and not keyword_filter:
            messagebox.showerror("错误", "请填写发件人筛选或主题关键词（至少填一个）")
            return
        if not cfg["save_folder"]:
            messagebox.showerror("错误", "请选择附件保存目录")
            return

        os.makedirs(cfg["save_folder"], exist_ok=True)

        self.cb.log("=" * 50, "download")
        self.cb.log("开始执行...", "download")

        def on_complete(count):
            self.cb.log(f"本次下载了 {count} 个附件", "download")
            if count == 0:
                self.cb.log("没有新的匹配附件", "download")
            self.cb.log("=" * 50, "download")
            messagebox.showinfo("下载完成", f"成功下载 {count} 个附件")

        def on_error(exc):
            self.cb.log(f"错误: {exc}", "download")
            messagebox.showerror("执行出错", str(exc))
            self.cb.log("=" * 50, "download")

        self.cb.coordinator.run_download_once(on_complete=on_complete, on_error=on_error)