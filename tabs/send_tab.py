import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from tabs.base_tab import BaseTab, TabCallbacks


class SendTab(BaseTab):
    WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    def __init__(self, parent, callbacks: TabCallbacks):
        super().__init__(parent, callbacks)
        self._init_vars()
        self.build_ui()
        self._load_config_to_ui()

    def _init_vars(self):
        self.var_smtp_ssl = tk.BooleanVar(value=True)
        self.var_skip_ssl_smtp = tk.BooleanVar(value=False)
        self.var_att_mode = tk.StringVar(value="single")
        self.var_sd_enabled = tk.BooleanVar(value=False)
        self.send_day_vars = [tk.BooleanVar() for _ in range(7)]

    def build_ui(self):
        send_inner = self.frame
        send_inner.columnconfigure(1, weight=1)

        srow = 0
        ttk.Label(send_inner, text="SMTP服务器:").grid(row=srow, column=0, sticky=tk.W, pady=2)
        self.entry_smtp_server = ttk.Entry(send_inner, width=35)
        self.entry_smtp_server.grid(row=srow, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        ttk.Label(send_inner, text="(如: smtp.qq.com / smtp.163.com)", foreground="gray").grid(
            row=srow, column=2, sticky=tk.W, pady=2, padx=5)
        srow += 1

        ttk.Label(send_inner, text="SMTP端口:").grid(row=srow, column=0, sticky=tk.W, pady=2)
        self.entry_smtp_port = ttk.Entry(send_inner, width=10)
        self.entry_smtp_port.grid(row=srow, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        ttk.Checkbutton(send_inner, text="使用SSL", variable=self.var_smtp_ssl).grid(
            row=srow, column=2, sticky=tk.W, pady=2, padx=5)
        srow += 1

        ttk.Checkbutton(send_inner, text="跳过SSL证书验证（内网自签名证书须勾选）",
                        variable=self.var_skip_ssl_smtp).grid(
            row=srow, column=0, columnspan=3, sticky=tk.W, pady=2)
        srow += 1

        ttk.Label(send_inner, text="发件人账号:").grid(row=srow, column=0, sticky=tk.W, pady=2)
        self.entry_send_user = ttk.Entry(send_inner, width=35)
        self.entry_send_user.grid(row=srow, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        ttk.Label(send_inner, text="(空则复用下载页邮箱)", foreground="gray").grid(
            row=srow, column=2, sticky=tk.W, pady=2, padx=5)
        srow += 1

        ttk.Label(send_inner, text="发件人密码:").grid(row=srow, column=0, sticky=tk.W, pady=2)
        self.entry_send_pass = ttk.Entry(send_inner, width=35, show="*")
        self.entry_send_pass.grid(row=srow, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        ttk.Label(send_inner, text="(空则复用下载页密码)", foreground="gray").grid(
            row=srow, column=2, sticky=tk.W, pady=2, padx=5)
        srow += 1

        ttk.Separator(send_inner, orient=tk.HORIZONTAL).grid(
            row=srow, column=0, columnspan=3, sticky=tk.EW, pady=8)
        srow += 1

        ttk.Label(send_inner, text="收件人:").grid(row=srow, column=0, sticky=tk.W, pady=2)
        self.entry_send_to = ttk.Entry(send_inner, width=35)
        self.entry_send_to.grid(row=srow, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        ttk.Label(send_inner, text="(多人用英文逗号分隔)", foreground="gray").grid(
            row=srow, column=2, sticky=tk.W, pady=2, padx=5)
        srow += 1

        ttk.Label(send_inner, text="邮件主题:").grid(row=srow, column=0, sticky=tk.W, pady=2)
        self.entry_send_subject = ttk.Entry(send_inner, width=35)
        self.entry_send_subject.grid(row=srow, column=1, sticky=tk.W, pady=2, padx=(5, 0))
        srow += 1

        ttk.Label(send_inner, text="邮件正文:").grid(row=srow, column=0, sticky=tk.NW, pady=2)
        body_frame = ttk.Frame(send_inner)
        body_frame.grid(row=srow, column=1, columnspan=2, sticky=tk.EW, pady=2, padx=(5, 0))
        self.text_send_body = tk.Text(body_frame, width=35, height=5, wrap=tk.WORD, font=("", 9))
        self.text_send_body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        body_scroll = ttk.Scrollbar(body_frame, command=self.text_send_body.yview)
        body_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text_send_body.config(yscrollcommand=body_scroll.set)
        srow += 1

        ttk.Label(send_inner, text="附件:").grid(row=srow, column=0, sticky=tk.NW, pady=2)
        att_mode_frame = ttk.Frame(send_inner)
        att_mode_frame.grid(row=srow, column=1, columnspan=2, sticky=tk.W, pady=2, padx=(5, 0))
        ttk.Radiobutton(att_mode_frame, text="单文件", variable=self.var_att_mode, value="single", command=self._on_send_attachment_mode_changed).pack(side=tk.LEFT)
        ttk.Radiobutton(att_mode_frame, text="多文件", variable=self.var_att_mode, value="multi", command=self._on_send_attachment_mode_changed).pack(side=tk.LEFT, padx=8)
        ttk.Radiobutton(att_mode_frame, text="文件夹", variable=self.var_att_mode, value="folder", command=self._on_send_attachment_mode_changed).pack(side=tk.LEFT, padx=8)
        srow += 1

        self.entry_send_attachment = ttk.Entry(send_inner, width=35)
        self.entry_send_attachment.grid(row=srow, column=1, sticky=tk.EW, pady=2, padx=(5, 0))
        att_btn_frame = ttk.Frame(send_inner)
        att_btn_frame.grid(row=srow, column=2, sticky=tk.W, pady=2, padx=5)
        ttk.Button(att_btn_frame, text="浏览...", command=self._on_browse_send_attachment_btn, width=8).pack(side=tk.LEFT)
        ttk.Button(att_btn_frame, text="清空",
                   command=lambda: self.entry_send_attachment.delete(0, tk.END), width=6).pack(side=tk.LEFT, padx=3)
        srow += 1

        ttk.Separator(send_inner, orient=tk.HORIZONTAL).grid(
            row=srow, column=0, columnspan=3, sticky=tk.EW, pady=8)
        srow += 1

        ttk.Label(send_inner, text="定时设置", font=("", 10, "bold")).grid(
            row=srow, column=0, columnspan=3, sticky=tk.W)
        srow += 1

        ttk.Checkbutton(send_inner, text="启用定时发送", variable=self.var_sd_enabled).grid(
            row=srow, column=0, columnspan=3, sticky=tk.W, pady=2)
        srow += 1

        sd_day_frame = ttk.Frame(send_inner)
        sd_day_frame.grid(row=srow, column=0, columnspan=3, sticky=tk.W, pady=2)
        weekday_names = self.cb.get_weekday_names() if self.cb.get_weekday_names else self.WEEKDAY_NAMES
        for i, name in enumerate(weekday_names):
            var = self.send_day_vars[i]
            ttk.Checkbutton(sd_day_frame, text=name, variable=var).pack(side=tk.LEFT, padx=4)
        srow += 1

        sd_time_frame = ttk.Frame(send_inner)
        sd_time_frame.grid(row=srow, column=0, columnspan=3, sticky=tk.W, pady=2)
        ttk.Label(sd_time_frame, text="时").pack(side=tk.LEFT)
        self.spin_send_hour = ttk.Spinbox(sd_time_frame, from_=0, to=23, width=4, justify=tk.CENTER)
        self.spin_send_hour.pack(side=tk.LEFT, padx=(2, 5)); self.spin_send_hour.set("8")
        ttk.Label(sd_time_frame, text="分").pack(side=tk.LEFT)
        self.spin_send_min = ttk.Spinbox(sd_time_frame, from_=0, to=59, width=4, justify=tk.CENTER)
        self.spin_send_min.pack(side=tk.LEFT, padx=(2, 5)); self.spin_send_min.set("0")
        ttk.Label(sd_time_frame, text="秒").pack(side=tk.LEFT)
        self.spin_send_sec = ttk.Spinbox(sd_time_frame, from_=0, to=59, width=4, justify=tk.CENTER)
        self.spin_send_sec.pack(side=tk.LEFT, padx=(2, 5)); self.spin_send_sec.set("0")
        srow += 1

        sd_btn_frame = ttk.Frame(send_inner)
        sd_btn_frame.grid(row=srow, column=0, columnspan=3, pady=8)
        ttk.Button(sd_btn_frame, text="立即执行一次", command=self._test_send,
                   width=14).pack(side=tk.LEFT, padx=3)
        ttk.Button(sd_btn_frame, text="测试SMTP连接", command=self._test_smtp_btn,
                   width=14).pack(side=tk.LEFT, padx=3)
        ttk.Button(sd_btn_frame, text="保存配置", command=self._save_send_config,
                   width=10).pack(side=tk.LEFT, padx=3)
        ttk.Button(sd_btn_frame, text="清除配置", command=self._clear_send_config,
                   width=10).pack(side=tk.LEFT, padx=3)

    def _load_config_to_ui(self):
        cfg = self.cb.config()
        self.entry_smtp_server.delete(0, tk.END)
        self.entry_smtp_server.insert(0, cfg.get("smtp_server", ""))
        self.entry_smtp_port.delete(0, tk.END)
        self.entry_smtp_port.insert(0, str(cfg.get("smtp_port", 465)))
        self.var_smtp_ssl.set(cfg.get("smtp_ssl", True))
        self.var_skip_ssl_smtp.set(cfg.get("skip_ssl_smtp", False))
        self.entry_send_user.delete(0, tk.END)
        self.entry_send_user.insert(0, cfg.get("send_user", ""))
        self.entry_send_pass.delete(0, tk.END)
        self.entry_send_pass.insert(0, cfg.get("send_pass", ""))
        self.entry_send_to.delete(0, tk.END)
        self.entry_send_to.insert(0, cfg.get("send_to", ""))
        self.entry_send_subject.delete(0, tk.END)
        self.entry_send_subject.insert(0, cfg.get("send_subject", ""))
        self.text_send_body.delete(1.0, tk.END)
        self.text_send_body.insert(1.0, cfg.get("send_body", ""))
        self.entry_send_attachment.delete(0, tk.END)
        att_mode = cfg.get("send_attachment_mode", "single")
        self.var_att_mode.set(att_mode)
        if att_mode == "single":
            path = cfg.get("send_attachment", "")
            if path:
                self.entry_send_attachment.insert(0, path)
        else:
            paths = cfg.get("send_attachment_list", [])
            if paths:
                self.entry_send_attachment.insert(0, "; ".join(paths))

        sd = cfg.get("schedule_send", {})
        self.var_sd_enabled.set(sd.get("enabled", False))
        send_days = sd.get("days", [])
        for i in range(7):
            self.send_day_vars[i].set((i + 1) in send_days)
        self.spin_send_hour.set(str(sd.get("hour", 8)))
        self.spin_send_min.set(str(sd.get("minute", 0)))
        self.spin_send_sec.set(str(sd.get("second", 0)))

    def _save_send_config(self):
        sd_days = [i + 1 for i, var in enumerate(self.send_day_vars) if var.get()]
        att_mode = self.var_att_mode.get()
        cfg = self.cb.config()
        cfg.update({
            "smtp_server": self.entry_smtp_server.get().strip(),
            "smtp_port": int(self.entry_smtp_port.get().strip() or "465"),
            "smtp_ssl": self.var_smtp_ssl.get(),
            "skip_ssl_smtp": self.var_skip_ssl_smtp.get(),
            "send_user": self.entry_send_user.get().strip(),
            "send_pass": self.entry_send_pass.get().strip(),
            "send_to": self.entry_send_to.get().strip(),
            "send_subject": self.entry_send_subject.get().strip(),
            "send_body": self.text_send_body.get(1.0, tk.END).strip(),
            "send_attachment_mode": att_mode,
            "send_attachment": self.entry_send_attachment.get().strip() if att_mode == "single" else "",
            "send_attachment_list": self._get_attachment_list_from_ui() if att_mode != "single" else [],
            "schedule_send": {
                "days": sd_days,
                "hour": int(self.spin_send_hour.get()),
                "minute": int(self.spin_send_min.get()),
                "second": int(self.spin_send_sec.get()),
                "enabled": self.var_sd_enabled.get(),
            },
        })
        self.cb.save_config(cfg)
        self.cb.log("发送配置已保存", "send")

    def _clear_send_config(self):
        self.entry_smtp_server.delete(0, tk.END)
        self.entry_smtp_port.delete(0, tk.END)
        self.entry_smtp_port.insert(0, "465")
        self.var_smtp_ssl.set(True)
        self.var_skip_ssl_smtp.set(False)
        self.entry_send_user.delete(0, tk.END)
        self.entry_send_pass.delete(0, tk.END)
        self.entry_send_to.delete(0, tk.END)
        self.entry_send_subject.delete(0, tk.END)
        self.text_send_body.delete(1.0, tk.END)
        self.entry_send_attachment.delete(0, tk.END)
        self.var_att_mode.set("single")
        self.var_sd_enabled.set(False)
        for var in self.send_day_vars:
            var.set(False)
        self.spin_send_hour.set("8")
        self.spin_send_min.set("0")
        self.spin_send_sec.set("0")
        self._save_send_config()
        self.cb.log("发送配置已清除", "send")

    def _get_attachment_list_from_ui(self):
        text = self.entry_send_attachment.get().strip()
        if not text:
            return []
        return [p.strip() for p in text.split(";") if p.strip()]

    def _resolve_attachment_paths(self, cfg):
        mode = cfg.get("send_attachment_mode", "single")
        if mode == "single":
            path = cfg.get("send_attachment", "")
            return [path] if path and os.path.isfile(path) else []
        if mode == "multi":
            paths = cfg.get("send_attachment_list", [])
            return [p for p in paths if os.path.isfile(p)]
        if mode == "folder":
            paths = cfg.get("send_attachment_list", [])
            folder = paths[0] if paths else ""
            if folder and os.path.isdir(folder):
                return sorted([
                    os.path.join(folder, f)
                    for f in os.listdir(folder)
                    if os.path.isfile(os.path.join(folder, f))
                ])
            return []
        return []

    def _on_browse_send_attachment_btn(self):
        mode = self.var_att_mode.get()
        if mode == "single":
            path = filedialog.askopenfilename(title="选择要发送的附件文件")
            if path:
                self.entry_send_attachment.delete(0, tk.END)
                self.entry_send_attachment.insert(0, path)
        elif mode == "multi":
            paths = filedialog.askopenfilenames(title="选择多个附件文件")
            if paths:
                self.entry_send_attachment.delete(0, tk.END)
                self.entry_send_attachment.insert(0, "; ".join(paths))
        elif mode == "folder":
            path = filedialog.askdirectory(title="选择文件夹（将发送文件夹内所有文件）")
            if path:
                self.entry_send_attachment.delete(0, tk.END)
                self.entry_send_attachment.insert(0, path)

    def _on_send_attachment_mode_changed(self):
        self.entry_send_attachment.delete(0, tk.END)

    def _test_smtp_btn(self):
        self._save_send_config()
        cfg = self.cb.config()
        send_user = cfg.get("send_user") or cfg.get("email_user")
        send_pass = cfg.get("send_pass") or cfg.get("email_pass")
        if not send_user or not send_pass:
            messagebox.showerror("错误", "请填写发件人账号和密码")
            return

        self.cb.log("正在测试SMTP连接...", "send")

        def on_complete(_result):
            self.cb.log("SMTP连接测试成功！", "send")
            messagebox.showinfo("连接成功", "SMTP服务器连接成功！")

        def on_error(exc):
            self.cb.log(f"SMTP连接测试失败: {exc}", "send")
            messagebox.showerror("连接失败", f"SMTP连接失败:\n{exc}")

        self.cb.coordinator.test_smtp(on_complete=on_complete, on_error=on_error)

    def _test_send(self):
        self._save_send_config()
        cfg = self.cb.config()
        send_user = cfg.get("send_user") or cfg.get("email_user")
        send_pass = cfg.get("send_pass") or cfg.get("email_pass")

        if not send_user or not send_pass:
            messagebox.showerror("错误", "请填写发件人账号和密码（或下载页的邮箱账号/密码）")
            return
        if not cfg.get("send_to"):
            messagebox.showerror("错误", "请填写收件人")
            return

        self.cb.log("=" * 50, "send")
        self.cb.log("开始发送邮件...", "send")

        def on_complete(_result):
            self.cb.log("发送成功！", "send")
            self.cb.log("=" * 50, "send")
            messagebox.showinfo("发送成功", "邮件已发送！")

        def on_error(exc):
            self.cb.log(f"发送出错: {exc}", "send")
            messagebox.showerror("发送出错", str(exc))
            self.cb.log("=" * 50, "send")

        self.cb.coordinator.run_send_once(on_complete=on_complete, on_error=on_error)