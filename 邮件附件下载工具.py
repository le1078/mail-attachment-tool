"""
邮件附件自动下载 & 定时发送工具
- 定时下载指定发件人的邮件附件
- 定时发送邮件（带附件）给指定收件人
- 邮件查询（收件箱/已发送）
- 失败告警、重试、日志持久化
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import datetime
import csv
import os

import pystray
from PIL import Image, ImageDraw

from config_manager import (
    CONFIG_FILE, DEFAULT_CONFIG as _DEFAULT_CONFIG,
    load_config, save_config, load_persisted_log, persist_log_entries,
    export_config_to, import_config_from, validate_config, LOG_CATEGORIES_LABELS,
    load_history, add_history_record, clear_history, MAX_HISTORY_RECORDS
)
from mail_utils import decode_str, clean_filename
from imap_backend import (
    connect_imap, test_imap_connection, get_sent_folder_name,
    archive_to_sent, fetch_attachments, query_emails, fetch_email_detail,
    batch_fetch_email_details, mark_email_as_read, retry_on_network_error
)
from smtp_backend import send_email, test_smtp_connection
from export_utils import export_to_excel, export_csv as export_csv_util
# ==================== GUI ====================
class MailAttachmentTool:
    WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("邮件自动化工具HK")
        self.root.geometry("780x800")
        self.root.resizable(True, True)

        self.config = load_config()
        self.running = False
        self.stop_event = threading.Event()
        self.scheduler_thread = None
        self.tray_icon = None
        # 加载持久化日志
        self.log_entries = load_persisted_log()
        self.log_filter = "all"
        self.last_error_time = {}  # 错误去重: {error_key: timestamp}
        self.retry_state = {}  # 重试状态: {task_type: {"fail_count": N, "last_fail": ts}}
        self.querying = False  # 邮件查询防重入标志
        self._query_results_cache: dict[str, dict[str, str]] = {}  # 邮件查询结果缓存 key=mail_id

        self._build_ui()
        self._start_clock_update()

        self.log("程序已启动，请配置参数后点击【启动定时任务】")
        try:
            validate_config(self.config)
        except ValueError as e:
            self.log(f"⚠ 配置校验警告: {e}", "system")

    # ---------- UI构建 ----------
    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- 标题 ---
        title = ttk.Label(main_frame, text="邮件附件定时下载 & 邮件定时发送",
                          font=("Microsoft YaHei", 14, "bold"))
        title.pack(pady=(0, 5))

        # --- 时间显示栏 (功能5) ---
        self.time_frame = ttk.Frame(main_frame)
        self.time_frame.pack(fill=tk.X, pady=(0, 8))
        self.time_label = ttk.Label(self.time_frame, text="",
                                     font=("Consolas", 10), foreground="#0078D4")
        self.time_label.pack(side=tk.LEFT)
        self.next_task_label = ttk.Label(self.time_frame, text="",
                                          font=("Consolas", 9), foreground="#555")
        self.next_task_label.pack(side=tk.LEFT, padx=(20, 0))

        # 笔记本（选项卡）
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # ================================================================
        # Tab 1: 下载配置
        # ================================================================
        tab_dl = ttk.Frame(notebook, padding=10)
        notebook.add(tab_dl, text="下载配置")

        dl_inner = ttk.Frame(tab_dl)
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

        self.var_skip_ssl = tk.BooleanVar()
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
        ttk.Button(folder_frame, text="浏览...", command=self._browse_folder,
                   width=8).pack(side=tk.LEFT, padx=5)
        row += 1

        ttk.Separator(dl_inner, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=3,
                                                            sticky=tk.EW, pady=8)
        row += 1

        ttk.Label(dl_inner, text="定时设置", font=("", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky=tk.W)
        row += 1

        self.var_dl_enabled = tk.BooleanVar(value=True)
        self.cb_dl_enabled = ttk.Checkbutton(dl_inner, text="启用定时下载", variable=self.var_dl_enabled)
        self.cb_dl_enabled.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=2)
        row += 1

        dl_day_frame = ttk.Frame(dl_inner)
        dl_day_frame.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=2)
        self.day_vars = []
        for i, name in enumerate(self.WEEKDAY_NAMES):
            var = tk.BooleanVar()
            self.day_vars.append(var)
            ttk.Checkbutton(dl_day_frame, text=name, variable=var).pack(side=tk.LEFT, padx=4)
        row += 1

        dl_time_frame = ttk.Frame(dl_inner)
        dl_time_frame.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=2)
        ttk.Label(dl_time_frame, text="时").pack(side=tk.LEFT)
        self.spin_hour = ttk.Spinbox(dl_time_frame, from_=0, to=23, width=4, justify=tk.CENTER)
        self.spin_hour.pack(side=tk.LEFT, padx=(2, 5)); self.spin_hour.set("9")
        ttk.Label(dl_time_frame, text="分").pack(side=tk.LEFT)
        self.spin_min = ttk.Spinbox(dl_time_frame, from_=0, to=59, width=4, justify=tk.CENTER)
        self.spin_min.pack(side=tk.LEFT, padx=(2, 5)); self.spin_min.set("0")
        ttk.Label(dl_time_frame, text="秒").pack(side=tk.LEFT)
        self.spin_sec = ttk.Spinbox(dl_time_frame, from_=0, to=59, width=4, justify=tk.CENTER)
        self.spin_sec.pack(side=tk.LEFT, padx=(2, 5)); self.spin_sec.set("0")
        row += 1

        # 邮件筛选
        ttk.Separator(dl_inner, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=3,
                                                            sticky=tk.EW, pady=8)
        row += 1
        ttk.Label(dl_inner, text="邮件筛选（可选：仅下载符合条件的邮件）",
                  font=("", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky=tk.W)
        row += 1

        ttk.Label(dl_inner, text="接收日:").grid(row=row, column=0, sticky=tk.W, pady=2)
        dl_email_day_frame = ttk.Frame(dl_inner)
        dl_email_day_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=2)
        self.dl_email_day_vars = []
        for i, name in enumerate(self.WEEKDAY_NAMES):
            var = tk.BooleanVar()
            self.dl_email_day_vars.append(var)
            ttk.Checkbutton(dl_email_day_frame, text=name, variable=var).pack(side=tk.LEFT, padx=3)
        ttk.Label(dl_email_day_frame, text=" (空=不限)", foreground="gray").pack(side=tk.LEFT)
        row += 1

        ttk.Label(dl_inner, text="接收时间段:").grid(row=row, column=0, sticky=tk.W, pady=2)
        dl_email_time_frame = ttk.Frame(dl_inner)
        dl_email_time_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=2)
        self.var_dl_email_time = tk.BooleanVar()
        ttk.Checkbutton(dl_email_time_frame, text="启用", variable=self.var_dl_email_time).pack(side=tk.LEFT, padx=(0, 5))
        self.spin_dl_email_h1 = ttk.Spinbox(dl_email_time_frame, from_=0, to=23, width=3, justify=tk.CENTER)
        self.spin_dl_email_h1.pack(side=tk.LEFT); self.spin_dl_email_h1.set("0")
        ttk.Label(dl_email_time_frame, text=":").pack(side=tk.LEFT)
        self.spin_dl_email_m1 = ttk.Spinbox(dl_email_time_frame, from_=0, to=59, width=3, justify=tk.CENTER)
        self.spin_dl_email_m1.pack(side=tk.LEFT); self.spin_dl_email_m1.set("0")
        ttk.Label(dl_email_time_frame, text=" ~ ").pack(side=tk.LEFT)
        self.spin_dl_email_h2 = ttk.Spinbox(dl_email_time_frame, from_=0, to=23, width=3, justify=tk.CENTER)
        self.spin_dl_email_h2.pack(side=tk.LEFT); self.spin_dl_email_h2.set("23")
        ttk.Label(dl_email_time_frame, text=":").pack(side=tk.LEFT)
        self.spin_dl_email_m2 = ttk.Spinbox(dl_email_time_frame, from_=0, to=59, width=3, justify=tk.CENTER)
        self.spin_dl_email_m2.pack(side=tk.LEFT); self.spin_dl_email_m2.set("59")
        row += 1

        ttk.Label(dl_inner, text="接收日期范围:").grid(row=row, column=0, sticky=tk.W, pady=2)
        dl_email_date_frame = ttk.Frame(dl_inner)
        dl_email_date_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=2)
        self.var_dl_email_date = tk.BooleanVar()
        ttk.Checkbutton(dl_email_date_frame, text="启用", variable=self.var_dl_email_date).pack(side=tk.LEFT, padx=(0, 5))
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

        # ================================================================
        # Tab 2: 发送配置
        # ================================================================
        tab_send = ttk.Frame(notebook, padding=10)
        notebook.add(tab_send, text="发送配置")

        send_inner = ttk.Frame(tab_send)
        send_inner.pack(fill=tk.BOTH, expand=True)
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
        self.var_smtp_ssl = tk.BooleanVar(value=True)
        ttk.Checkbutton(send_inner, text="使用SSL", variable=self.var_smtp_ssl).grid(
            row=srow, column=2, sticky=tk.W, pady=2, padx=5)
        srow += 1

        self.var_skip_ssl_smtp = tk.BooleanVar(value=False)
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

        ttk.Separator(send_inner, orient=tk.HORIZONTAL).grid(row=srow, column=0, columnspan=3,
                                                              sticky=tk.EW, pady=8)
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
        self.var_att_mode = tk.StringVar(value="single")
        ttk.Radiobutton(att_mode_frame, text="单文件", variable=self.var_att_mode, value="single").pack(side=tk.LEFT)
        ttk.Radiobutton(att_mode_frame, text="多文件", variable=self.var_att_mode, value="multi").pack(side=tk.LEFT, padx=8)
        ttk.Radiobutton(att_mode_frame, text="文件夹", variable=self.var_att_mode, value="folder").pack(side=tk.LEFT, padx=8)
        srow += 1

        self.entry_send_attachment = ttk.Entry(send_inner, width=35)
        self.entry_send_attachment.grid(row=srow, column=1, sticky=tk.EW, pady=2, padx=(5, 0))
        att_btn_frame = ttk.Frame(send_inner)
        att_btn_frame.grid(row=srow, column=2, sticky=tk.W, pady=2, padx=5)
        ttk.Button(att_btn_frame, text="浏览...", command=self._browse_send_att, width=8).pack(side=tk.LEFT)
        ttk.Button(att_btn_frame, text="清空",
                   command=lambda: self.entry_send_attachment.delete(0, tk.END), width=6).pack(side=tk.LEFT, padx=3)
        srow += 1

        ttk.Separator(send_inner, orient=tk.HORIZONTAL).grid(row=srow, column=0, columnspan=3,
                                                              sticky=tk.EW, pady=8)
        srow += 1

        ttk.Label(send_inner, text="定时设置", font=("", 10, "bold")).grid(row=srow, column=0, columnspan=3, sticky=tk.W)
        srow += 1

        self.var_sd_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(send_inner, text="启用定时发送", variable=self.var_sd_enabled).grid(
            row=srow, column=0, columnspan=3, sticky=tk.W, pady=2)
        srow += 1

        sd_day_frame = ttk.Frame(send_inner)
        sd_day_frame.grid(row=srow, column=0, columnspan=3, sticky=tk.W, pady=2)
        self.send_day_vars = []
        for i, name in enumerate(self.WEEKDAY_NAMES):
            var = tk.BooleanVar()
            self.send_day_vars.append(var)
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

        # ================================================================
        # Tab 3: 邮件查询 (功能3)
        # ================================================================
        tab_query = ttk.Frame(notebook, padding=5)
        notebook.add(tab_query, text="邮件查询")

        # 查询控制栏 — 第一行：文件夹 + 关键词 + 操作按钮
        query_ctrl1 = ttk.Frame(tab_query)
        query_ctrl1.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(query_ctrl1, text="文件夹:").pack(side=tk.LEFT)
        self.query_folder_var = tk.StringVar(value="INBOX")
        self.combo_query_folder = ttk.Combobox(query_ctrl1, width=15, state="readonly",
                                               values=["INBOX", "已发送"])
        self.combo_query_folder.pack(side=tk.LEFT, padx=3)
        self.combo_query_folder.set("INBOX")

        ttk.Label(query_ctrl1, text="搜索关键词:").pack(side=tk.LEFT, padx=(10, 0))
        self.entry_query_keyword = ttk.Entry(query_ctrl1, width=20)
        self.entry_query_keyword.pack(side=tk.LEFT, padx=3)

        self.var_search_body = tk.BooleanVar(value=False)
        self.chk_search_body = ttk.Checkbutton(query_ctrl1, text="搜索正文", variable=self.var_search_body)
        self.chk_search_body.pack(side=tk.LEFT, padx=3)

        ttk.Label(query_ctrl1, text="已读/未读:").pack(side=tk.LEFT, padx=(10, 0))
        self.combo_read_filter = ttk.Combobox(query_ctrl1, width=6, state="readonly",
                                              values=["全部", "已读", "未读"])
        self.combo_read_filter.pack(side=tk.LEFT, padx=3)
        self.combo_read_filter.set("全部")

        self.btn_query_mail = ttk.Button(query_ctrl1, text="查询", command=self._do_mail_query, width=8)
        self.btn_query_mail.pack(side=tk.LEFT, padx=3)
        self.btn_refresh_mail = ttk.Button(query_ctrl1, text="刷新列表", command=self._do_mail_query, width=8)
        self.btn_refresh_mail.pack(side=tk.LEFT, padx=3)
        self.btn_sent_mail = ttk.Button(query_ctrl1, text="查看已发送", command=self._do_sent_mail_query, width=10)
        self.btn_sent_mail.pack(side=tk.LEFT, padx=3)

        # 查询控制栏 — 第二行：日期范围 + 数量
        query_ctrl2 = ttk.Frame(tab_query)
        query_ctrl2.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(query_ctrl2, text="起始:").pack(side=tk.LEFT)
        self.entry_query_start_date = ttk.Entry(query_ctrl2, width=10, justify=tk.CENTER)
        self.entry_query_start_date.pack(side=tk.LEFT, padx=2)
        self.entry_query_start_date.insert(0, "")
        ttk.Label(query_ctrl2, text="截止:").pack(side=tk.LEFT, padx=(5, 0))
        self.entry_query_end_date = ttk.Entry(query_ctrl2, width=10, justify=tk.CENTER)
        self.entry_query_end_date.pack(side=tk.LEFT, padx=2)
        self.entry_query_end_date.insert(0, "")

        ttk.Label(query_ctrl2, text="数量:").pack(side=tk.LEFT, padx=(10, 0))
        self.spin_query_count = ttk.Spinbox(query_ctrl2, from_=10, to=200, width=5, justify=tk.CENTER)
        self.spin_query_count.pack(side=tk.LEFT, padx=(2, 5))
        self.spin_query_count.set("50")

        # 邮件列表操作栏
        query_ctrl3 = ttk.Frame(tab_query)
        query_ctrl3.pack(fill=tk.X, pady=(3, 3))
        ttk.Button(query_ctrl3, text="全选", command=self._select_all_mails, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Button(query_ctrl3, text="取消全选", command=self._deselect_all_mails, width=8).pack(side=tk.LEFT, padx=2)
        self.lbl_selected_count = ttk.Label(query_ctrl3, text="", foreground="#0078D4")
        self.lbl_selected_count.pack(side=tk.LEFT, padx=(10, 0))
        self.btn_export_excel = ttk.Button(query_ctrl3, text="导出Excel",
            command=self._export_query_excel, width=12)
        self.btn_export_excel.pack(side=tk.RIGHT, padx=2)
        self.btn_batch_download = ttk.Button(query_ctrl3, text="批量下载所选附件",
                                              command=self._batch_download_query_attachments, width=18)
        self.btn_batch_download.pack(side=tk.RIGHT, padx=2)

        # 邮件列表 + 详情 左右分栏
        query_paned = ttk.PanedWindow(tab_query, orient=tk.HORIZONTAL)
        query_paned.pack(fill=tk.BOTH, expand=True)

        # 左侧：邮件列表 (Treeview)
        list_frame = ttk.Frame(query_paned)
        query_paned.add(list_frame, weight=3)

        columns = ("状态", "发件人/收件人", "主题", "日期")
        self.mail_tree = ttk.Treeview(list_frame, columns=columns, show="headings",
                                       selectmode="extended", height=15)
        self.mail_tree.heading("状态", text="状态", command=lambda: self._sort_treeview("状态"))
        self.mail_tree.heading("发件人/收件人", text="发件人/收件人", command=lambda: self._sort_treeview("发件人/收件人"))
        self.mail_tree.heading("主题", text="主题", command=lambda: self._sort_treeview("主题"))
        self.mail_tree.heading("日期", text="日期", command=lambda: self._sort_treeview("日期"))
        self.mail_tree.column("状态", width=48, anchor=tk.CENTER)
        self.mail_tree.column("发件人/收件人", width=150)
        self.mail_tree.column("主题", width=190)
        self.mail_tree.column("日期", width=110)
        self.mail_tree.tag_configure("unseen", font=("", 9, "bold"))
        self.mail_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll = ttk.Scrollbar(list_frame, command=self.mail_tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.mail_tree.config(yscrollcommand=tree_scroll.set)
        self.mail_tree.bind("<<TreeviewSelect>>", self._on_mail_select)

        self._tree_sort_col = None
        self._tree_sort_reverse = False

        self._tree_context_menu = tk.Menu(self.mail_tree, tearoff=0)
        self._tree_context_menu.add_command(label="标记已读", command=self._mark_selected_as_read)
        self._tree_context_menu.add_command(label="下载所选邮件附件", command=self._download_query_attachment)
        self._tree_context_menu.add_command(label="批量下载所选邮件附件", command=self._batch_download_query_attachments)
        self._tree_context_menu.add_separator()
        self._tree_context_menu.add_command(label="导出查询结果为CSV", command=self._export_query_csv)
        self._tree_context_menu.add_command(label="导出查询结果为Excel", command=self._export_query_excel)
        self.mail_tree.bind("<Button-3>", self._on_tree_right_click)

        # 右侧：邮件详情
        detail_frame = ttk.Frame(query_paned)
        query_paned.add(detail_frame, weight=2)

        ttk.Label(detail_frame, text="邮件详情", font=("", 10, "bold")).pack(anchor=tk.W, pady=(0, 5))
        self.mail_detail_text = scrolledtext.ScrolledText(detail_frame, wrap=tk.WORD,
                                                           font=("Consolas", 9),
                                                           state=tk.DISABLED, height=20)
        self.mail_detail_text.pack(fill=tk.BOTH, expand=True)

        detail_btn_frame = ttk.Frame(detail_frame)
        detail_btn_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(detail_btn_frame, text="下载此邮件附件",
                   command=self._download_query_attachment, width=16).pack(side=tk.LEFT, padx=3)
        ttk.Button(detail_btn_frame, text="标记已读",
                   command=self._mark_selected_as_read, width=10).pack(side=tk.LEFT, padx=3)
        ttk.Button(detail_btn_frame, text="导出CSV",
                   command=self._export_query_csv, width=8).pack(side=tk.LEFT, padx=3)
        ttk.Button(detail_btn_frame, text="导出Excel",
                   command=self._export_query_excel, width=9).pack(side=tk.LEFT, padx=3)
        self.query_detail_data = None  # 缓存选中邮件的详情

        # ================================================================
        # Tab 4: 运行日志
        # ================================================================
        tab_log = ttk.Frame(notebook, padding=5)
        notebook.add(tab_log, text="运行日志")

        log_toolbar = ttk.Frame(tab_log)
        log_toolbar.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(log_toolbar, text="筛选:").pack(side=tk.LEFT)
        ttk.Button(log_toolbar, text="全部", width=6,
                   command=lambda: self._apply_log_filter("all")).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_toolbar, text="下载", width=6,
                   command=lambda: self._apply_log_filter("download")).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_toolbar, text="发送", width=6,
                   command=lambda: self._apply_log_filter("send")).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_toolbar, text="查询", width=6,
                   command=lambda: self._apply_log_filter("query")).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_toolbar, text="系统", width=6,
                   command=lambda: self._apply_log_filter("system")).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_toolbar, text="导出日志", width=10,
                   command=self._export_log).pack(side=tk.RIGHT, padx=2)
        ttk.Button(log_toolbar, text="清空日志", width=10,
                   command=self._clear_log).pack(side=tk.RIGHT, padx=2)

        self.log_text = scrolledtext.ScrolledText(tab_log, wrap=tk.WORD,
                                                   font=("Consolas", 9),
                                                   state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 定时导出设置
        export_frame = ttk.LabelFrame(tab_log, text="定时导出日志", padding=5)
        export_frame.pack(fill=tk.X, pady=(5, 0))

        er1 = ttk.Frame(export_frame)
        er1.pack(fill=tk.X, pady=2)
        self.var_export_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(er1, text="启用定时导出", variable=self.var_export_enabled).pack(side=tk.LEFT)
        ttk.Label(er1, text="导出目录:").pack(side=tk.LEFT, padx=(15, 0))
        self.entry_export_folder = ttk.Entry(er1, width=28)
        self.entry_export_folder.pack(side=tk.LEFT, padx=3)
        ttk.Button(er1, text="浏览...", command=self._browse_export_folder, width=7).pack(side=tk.LEFT)

        er2 = ttk.Frame(export_frame)
        er2.pack(fill=tk.X, pady=2)
        ttk.Label(er2, text="日期:").pack(side=tk.LEFT)
        self.export_day_vars = []
        for i, name in enumerate(self.WEEKDAY_NAMES):
            var = tk.BooleanVar()
            self.export_day_vars.append(var)
            ttk.Checkbutton(er2, text=name, variable=var).pack(side=tk.LEFT, padx=2)
        ttk.Label(er2, text="  时间: 时").pack(side=tk.LEFT, padx=(10, 0))
        self.spin_export_hour = ttk.Spinbox(er2, from_=0, to=23, width=4, justify=tk.CENTER)
        self.spin_export_hour.pack(side=tk.LEFT, padx=(2, 3)); self.spin_export_hour.set("23")
        ttk.Label(er2, text="分").pack(side=tk.LEFT)
        self.spin_export_min = ttk.Spinbox(er2, from_=0, to=59, width=4, justify=tk.CENTER)
        self.spin_export_min.pack(side=tk.LEFT, padx=(2, 3)); self.spin_export_min.set("59")
        ttk.Label(er2, text="秒").pack(side=tk.LEFT)
        self.spin_export_sec = ttk.Spinbox(er2, from_=0, to=59, width=4, justify=tk.CENTER)
        self.spin_export_sec.pack(side=tk.LEFT, padx=(2, 3)); self.spin_export_sec.set("0")

        # --- 底部控制栏 ---
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=tk.X, pady=(10, 0))

        self.status_label = ttk.Label(bottom_frame, text="状态: 未启动", foreground="gray")
        self.status_label.pack(side=tk.LEFT)

        # 重试设置
        self.var_retry_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(bottom_frame, text="失败自动重试", variable=self.var_retry_enabled).pack(side=tk.LEFT, padx=(15, 0))
        ttk.Label(bottom_frame, text="重试次数:").pack(side=tk.LEFT, padx=(2, 0))
        self.spin_retry_count = ttk.Spinbox(bottom_frame, from_=1, to=10, width=3, justify=tk.CENTER)
        self.spin_retry_count.pack(side=tk.LEFT); self.spin_retry_count.set("3")
        ttk.Label(bottom_frame, text="间隔(分):").pack(side=tk.LEFT, padx=(2, 0))
        self.spin_retry_interval = ttk.Spinbox(bottom_frame, from_=1, to=60, width=3, justify=tk.CENTER)
        self.spin_retry_interval.pack(side=tk.LEFT); self.spin_retry_interval.set("5")

        # 配置导入导出
        ttk.Button(bottom_frame, text="导出配置", command=self._export_config_btn, width=10).pack(side=tk.RIGHT, padx=3)
        ttk.Button(bottom_frame, text="导入配置", command=self._import_config_btn, width=10).pack(side=tk.RIGHT, padx=3)
        ttk.Separator(bottom_frame, orient=tk.VERTICAL).pack(side=tk.RIGHT, padx=5, fill=tk.Y)

        self.btn_start = ttk.Button(bottom_frame, text="启动定时任务",
                                     command=self._toggle_scheduler, width=16)
        self.btn_start.pack(side=tk.RIGHT, padx=5)

        ttk.Button(bottom_frame, text="全部应用配置", command=self._save_ui_config,
                   width=12).pack(side=tk.RIGHT, padx=5)

        # ================================================================
        # Tab 5: 下载历史
        # ================================================================
        tab_history = ttk.Frame(notebook, padding=5)
        notebook.add(tab_history, text="下载历史")

        # 工具栏：搜索 + 日期筛选 + 按钮
        hist_toolbar = ttk.Frame(tab_history)
        hist_toolbar.pack(fill=tk.X, pady=(0, 3))

        ttk.Label(hist_toolbar, text="搜索:").pack(side=tk.LEFT)
        self.hist_search_var = tk.StringVar()
        self.hist_search_var.trace_add("write", self._on_history_search)
        self.entry_hist_search = ttk.Entry(hist_toolbar, textvariable=self.hist_search_var, width=22)
        self.entry_hist_search.pack(side=tk.LEFT, padx=3)

        ttk.Label(hist_toolbar, text="从:").pack(side=tk.LEFT, padx=(10, 0))
        self.entry_hist_date_from = ttk.Entry(hist_toolbar, width=12, justify=tk.CENTER)
        self.entry_hist_date_from.pack(side=tk.LEFT, padx=2)
        self.entry_hist_date_from.insert(0, "")
        ttk.Label(hist_toolbar, text="到:").pack(side=tk.LEFT)
        self.entry_hist_date_to = ttk.Entry(hist_toolbar, width=12, justify=tk.CENTER)
        self.entry_hist_date_to.pack(side=tk.LEFT, padx=2)
        self.entry_hist_date_to.insert(0, "")
        ttk.Label(hist_toolbar, text="(YYYY-MM-DD)", foreground="gray").pack(side=tk.LEFT, padx=2)

        ttk.Button(hist_toolbar, text="筛选", command=self._on_history_date_filter, width=6).pack(side=tk.LEFT, padx=3)
        ttk.Button(hist_toolbar, text="重置", command=self._on_history_reset_filter, width=6).pack(side=tk.LEFT, padx=2)

        ttk.Separator(hist_toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, padx=8, fill=tk.Y)

        self.btn_open_hist_folder = ttk.Button(hist_toolbar, text="打开文件夹", width=10,
                                                command=self._on_open_history_folder)
        self.btn_open_hist_folder.pack(side=tk.RIGHT, padx=2)
        ttk.Button(hist_toolbar, text="清空历史", width=10,
                   command=self._on_clear_history).pack(side=tk.RIGHT, padx=2)
        ttk.Button(hist_toolbar, text="刷新", width=8,
                   command=lambda: self._refresh_history_list()).pack(side=tk.RIGHT, padx=2)

        # Treeview 列表
        self.hist_tree_frame = ttk.Frame(tab_history)
        self.hist_tree_frame.pack(fill=tk.BOTH, expand=True)

        hist_columns = ("time", "filename", "subject", "sender", "save_path", "size", "status")
        self.hist_tree = ttk.Treeview(self.hist_tree_frame, columns=hist_columns,
                                       show="headings", selectmode="browse")
        self.hist_tree.heading("time", text="下载时间", anchor=tk.W)
        self.hist_tree.heading("filename", text="文件名", anchor=tk.W)
        self.hist_tree.heading("subject", text="邮件主题", anchor=tk.W)
        self.hist_tree.heading("sender", text="发件人", anchor=tk.W)
        self.hist_tree.heading("save_path", text="保存路径", anchor=tk.W)
        self.hist_tree.heading("size", text="大小", anchor=tk.E)
        self.hist_tree.heading("status", text="状态", anchor=tk.CENTER)

        self.hist_tree.column("time", width=140, minwidth=100)
        self.hist_tree.column("filename", width=160, minwidth=80)
        self.hist_tree.column("subject", width=180, minwidth=80)
        self.hist_tree.column("sender", width=150, minwidth=80)
        self.hist_tree.column("save_path", width=200, minwidth=100)
        self.hist_tree.column("size", width=80, minwidth=60)
        self.hist_tree.column("status", width=60, minwidth=50)

        hist_scroll_y = ttk.Scrollbar(self.hist_tree_frame, orient=tk.VERTICAL, command=self.hist_tree.yview)
        hist_scroll_x = ttk.Scrollbar(self.hist_tree_frame, orient=tk.HORIZONTAL, command=self.hist_tree.xview)
        self.hist_tree.configure(yscrollcommand=hist_scroll_y.set, xscrollcommand=hist_scroll_x.set)
        self.hist_tree.grid(row=0, column=0, sticky="nsew")
        hist_scroll_y.grid(row=0, column=1, sticky="ns")
        hist_scroll_x.grid(row=1, column=0, sticky="ew")
        self.hist_tree_frame.rowconfigure(0, weight=1)
        self.hist_tree_frame.columnconfigure(0, weight=1)

        # 状态栏
        self.hist_status_label = ttk.Label(tab_history, text="", foreground="gray")
        self.hist_status_label.pack(fill=tk.X, pady=(3, 0))

        # 底部统计
        self.hist_stat_label = ttk.Label(tab_history, text="", foreground="#555")
        self.hist_stat_label.pack(fill=tk.X)

        # 启动时渲染历史记录
        self._refresh_history_list()

        # 启动时渲染持久化日志
        self._render_persisted_log()

    # ---------- 时钟更新 (功能5) ----------
    def _start_clock_update(self):
        """每秒更新顶部时间显示"""
        self._update_clock()

    def _update_clock(self):
        now = datetime.datetime.now()
        self.time_label.config(text=f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}  {self.WEEKDAY_NAMES[now.weekday()]}")

        # 显示下次执行时间
        cfg = self.config
        parts = []
        if self.running:
            dl = cfg.get("schedule_download", {})
            if dl.get("enabled") and dl.get("days"):
                dl_days = [self.WEEKDAY_NAMES[d-1] for d in dl["days"]]
                parts.append(f"下次下载: {','.join(dl_days)} {dl['hour']:02d}:{dl['minute']:02d}:{dl['second']:02d}")
            sd = cfg.get("schedule_send", {})
            if sd.get("enabled") and sd.get("days"):
                sd_days = [self.WEEKDAY_NAMES[d-1] for d in sd["days"]]
                parts.append(f"下次发送: {','.join(sd_days)} {sd['hour']:02d}:{sd['minute']:02d}:{sd['second']:02d}")
        else:
            parts.append("定时任务未启动")
        self.next_task_label.config(text=" | ".join(parts))

        self.root.after(1000, self._update_clock)

    # ---------- 配置加载/保存 ----------
    def _load_config_to_ui(self):
        cfg = self.config
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

        dl = cfg.get("schedule_download", _DEFAULT_CONFIG["schedule_download"])
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

        # 发送配置
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

        sd = cfg.get("schedule_send", _DEFAULT_CONFIG["schedule_send"])
        self.var_sd_enabled.set(sd.get("enabled", False))
        send_days = sd.get("days", [])
        for i in range(7):
            self.send_day_vars[i].set((i + 1) in send_days)
        self.spin_send_hour.set(str(sd.get("hour", 8)))
        self.spin_send_min.set(str(sd.get("minute", 0)))
        self.spin_send_sec.set(str(sd.get("second", 0)))

        # 日志导出
        self.var_export_enabled.set(cfg.get("log_export_enabled", False))
        self.entry_export_folder.delete(0, tk.END)
        self.entry_export_folder.insert(0, cfg.get("log_export_folder", ""))
        exp_days = cfg.get("log_export_days", [])
        for i in range(7):
            self.export_day_vars[i].set((i + 1) in exp_days)
        self.spin_export_hour.set(str(cfg.get("log_export_hour", 23)))
        self.spin_export_min.set(str(cfg.get("log_export_minute", 59)))
        self.spin_export_sec.set(str(cfg.get("log_export_second", 0)))

        # 重试设置
        self.var_retry_enabled.set(cfg.get("retry_enabled", True))
        self.spin_retry_count.set(str(cfg.get("retry_count", 3)))
        self.spin_retry_interval.set(str(cfg.get("retry_interval_minutes", 5)))

    def _save_ui_config(self):
        try:
            dl_days = [i + 1 for i, var in enumerate(self.day_vars) if var.get()]
            sd_days = [i + 1 for i, var in enumerate(self.send_day_vars) if var.get()]
            exp_days = [i + 1 for i, var in enumerate(self.export_day_vars) if var.get()]

            cfg = {
                "imap_server": self.entry_server.get().strip(),
                "imap_port": int(self.entry_port.get().strip() or "993"),
                "email_user": self.entry_user.get().strip(),
                "email_pass": self.entry_pass.get().strip(),
                "sender_filter": "",
                "sender_filter_list": [
                    s.strip() for s in self.entry_sender.get().split(",") if s.strip()
                ],
                "download_keyword_filter": self.entry_keyword.get().strip(),
                "download_read_status": {"全部邮件": "all", "仅未读": "unseen", "仅已读": "seen"}.get(self.combo_read_status.get(), "all"),
                "save_folder": self.entry_folder.get().strip(),
                "skip_ssl_verify": self.var_skip_ssl.get(),
                "schedule_download": {
                    "days": dl_days,
                    "hour": int(self.spin_hour.get()),
                    "minute": int(self.spin_min.get()),
                    "second": int(self.spin_sec.get()),
                    "enabled": self.var_dl_enabled.get()
                },
                "download_filter_days": [i + 1 for i, var in enumerate(self.dl_email_day_vars) if var.get()],
                "download_filter_time_enabled": self.var_dl_email_time.get(),
                "download_filter_time_start": f"{int(self.spin_dl_email_h1.get()):02d}:{int(self.spin_dl_email_m1.get()):02d}",
                "download_filter_time_end": f"{int(self.spin_dl_email_h2.get()):02d}:{int(self.spin_dl_email_m2.get()):02d}",
                "download_filter_date_enabled": self.var_dl_email_date.get(),
                "download_filter_date_start": self.entry_dl_email_date1.get().strip(),
                "download_filter_date_end": self.entry_dl_email_date2.get().strip(),
                "smtp_server": self.entry_smtp_server.get().strip(),
                "smtp_port": int(self.entry_smtp_port.get().strip() or "465"),
                "smtp_ssl": self.var_smtp_ssl.get(),
                "skip_ssl_smtp": self.var_skip_ssl_smtp.get(),
                "send_user": self.entry_send_user.get().strip(),
                "send_pass": self.entry_send_pass.get().strip(),
                "send_to": self.entry_send_to.get().strip(),
                "send_subject": self.entry_send_subject.get().strip(),
                "send_body": self.text_send_body.get(1.0, tk.END).strip(),
                "send_attachment_mode": self.var_att_mode.get(),
                "send_attachment": self.entry_send_attachment.get().strip() if self.var_att_mode.get() == "single" else "",
                "send_attachment_list": self._get_attachment_list_from_ui() if self.var_att_mode.get() != "single" else [],
                "schedule_send": {
                    "days": sd_days,
                    "hour": int(self.spin_send_hour.get()),
                    "minute": int(self.spin_send_min.get()),
                    "second": int(self.spin_send_sec.get()),
                    "enabled": self.var_sd_enabled.get()
                },
                "log_export_enabled": self.var_export_enabled.get(),
                "log_export_folder": self.entry_export_folder.get().strip(),
                "log_export_days": exp_days,
                "log_export_hour": int(self.spin_export_hour.get()),
                "log_export_minute": int(self.spin_export_min.get()),
                "log_export_second": int(self.spin_export_sec.get()),
                "retry_enabled": self.var_retry_enabled.get(),
                "retry_count": int(self.spin_retry_count.get()),
                "retry_interval_minutes": int(self.spin_retry_interval.get()),
            }
            save_config(cfg)
            self.config = cfg
            self.log("配置已保存")
        except ValueError as e:
            messagebox.showerror("错误", f"端口/时间必须是数字\n{str(e)}")

    def _save_download_config(self):
        dl_days = [i + 1 for i, var in enumerate(self.day_vars) if var.get()]
        self.config.update({
            "imap_server": self.entry_server.get().strip(),
            "imap_port": int(self.entry_port.get().strip() or "993"),
            "email_user": self.entry_user.get().strip(),
            "email_pass": self.entry_pass.get().strip(),
            "sender_filter": "",
            "sender_filter_list": [
                s.strip() for s in self.entry_sender.get().split(",") if s.strip()
            ],
            "download_keyword_filter": self.entry_keyword.get().strip(),
            "download_read_status": {"全部邮件": "all", "仅未读": "unseen", "仅已读": "seen"}.get(self.combo_read_status.get(), "all"),
            "save_folder": self.entry_folder.get().strip(),
            "skip_ssl_verify": self.var_skip_ssl.get(),
            "schedule_download": {
                "days": dl_days,
                "hour": int(self.spin_hour.get()),
                "minute": int(self.spin_min.get()),
                "second": int(self.spin_sec.get()),
                "enabled": self.var_dl_enabled.get()
            },
            "download_filter_days": [i + 1 for i, var in enumerate(self.dl_email_day_vars) if var.get()],
            "download_filter_time_enabled": self.var_dl_email_time.get(),
            "download_filter_time_start": f"{int(self.spin_dl_email_h1.get()):02d}:{int(self.spin_dl_email_m1.get()):02d}",
            "download_filter_time_end": f"{int(self.spin_dl_email_h2.get()):02d}:{int(self.spin_dl_email_m2.get()):02d}",
            "download_filter_date_enabled": self.var_dl_email_date.get(),
            "download_filter_date_start": self.entry_dl_email_date1.get().strip(),
            "download_filter_date_end": self.entry_dl_email_date2.get().strip(),
        })
        save_config(self.config)
        self.log("下载配置已保存", "download")

    def _clear_download_config(self):
        self.entry_server.delete(0, tk.END)
        self.entry_port.delete(0, tk.END); self.entry_port.insert(0, "993")
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
        self.spin_hour.set("9"); self.spin_min.set("0"); self.spin_sec.set("0")
        for var in self.dl_email_day_vars:
            var.set(False)
        self.var_dl_email_time.set(False)
        self.spin_dl_email_h1.set("0"); self.spin_dl_email_m1.set("0")
        self.spin_dl_email_h2.set("23"); self.spin_dl_email_m2.set("59")
        self.var_dl_email_date.set(False)
        self.entry_dl_email_date1.delete(0, tk.END)
        self.entry_dl_email_date2.delete(0, tk.END)
        self._save_download_config()
        self.log("下载配置已清除", "download")

    def _save_send_config(self):
        sd_days = [i + 1 for i, var in enumerate(self.send_day_vars) if var.get()]
        self.config.update({
            "smtp_server": self.entry_smtp_server.get().strip(),
            "smtp_port": int(self.entry_smtp_port.get().strip() or "465"),
            "smtp_ssl": self.var_smtp_ssl.get(),
            "skip_ssl_smtp": self.var_skip_ssl_smtp.get(),
            "send_user": self.entry_send_user.get().strip(),
            "send_pass": self.entry_send_pass.get().strip(),
            "send_to": self.entry_send_to.get().strip(),
            "send_subject": self.entry_send_subject.get().strip(),
            "send_body": self.text_send_body.get(1.0, tk.END).strip(),
            "send_attachment_mode": self.var_att_mode.get(),
            "send_attachment": self.entry_send_attachment.get().strip() if self.var_att_mode.get() == "single" else "",
            "send_attachment_list": self._get_attachment_list_from_ui() if self.var_att_mode.get() != "single" else [],
            "schedule_send": {
                "days": sd_days,
                "hour": int(self.spin_send_hour.get()),
                "minute": int(self.spin_send_min.get()),
                "second": int(self.spin_send_sec.get()),
                "enabled": self.var_sd_enabled.get()
            }
        })
        save_config(self.config)
        self.log("发送配置已保存", "send")

    def _clear_send_config(self):
        self.entry_smtp_server.delete(0, tk.END)
        self.entry_smtp_port.delete(0, tk.END); self.entry_smtp_port.insert(0, "465")
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
        self.spin_send_hour.set("8"); self.spin_send_min.set("0"); self.spin_send_sec.set("0")
        self._save_send_config()
        self.log("发送配置已清除", "send")

    def _browse_folder(self):
        path = filedialog.askdirectory(title="选择附件保存目录")
        if path:
            self.entry_folder.delete(0, tk.END)
            self.entry_folder.insert(0, path)

    def _browse_send_att(self):
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
        elif mode == "multi":
            paths = cfg.get("send_attachment_list", [])
            return [p for p in paths if os.path.isfile(p)]
        elif mode == "folder":
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

    def _format_subject(self, subject):
        now = datetime.datetime.now()
        return subject.replace("YYYY", str(now.year)).replace("MM", str(now.month)).replace("DD", str(now.day))

    # ---------- 测试连接 (建议D) ----------
    def _test_imap_btn(self):
        """测试IMAP连接"""
        self._save_download_config()
        cfg = self.config
        if not cfg["email_user"] or not cfg["email_pass"]:
            messagebox.showerror("错误", "请先填写邮箱账号和密码/授权码")
            return
        try:
            self.log("正在测试IMAP连接...", "download")
            test_imap_connection(cfg["imap_server"], cfg["imap_port"],
                                cfg["email_user"], cfg["email_pass"],
                                cfg.get("skip_ssl_verify", False))
            self.log("IMAP连接测试成功！", "download")
            messagebox.showinfo("连接成功", "IMAP服务器连接成功！")
        except Exception as e:
            self.log(f"IMAP连接测试失败: {e}", "download")
            messagebox.showerror("连接失败", f"IMAP连接失败:\n{e}")

    def _test_smtp_btn(self):
        """测试SMTP连接"""
        self._save_send_config()
        cfg = self.config
        send_user = cfg.get("send_user") or cfg.get("email_user")
        send_pass = cfg.get("send_pass") or cfg.get("email_pass")
        if not send_user or not send_pass:
            messagebox.showerror("错误", "请填写发件人账号和密码")
            return
        try:
            self.log("正在测试SMTP连接...", "send")
            test_smtp_connection(cfg["smtp_server"], cfg["smtp_port"],
                                 cfg["smtp_ssl"], send_user, send_pass,
                                 cfg.get("skip_ssl_smtp", False))
            self.log("SMTP连接测试成功！", "send")
            messagebox.showinfo("连接成功", "SMTP服务器连接成功！")
        except Exception as e:
            self.log(f"SMTP连接测试失败: {e}", "send")
            messagebox.showerror("连接失败", f"SMTP连接失败:\n{e}")

    # ---------- 核心操作 ----------
    def _test_and_run(self):
        self._save_ui_config()
        cfg = self.config
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

        self.log("=" * 50, "download")
        self.log(f"开始执行...", "download")

        try:
            self.log(f"正在连接 {cfg['imap_server']}:{cfg['imap_port']} ...", "download")
            mail = connect_imap(cfg["imap_server"], cfg["imap_port"],
                                cfg["email_user"], cfg["email_pass"],
                                cfg.get("skip_ssl_verify", False))
            self.log("连接成功！", "download")
            filter_days = cfg.get("download_filter_days", [])
            filter_time_enabled = cfg.get("download_filter_time_enabled", False)
            filter_date_enabled = cfg.get("download_filter_date_enabled", False)
            read_status = cfg.get("download_read_status", "all")
            if filter_days or filter_time_enabled or filter_date_enabled:
                self.log(f"邮件筛选: 接收日={filter_days}, 时间段={'启用' if filter_time_enabled else '不启用'}, 日期范围={'启用' if filter_date_enabled else '不启用'}", "download")
            count = fetch_attachments(mail, filter_list,
                                      cfg["save_folder"],
                                      lambda msg: self.log(msg, "download"),
                                      keyword_filter=keyword_filter,
                                      read_status=read_status,
                                      filter_days=filter_days if filter_days else None,
                                      filter_time_enabled=filter_time_enabled,
                                      filter_time_start=cfg.get("download_filter_time_start", "00:00"),
                                      filter_time_end=cfg.get("download_filter_time_end", "23:59"),
                                      filter_date_enabled=filter_date_enabled,
                                      filter_date_start=cfg.get("download_filter_date_start", ""),
                                      filter_date_end=cfg.get("download_filter_date_end", ""))
            mail.logout()
            self.log(f"本次下载了 {count} 个附件", "download")
            if count == 0:
                self.log("没有新的匹配附件", "download")
        except imaplib.IMAP4.error as e:
            self.log(f"IMAP错误: {e}", "download")
            messagebox.showerror("连接失败",
                                 f"IMAP登录失败，请检查服务器/端口/账号/授权码。\n错误: {e}")
        except Exception as e:
            self.log(f"错误: {e}", "download")
            messagebox.showerror("执行出错", str(e))

        self.log("=" * 50, "download")

    def _test_send(self):
        self._save_ui_config()
        cfg = self.config
        send_user = cfg.get("send_user") or cfg["email_user"]
        send_pass = cfg.get("send_pass") or cfg["email_pass"]

        if not send_user or not send_pass:
            messagebox.showerror("错误", "请填写发件人账号和密码（或下载页的邮箱账号/密码）")
            return
        if not cfg["send_to"]:
            messagebox.showerror("错误", "请填写收件人")
            return

        self.log("=" * 50, "send")
        self.log(f"开始发送邮件...", "send")

        try:
            self.log(f"正在连接 {cfg['smtp_server']}:{cfg['smtp_port']} ...", "send")
            att_paths = self._resolve_attachment_paths(cfg)
            formatted_subject = self._format_subject(cfg.get("send_subject", ""))
            send_email(
                cfg["smtp_server"], cfg["smtp_port"], cfg["smtp_ssl"],
                send_user, send_pass,
                cfg["send_to"], formatted_subject,
                cfg.get("send_body", ""), att_paths,
                cfg.get("skip_ssl_smtp", False),
                lambda msg: self.log(msg, "send"),
                config=cfg
            )
            self.log("发送成功！", "send")
        except smtplib.SMTPAuthenticationError:
            self.log("SMTP错误: 认证失败，请检查账号/密码/授权码", "send")
            messagebox.showerror("发送失败", "SMTP认证失败，请检查账号/密码/授权码")
        except Exception as e:
            self.log(f"发送出错: {e}", "send")
            messagebox.showerror("发送出错", str(e))

        self.log("=" * 50, "send")

    # ---------- 邮件查询 (功能3) ----------
    def _do_mail_query(self):
        """执行邮件查询（后台线程，不阻塞 UI）"""
        if self.querying:
            return  # 防止重复点击

        self._save_download_config()
        cfg = self.config
        if not cfg["email_user"] or not cfg["email_pass"]:
            messagebox.showerror("错误", "请先在下载配置页填写IMAP邮箱账号和密码")
            return

        keyword = self.entry_query_keyword.get().strip()
        max_count = int(self.spin_query_count.get())
        folder = self.combo_query_folder.get()
        start_date = self.entry_query_start_date.get().strip() or None
        end_date = self.entry_query_end_date.get().strip() or None

        read_filter_map = {"全部": "all", "已读": "seen", "未读": "unseen"}
        read_filter = read_filter_map.get(self.combo_read_filter.get(), "all")

        # 标记查询中，禁用按钮
        self.querying = True
        self.btn_query_mail.config(state=tk.DISABLED, text="查询中...")
        self.btn_refresh_mail.config(state=tk.DISABLED, text="查询中...")
        self.btn_sent_mail.config(state=tk.DISABLED, text="查询中...")

        # 清除旧列表，显示加载提示
        for item in self.mail_tree.get_children():
            self.mail_tree.delete(item)
        self.query_detail_data = None
        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.insert(tk.END, "正在查询邮件，请稍候...")
        self.mail_detail_text.config(state=tk.DISABLED)

        self.log(f"正在查询 {folder}...", "query")

        # 在后台线程执行 IMAP 操作
        def _query_worker():
            try:
                imap_user = cfg["email_user"]
                imap_pass = cfg["email_pass"]
                actual_folder = folder
                if folder == "已发送":
                    sent_user = cfg.get("send_user") or cfg["email_user"]
                    sent_pass = cfg.get("send_pass") or cfg["email_pass"]
                    imap_user = sent_user
                    imap_pass = sent_pass

                mail = connect_imap(cfg["imap_server"], cfg["imap_port"],
                                    imap_user, imap_pass,
                                    cfg.get("skip_ssl_verify", False))

                criteria = "ALL"

                if folder == "已发送":
                    sent_folder = get_sent_folder_name(mail, log_func=lambda msg: self.root.after(0, lambda: self.log(msg, "query")))
                    if sent_folder:
                        actual_folder = sent_folder
                        self.root.after(0, lambda: self.log(f"检测到已发送文件夹: {sent_folder}", "query"))
                    else:
                        mail.logout()
                        self.root.after(0, lambda: self._query_failed("未找到已发送文件夹"))
                        return

                emails = query_emails(mail, actual_folder, criteria, max_count=max_count,
                                     log_func=lambda msg: self.root.after(0, lambda: self.log(msg, "query")),
                                     start_date=start_date,
                                     end_date=end_date,
                                     read_filter=read_filter)
                mail.logout()

                # 关键词本地过滤
                if keyword:
                    kw = keyword.lower()
                    search_body = self.var_search_body.get()
                    if search_body:
                        filtered = []
                        for e in emails:
                            if kw in (e.get("subject") or "").lower() or kw in (e.get("from") or "").lower():
                                filtered.append(e)
                                continue
                            detail = fetch_email_detail(mail, e["id"])
                            if detail and kw in (detail.get("body") or "").lower():
                                filtered.append(e)
                        emails = filtered
                    else:
                        emails = [e for e in emails if kw in (e.get("subject") or "").lower() or kw in (e.get("from") or "").lower()]

                # 回到主线程更新 UI
                self.root.after(0, lambda: self._query_success(emails, folder))
            except Exception as e:
                self.root.after(0, lambda: self._query_failed(str(e)))

        threading.Thread(target=_query_worker, daemon=True).start()

    def _query_success(self, emails, folder):
        """主线程：显示查询结果"""
        self.querying = False
        self.btn_query_mail.config(state=tk.NORMAL, text="查询")
        self.btn_refresh_mail.config(state=tk.NORMAL, text="刷新列表")
        self.btn_sent_mail.config(state=tk.NORMAL, text="查看已发送")

        # 清空旧缓存并保存本次查询原始数据
        self._query_results_cache = {em["id"]: em for em in emails}

        # 清除旧详情
        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.config(state=tk.DISABLED)

        # 填充列表（已在 _do_mail_query 中清空）
        for em in emails:
            person = em["from"] if folder != "已发送" else em["to"]
            status_text = "未读" if not em.get("seen") else "已读"
            tags = ("unseen",) if not em.get("seen") else ()
            self.mail_tree.insert("", tk.END, values=(status_text, person, em["subject"], em["date"]),
                                  iid=em["id"], tags=tags)

        self.log(f"查询完成，共 {len(emails)} 封邮件", "query")
        self.log("=" * 50, "query")

    def _query_failed(self, error_msg):
        """主线程：查询失败处理"""
        self.querying = False
        self.btn_query_mail.config(state=tk.NORMAL, text="查询")
        self.btn_refresh_mail.config(state=tk.NORMAL, text="刷新列表")
        self.btn_sent_mail.config(state=tk.NORMAL, text="查看已发送")

        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.insert(tk.END, f"查询失败:\n{error_msg}")
        self.mail_detail_text.config(state=tk.DISABLED)

        self.log(f"查询失败: {error_msg}", "query")
        self.log("=" * 50, "query")

        if "未找到已发送文件夹" not in error_msg:
            messagebox.showerror("查询失败", error_msg)

    def _do_sent_mail_query(self):
        """快捷查看已发送邮件"""
        self.combo_query_folder.set("已发送")
        self.combo_read_filter.set("全部")
        self._do_mail_query()

    def _on_mail_select(self, event):
        """选中邮件时显示详情"""
        self._update_selected_count()
        selection = self.mail_tree.selection()
        if not selection:
            return
        mail_id = selection[0]

        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.insert(tk.END, "正在加载邮件详情...")
        self.mail_detail_text.config(state=tk.DISABLED)

        # 后台线程获取详情（在生成线程前捕获 folder，避免线程内调用 Tkinter）
        folder = self.combo_query_folder.get()
        threading.Thread(target=self._fetch_mail_detail_thread, args=(mail_id, folder), daemon=True).start()

    def _fetch_mail_detail_thread(self, mail_id, query_folder):
        """后台线程获取邮件详情"""
        cfg = self.config
        try:
            imap_user = cfg["email_user"]
            imap_pass = cfg["email_pass"]
            folder = query_folder
            if folder == "已发送":
                sent_user = cfg.get("send_user") or cfg["email_user"]
                sent_pass = cfg.get("send_pass") or cfg["email_pass"]
                imap_user = sent_user
                imap_pass = sent_pass

            mail = connect_imap(cfg["imap_server"], cfg["imap_port"],
                                imap_user, imap_pass,
                                cfg.get("skip_ssl_verify", False))

            if folder == "已发送":
                sent_folder = get_sent_folder_name(mail)
                folder = sent_folder or "INBOX"
            elif folder != "INBOX":
                folder = "INBOX"

            # 必须先 select 进入 SELECTED 状态才能 FETCH
            if " " in folder or "/" in folder or any(ord(c) > 127 for c in folder):
                select_name = f'"{folder}"'
            else:
                select_name = folder
            try:
                mail.select(select_name)
            except Exception:
                mail.select(folder)

            detail = fetch_email_detail(mail, mail_id)
            mail.logout()

            self.root.after(0, lambda: self._show_mail_detail(detail))
        except Exception as e:
            self.root.after(0, lambda: self._show_mail_detail_error(str(e)))

    def _show_mail_detail(self, detail):
        """显示邮件详情"""
        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        if detail:
            self.query_detail_data = detail
            text = f"发件人: {detail['from']}\n"
            text += f"收件人: {detail['to']}\n"
            if detail.get('cc'):
                text += f"抄送: {detail['cc']}\n"
            text += f"主题: {detail['subject']}\n"
            text += f"日期: {detail['date']}\n"
            seen_text = "已读" if detail.get('seen') else "未读"
            text += f"状态: {seen_text}\n"
            text += f"{'─' * 40}\n"
            if detail.get('body'):
                text += detail['body']
            else:
                text += "(无文本正文，可能为HTML格式)"
            text += f"\n{'─' * 40}\n"
            if detail.get('attachments'):
                text += f"附件 ({len(detail['attachments'])} 个):\n"
                for att in detail['attachments']:
                    text += f"  - {att['filename']} ({att['size']} 字节)\n"
            else:
                text += "无附件"
            self.mail_detail_text.insert(tk.END, text)
        else:
            self.mail_detail_text.insert(tk.END, "无法获取邮件详情")
        self.mail_detail_text.config(state=tk.DISABLED)

    def _show_mail_detail_error(self, error):
        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.insert(tk.END, f"加载失败: {error}")
        self.mail_detail_text.config(state=tk.DISABLED)

    # ---------- 下载历史记录 ----------
    def _format_file_size(self, size_bytes):
        """将字节数转为人类可读的大小字符串"""
        if size_bytes < 0:
            return "-"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def _record_download(self, filename, subject, sender, save_path, size, status, email_uid=""):
        """记录一条下载历史"""
        try:
            record = {
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "filename": filename,
                "subject": subject[:500] if subject else "",
                "sender": sender[:200] if sender else "",
                "save_path": save_path,
                "size": size,
                "status": status,
                "email_uid": str(email_uid) if email_uid else ""
            }
            add_history_record(record)
            # 异步刷新历史列表
            self.root.after(100, self._refresh_history_list)
        except Exception as e:
            self.log(f"记录下载历史失败: {e}", "system")

    def _refresh_history_list(self, keyword="", date_from="", date_to=""):
        """刷新历史记录 Treeview，支持搜索和日期筛选"""
        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)
        records = load_history()
        # 过滤
        filtered = []
        kw_lower = keyword.lower().strip()
        df = date_from.strip()
        dt = date_to.strip()
        for r in records:
            if kw_lower:
                match_kw = False
                for field in ("filename", "subject", "sender", "save_path"):
                    if kw_lower in r.get(field, "").lower():
                        match_kw = True
                        break
                if not match_kw:
                    continue
            if df:
                if r.get("time", "")[:10] < df:
                    continue
            if dt:
                if r.get("time", "")[:10] > dt:
                    continue
            filtered.append(r)

        for r in filtered:
            size_str = self._format_file_size(r.get("size", -1))
            status_display = "✓" if r.get("status") == "success" else "✗"
            self.hist_tree.insert("", tk.END, values=(
                r.get("time", ""),
                r.get("filename", ""),
                r.get("subject", ""),
                r.get("sender", ""),
                r.get("save_path", ""),
                size_str,
                status_display
            ))

        total = len(records)
        shown = len(filtered)
        if keyword or df or dt:
            self.hist_status_label.config(text=f"显示 {shown} / {total} 条记录（已筛选）")
        else:
            self.hist_status_label.config(text=f"共 {total} 条记录（上限 {MAX_HISTORY_RECORDS} 条）")

        success_count = sum(1 for r in filtered if r.get("status") == "success")
        failed_count = shown - success_count
        self.hist_stat_label.config(text=f"成功: {success_count} 条  |  失败: {failed_count} 条")

    def _on_history_search(self, *args):
        """搜索框输入实时过滤"""
        self._refresh_history_list(
            keyword=self.hist_search_var.get(),
            date_from=self.entry_hist_date_from.get().strip(),
            date_to=self.entry_hist_date_to.get().strip()
        )

    def _on_history_date_filter(self):
        """日期筛选按钮"""
        self._refresh_history_list(
            keyword=self.hist_search_var.get(),
            date_from=self.entry_hist_date_from.get().strip(),
            date_to=self.entry_hist_date_to.get().strip()
        )

    def _on_history_reset_filter(self):
        """重置筛选条件"""
        self.hist_search_var.set("")
        self.entry_hist_date_from.delete(0, tk.END)
        self.entry_hist_date_to.delete(0, tk.END)
        self._refresh_history_list()

    def _on_open_history_folder(self):
        """打开选中记录的保存目录"""
        selection = self.hist_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一条记录")
            return
        item = selection[0]
        values = self.hist_tree.item(item, "values")
        save_path = values[4] if values else ""
        if not save_path:
            messagebox.showinfo("提示", "该记录无保存路径")
            return
        folder = os.path.dirname(save_path)
        if os.path.isdir(folder):
            os.startfile(folder)
        else:
            messagebox.showwarning("提示", f"目录不存在:\n{folder}")

    def _on_clear_history(self):
        """清空历史确认"""
        if not messagebox.askyesno("确认清空", "确定要清空所有下载历史记录吗？\n\n此操作不可恢复。"):
            return
        clear_history()
        self._refresh_history_list()
        self.log("下载历史已清空", "system")

    def _download_query_attachment(self):
        """下载查询邮件中的附件"""
        if not self.query_detail_data or not self.query_detail_data.get("attachments"):
            messagebox.showinfo("提示", "此邮件无附件")
            return
        folder = filedialog.askdirectory(title="选择附件保存目录")
        if not folder:
            return
        count = 0
        detail = self.query_detail_data
        subject = detail.get("subject", "")
        sender = detail.get("sender", "")
        email_uid = detail.get("email_uid", "")
        for att in detail["attachments"]:
            filename = clean_filename(att["filename"])
            filepath = os.path.join(folder, filename)
            if os.path.exists(filepath):
                base, ext = os.path.splitext(filename)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filepath = os.path.join(folder, f"{base}_{ts}{ext}")
            try:
                with open(filepath, "wb") as f:
                    f.write(att["payload"])
                actual_size = os.path.getsize(filepath)
                self._record_download(
                    filename=os.path.basename(filepath),
                    subject=subject,
                    sender=sender,
                    save_path=filepath,
                    size=actual_size,
                    status="success",
                    email_uid=email_uid
                )
                self.log(f"已下载附件: {filename}", "query")
                count += 1
            except Exception as e:
                self._record_download(
                    filename=att.get("filename", "unknown"),
                    subject=subject,
                    sender=sender,
                    save_path=filepath,
                    size=len(att.get("payload", b"")),
                    status="failed",
                    email_uid=email_uid
                )
                self.log(f"下载附件失败: {filename} — {e}", "query")
        messagebox.showinfo("下载完成", f"成功下载 {count} 个附件到:\n{folder}")
        self.log(f"本次下载了 {count} 个附件", "query")

    def _update_selected_count(self):
        count = len(self.mail_tree.selection())
        if count > 0:
            self.lbl_selected_count.config(text=f"已选 {count} 封")
        else:
            self.lbl_selected_count.config(text="")

    def _select_all_mails(self):
        for item in self.mail_tree.get_children():
            self.mail_tree.selection_add(item)
        self._update_selected_count()

    def _deselect_all_mails(self):
        self.mail_tree.selection_remove(*self.mail_tree.selection())
        self._update_selected_count()

    def _batch_download_query_attachments(self):
        selection = self.mail_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先在左侧列表中选中要下载的邮件（支持 Ctrl+点击 多选）")
            return

        folder = filedialog.askdirectory(title="选择批量下载附件保存目录")
        if not folder:
            return

        query_folder = self.combo_query_folder.get()
        cfg = self.config
        total_mails = len(selection)
        self.log("=" * 50, "query")
        self.log(f"开始批量下载: {total_mails} 封邮件的附件...", "query")
        self.btn_batch_download.config(state=tk.DISABLED, text="下载中...")

        def _batch_worker():
            total_attachments = 0
            success_mails = 0
            failed_mails = 0
            mail = None
            try:
                imap_user = cfg["email_user"]
                imap_pass = cfg["email_pass"]
                if query_folder == "已发送":
                    sent_user = cfg.get("send_user") or cfg["email_user"]
                    sent_pass = cfg.get("send_pass") or cfg["email_pass"]
                    imap_user = sent_user
                    imap_pass = sent_pass

                mail = connect_imap(cfg["imap_server"], cfg["imap_port"],
                                    imap_user, imap_pass,
                                    cfg.get("skip_ssl_verify", False))

                actual_folder = query_folder
                if query_folder == "已发送":
                    sent_folder = get_sent_folder_name(mail,
                        log_func=lambda msg: self.root.after(0, lambda: self.log(msg, "query")))
                    actual_folder = sent_folder or "INBOX"

                if " " in actual_folder or "/" in actual_folder or any(ord(c) > 127 for c in actual_folder):
                    select_name = f'"{actual_folder}"'
                else:
                    select_name = actual_folder
                try:
                    mail.select(select_name)
                except Exception:
                    mail.select(actual_folder)

                details, fetch_failed = batch_fetch_email_details(mail, list(selection),
                    log_func=lambda msg: self.root.after(0, lambda: self.log(msg, "query")))
                failed_mails = fetch_failed

                for detail in details:
                    detail_subject = detail.get("subject", "")
                    detail_sender = detail.get("sender", "")
                    detail_uid = detail.get("email_uid", "")
                    if detail.get("attachments"):
                        mail_att_count = 0
                        for att in detail["attachments"]:
                            filename = clean_filename(att["filename"])
                            filepath = os.path.join(folder, filename)
                            if os.path.exists(filepath):
                                base, ext = os.path.splitext(filename)
                                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                                filepath = os.path.join(folder, f"{base}_{ts}{ext}")
                            try:
                                with open(filepath, "wb") as f:
                                    f.write(att["payload"])
                                actual_size = os.path.getsize(filepath)
                                self._record_download(
                                    filename=os.path.basename(filepath),
                                    subject=detail_subject,
                                    sender=detail_sender,
                                    save_path=filepath,
                                    size=actual_size,
                                    status="success",
                                    email_uid=detail_uid
                                )
                                mail_att_count += 1
                                total_attachments += 1
                            except Exception as ex:
                                self._record_download(
                                    filename=att.get("filename", "unknown"),
                                    subject=detail_subject,
                                    sender=detail_sender,
                                    save_path=filepath,
                                    size=len(att.get("payload", b"")),
                                    status="failed",
                                    email_uid=detail_uid
                                )
                                self.root.after(0, lambda a=att, e=ex:
                                    self.log(f"  ✗ 下载失败: {a.get('filename','?')[:40]} — {e}", "query"))
                        self.root.after(0, lambda d=detail, c=mail_att_count:
                            self.log(f"  ✓ {d['subject'][:40]} — {c} 个附件", "query"))
                        success_mails += 1
                    else:
                        self.root.after(0, lambda d=detail:
                            self.log(f"  - {d['subject'][:40]} — 无附件", "query"))
                        success_mails += 1

                self.root.after(0, lambda: self._batch_download_done(
                    success_mails, total_mails, total_attachments, folder, failed_mails))
            except imaplib.IMAP4.error:
                self.root.after(0, lambda: self._batch_download_failed(
                    "IMAP连接或认证失败，请检查服务器地址和密码"))
            except Exception:
                self.root.after(0, lambda: self._batch_download_failed(
                    f"批量下载出错，请检查网络连接后重试"))
            finally:
                if mail is not None:
                    try:
                        mail.logout()
                    except Exception:
                        pass

        threading.Thread(target=_batch_worker, daemon=True).start()

    def _batch_download_done(self, success_mails, total_mails, total_attachments, folder, failed_mails=0):
        self.btn_batch_download.config(state=tk.NORMAL, text="批量下载所选附件")
        status_parts = [f"处理邮件: {success_mails}/{total_mails} 封"]
        if failed_mails > 0:
            status_parts.append(f"获取失败: {failed_mails} 封")
        status_parts.append(f"下载附件: {total_attachments} 个")
        self.log(f"批量下载完成: {success_mails}/{total_mails} 封邮件, 共 {total_attachments} 个附件", "query")
        self.log("=" * 50, "query")
        msg = f"批量下载完成！\n\n" + "\n".join(status_parts) + f"\n保存目录: {folder}"
        if total_attachments == 0:
            msg += "\n\n(所选邮件均无附件)"
        messagebox.showinfo("批量下载完成", msg)

    def _batch_download_failed(self, error_msg):
        self.btn_batch_download.config(state=tk.NORMAL, text="批量下载所选附件")
        self.log(f"批量下载失败: {error_msg}", "query")
        self.log("=" * 50, "query")
        messagebox.showerror("批量下载失败", f"批量下载出错:\n{error_msg}")

    def _mark_selected_as_read(self):
        """将当前选中的未读邮件标记为已读（支持多选）"""
        selection = self.mail_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中邮件")
            return

        unseen_ids = [mid for mid in selection
                      if self.mail_tree.item(mid, "values") and self.mail_tree.item(mid, "values")[0] == "未读"]
        if not unseen_ids:
            messagebox.showinfo("提示", "所选邮件均已读")
            return

        folder = self.combo_query_folder.get()
        cfg = self.config

        def _mark_worker():
            try:
                imap_user = cfg["email_user"]
                imap_pass = cfg["email_pass"]
                actual_folder = folder
                if folder == "已发送":
                    sent_user = cfg.get("send_user") or cfg["email_user"]
                    sent_pass = cfg.get("send_pass") or cfg["email_pass"]
                    imap_user = sent_user
                    imap_pass = sent_pass

                mail = connect_imap(cfg["imap_server"], cfg["imap_port"],
                                    imap_user, imap_pass,
                                    cfg.get("skip_ssl_verify", False))

                if folder == "已发送":
                    sent_folder = get_sent_folder_name(mail)
                    actual_folder = sent_folder or "INBOX"

                success_count = 0
                for mail_id in unseen_ids:
                    if mark_email_as_read(mail, actual_folder, mail_id,
                                          log_func=lambda msg, mid=mail_id: self.root.after(0, lambda: self.log(msg, "query"))):
                        success_count += 1
                        self.root.after(0, lambda mid=mail_id: self._on_mark_read_success(mid))
                mail.logout()
                self.root.after(0, lambda: self.log(f"批量标记已读完成: {success_count}/{len(unseen_ids)} 封", "query"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("错误", f"标记已读失败: {e}"))

        threading.Thread(target=_mark_worker, daemon=True).start()

    def _on_mark_read_success(self, mail_id):
        """标记已读成功后更新 UI"""
        values = list(self.mail_tree.item(mail_id, "values"))
        if values:
            values[0] = "已读"
            self.mail_tree.item(mail_id, values=tuple(values), tags=())
        if self.query_detail_data:
            self.query_detail_data["seen"] = True
            self._show_mail_detail(self.query_detail_data)
        self.log(f"邮件 {mail_id} 已标记为已读", "query")

    def _sort_treeview(self, col):
        if self._tree_sort_col == col:
            self._tree_sort_reverse = not self._tree_sort_reverse
        else:
            self._tree_sort_reverse = False
        self._tree_sort_col = col
        col_idx = {"状态": 0, "发件人/收件人": 1, "主题": 2, "日期": 3}
        idx = col_idx.get(col, 0)
        items = [(self.mail_tree.set(k, col), k) for k in self.mail_tree.get_children("")]
        items.sort(reverse=self._tree_sort_reverse, key=lambda x: x[0].lower())
        for i, (_, item) in enumerate(items):
            self.mail_tree.move(item, "", i)

    def _on_tree_right_click(self, event):
        iid = self.mail_tree.identify_row(event.y)
        if iid:
            if iid not in self.mail_tree.selection():
                self.mail_tree.selection_set(iid)
            self._tree_context_menu.tk_popup(event.x_root, event.y_root)

    def _export_query_csv(self):
        children = self.mail_tree.get_children()
        if not children:
            messagebox.showinfo("提示", "查询列表为空，请先查询邮件")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV文件", "*.csv")],
            initialfile=f"邮件查询结果_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["状态", "发件人/收件人", "主题", "日期"])
            for item in children:
                writer.writerow(self.mail_tree.item(item, "values"))
        self.log(f"查询结果已导出到 {path}", "query")

    def _export_query_excel(self) -> None:
        """将邮件查询结果导出为 Excel 文件。

        从 mail_tree 和 _query_results_cache 构建 6 列数据 dict，
        调用 export_utils.export_to_excel 写入文件。
        """
        children = self.mail_tree.get_children()
        if not children:
            messagebox.showinfo("提示", "查询列表为空，请先查询邮件")
            return

        path: str = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel文件", "*.xlsx")],
            initialfile=(
                f"邮件查询结果_{datetime.datetime.now():%Y%m%d_%H%M%S}.xlsx"
            ),
        )
        if not path:
            return

        columns = ["状态", "发件人/收件人", "主题", "日期", "有无附件", "附件数量"]
        data: list[dict[str, str]] = []

        for item in children:
            values = self.mail_tree.item(item, "values")
            mail_id: str = str(item)

            cached = self._query_results_cache.get(mail_id, {})

            has_attachments_str: str = "—"
            attachment_count_str: str = "—"

            att_list = cached.get("attachments")
            if att_list is not None and isinstance(att_list, list):
                if len(att_list) > 0:
                    has_attachments_str = "有"
                    attachment_count_str = str(len(att_list))
                else:
                    has_attachments_str = "无"
                    attachment_count_str = "0"

            data.append({
                "状态": values[0] if len(values) > 0 else "",
                "发件人/收件人": values[1] if len(values) > 1 else "",
                "主题": values[2] if len(values) > 2 else "",
                "日期": values[3] if len(values) > 3 else "",
                "有无附件": has_attachments_str,
                "附件数量": attachment_count_str,
            })

        try:
            export_to_excel(data, path, columns=columns, sheet_title="邮件查询结果")
        except PermissionError:
            messagebox.showerror(
                "导出失败",
                "无法写入文件，该文件可能已被其他程序占用。\n请关闭文件后重试。",
            )
            return
        except OSError as e:
            messagebox.showerror("导出失败", f"写入文件失败，请检查磁盘空间。\n{e}")
            return

        self.log(f"查询结果已导出为Excel -> {path}", "query")

    # ---------- 定时调度 ----------
    def _toggle_scheduler(self):
        if self.running:
            self._stop_scheduler()
        else:
            self._start_scheduler()

    def _start_scheduler(self):
        self._save_ui_config()
        cfg = self.config

        dl_enabled = cfg.get("schedule_download", {}).get("enabled", False)
        sd_enabled = cfg.get("schedule_send", {}).get("enabled", False)

        if not dl_enabled and not sd_enabled:
            messagebox.showerror("错误", "请至少启用一个定时任务（下载或发送）")
            return

        # 建议E：检查是否勾选了星期几
        warnings = []
        if dl_enabled:
            dl_days = cfg["schedule_download"]["days"]
            if not dl_days:
                warnings.append("下载定时任务已启用但未勾选任何执行日期，任务永远不会触发")
        if sd_enabled:
            sd_days = cfg["schedule_send"]["days"]
            if not sd_days:
                warnings.append("发送定时任务已启用但未勾选任何执行日期，任务永远不会触发")
        if warnings:
            warn_msg = "\n".join(warnings)
            if not messagebox.askyesno("警告", warn_msg + "\n\n仍然启动？"):
                return

        if dl_enabled:
            if not cfg.get("email_user") or not cfg.get("email_pass"):
                messagebox.showerror("错误", "下载任务已启用，请填写邮箱账号和密码")
                return
            filter_list = cfg.get("sender_filter_list", [])
            keyword_filter = cfg.get("download_keyword_filter", "").strip()
            if not filter_list and not keyword_filter:
                messagebox.showerror("错误", "下载任务已启用，请填写发件人筛选或主题关键词（至少填一个）")
                return
            if not cfg.get("save_folder"):
                messagebox.showerror("错误", "下载任务已启用，请选择附件保存目录")
                return
            os.makedirs(cfg["save_folder"], exist_ok=True)

        if sd_enabled:
            send_user = cfg.get("send_user") or cfg.get("email_user")
            send_pass = cfg.get("send_pass") or cfg.get("email_pass")
            if not send_user or not send_pass:
                messagebox.showerror("错误", "发送任务已启用，请填写发件人账号和密码")
                return
            if not cfg.get("send_to"):
                messagebox.showerror("错误", "发送任务已启用，请填写收件人")
                return

        self.running = True
        self.stop_event.clear()
        self.btn_start.config(text="停止定时任务")
        self.status_label.config(text="状态: 运行中", foreground="green")
        self.retry_state = {}

        self.log("=" * 50)
        self.log("定时任务已启动")
        if dl_enabled:
            dl = cfg["schedule_download"]
            day_names = [self.WEEKDAY_NAMES[d - 1] for d in dl["days"]]
            self.log(f"  [下载] {', '.join(day_names)} {dl['hour']:02d}:{dl['minute']:02d}:{dl['second']:02d}")
        if sd_enabled:
            sd = cfg["schedule_send"]
            day_names = [self.WEEKDAY_NAMES[d - 1] for d in sd["days"]]
            self.log(f"  [发送] {', '.join(day_names)} {sd['hour']:02d}:{sd['minute']:02d}:{sd['second']:02d}")
            self.log(f"  收件人: {cfg['send_to']}")
        if cfg.get("log_export_enabled"):
            ed = cfg.get("log_export_days", [])
            if ed:
                dn = [self.WEEKDAY_NAMES[d - 1] for d in ed]
                self.log(f"  [日志导出] {', '.join(dn)} {cfg.get('log_export_hour',23):02d}:{cfg.get('log_export_minute',59):02d}:{cfg.get('log_export_second',0):02d}")
        if cfg.get("retry_enabled"):
            self.log(f"  [重试] 启用，最多{cfg.get('retry_count',3)}次，间隔{cfg.get('retry_interval_minutes',5)}分钟")
        self.log("=" * 50)

        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()

    def _stop_scheduler(self):
        self.running = False
        self.stop_event.set()
        self.btn_start.config(text="启动定时任务")
        self.status_label.config(text="状态: 已停止", foreground="gray")
        self.log("定时任务已停止")

    def _scheduler_loop(self):
        cfg = self.config
        last_dl_date = None
        last_sd_date = None
        last_export_date = None

        while not self.stop_event.is_set():
            now = datetime.datetime.now()
            today = now.date()
            weekday = now.isoweekday()

            # --- 下载任务 ---
            dl = cfg.get("schedule_download", {})
            if dl.get("enabled") and dl.get("days"):
                if (weekday in set(dl["days"])
                        and now.hour == dl.get("hour", 0)
                        and now.minute == dl.get("minute", 0)
                        and now.second == dl.get("second", 0)
                        and last_dl_date != today):
                    last_dl_date = today
                    self.root.after(0, self._execute_download)

            # --- 发送任务 ---
            sd = cfg.get("schedule_send", {})
            if sd.get("enabled") and sd.get("days"):
                if (weekday in set(sd["days"])
                        and now.hour == sd.get("hour", 0)
                        and now.minute == sd.get("minute", 0)
                        and now.second == sd.get("second", 0)
                        and last_sd_date != today):
                    last_sd_date = today
                    self.root.after(0, self._execute_send)

            # --- 日志导出任务 ---
            if (cfg.get("log_export_enabled")
                    and cfg.get("log_export_days", [])
                    and weekday in set(cfg["log_export_days"])
                    and now.hour == cfg.get("log_export_hour", 23)
                    and now.minute == cfg.get("log_export_minute", 59)
                    and now.second == cfg.get("log_export_second", 0)
                    and last_export_date != today):
                last_export_date = today
                self.root.after(0, self._auto_export_log)

            # --- 重试检查 (建议C) ---
            if cfg.get("retry_enabled"):
                self._check_retry()

            time.sleep(0.5)

    def _check_retry(self):
        """检查是否有待重试的任务"""
        cfg = self.config
        now = time.time()
        for task_type in list(self.retry_state.keys()):
            state = self.retry_state[task_type]
            if state["fail_count"] >= cfg.get("retry_count", 3):
                # 超过重试次数，放弃
                del self.retry_state[task_type]
                continue
            elapsed = (now - state["last_fail"]) / 60.0
            if elapsed >= cfg.get("retry_interval_minutes", 5):
                self.retry_state[task_type]["last_fail"] = now
                self.root.after(0, lambda t=task_type: self._retry_task(t))

    def _retry_task(self, task_type):
        """执行重试任务"""
        state = self.retry_state.get(task_type, {})
        fail_count = state.get("fail_count", 0)
        self.log(f"正在重试{task_type}任务 (第{fail_count}次失败后)...", task_type)
        if task_type == "download":
            self._execute_download()
        elif task_type == "send":
            self._execute_send()

    def _execute_download(self):
        cfg = self.config
        self.log("=" * 50, "download")
        self.log(f"定时任务触发，开始下载...", "download")

        try:
            mail = connect_imap(cfg["imap_server"], cfg["imap_port"],
                                cfg["email_user"], cfg["email_pass"],
                                cfg.get("skip_ssl_verify", False))
            filter_list = cfg.get("sender_filter_list", [])
            filter_days = cfg.get("download_filter_days", [])
            filter_time_enabled = cfg.get("download_filter_time_enabled", False)
            filter_date_enabled = cfg.get("download_filter_date_enabled", False)
            keyword_filter = cfg.get("download_keyword_filter", "").strip()
            read_status = cfg.get("download_read_status", "all")
            if filter_days or filter_time_enabled or filter_date_enabled:
                self.log(f"邮件筛选: 接收日={filter_days}, 时间段={'启用' if filter_time_enabled else '不启用'}, 日期范围={'启用' if filter_date_enabled else '不启用'}", "download")
            count = fetch_attachments(mail, filter_list,
                                      cfg["save_folder"],
                                      lambda msg: self.log(msg, "download"),
                                      keyword_filter=keyword_filter,
                                      read_status=read_status,
                                      filter_days=filter_days if filter_days else None,
                                      filter_time_enabled=filter_time_enabled,
                                      filter_time_start=cfg.get("download_filter_time_start", "00:00"),
                                      filter_time_end=cfg.get("download_filter_time_end", "23:59"),
                                      filter_date_enabled=filter_date_enabled,
                                      filter_date_start=cfg.get("download_filter_date_start", ""),
                                      filter_date_end=cfg.get("download_filter_date_end", ""))
            mail.logout()
            self.log(f"本次下载了 {count} 个附件", "download")
            if count == 0:
                self.log("没有新的匹配附件", "download")
            # 成功后清除重试状态
            self.retry_state.pop("download", None)
        except Exception as e:
            error_msg = f"下载任务执行出错: {e}"
            self.log(error_msg, "download")
            self._handle_task_error("download", error_msg)
            # 记录重试状态
            if "download" not in self.retry_state:
                self.retry_state["download"] = {"fail_count": 0, "last_fail": time.time()}
            else:
                self.retry_state["download"]["fail_count"] += 1
                self.retry_state["download"]["last_fail"] = time.time()

        self.log("=" * 50, "download")

    def _execute_send(self):
        cfg = self.config
        self.log("=" * 50, "send")
        self.log(f"定时发送触发...", "send")

        send_user = cfg.get("send_user") or cfg["email_user"]
        send_pass = cfg.get("send_pass") or cfg["email_pass"]

        try:
            self.log(f"正在连接 {cfg['smtp_server']}:{cfg['smtp_port']} ...", "send")
            att_paths = self._resolve_attachment_paths(cfg)
            formatted_subject = self._format_subject(cfg.get("send_subject", ""))
            send_email(
                cfg["smtp_server"], cfg["smtp_port"], cfg["smtp_ssl"],
                send_user, send_pass,
                cfg["send_to"], formatted_subject,
                cfg.get("send_body", ""), att_paths,
                cfg.get("skip_ssl_smtp", False),
                lambda msg: self.log(msg, "send"),
                config=cfg
            )
            self.log("发送成功！", "send")
            self.retry_state.pop("send", None)
        except Exception as e:
            error_msg = f"发送任务执行出错: {e}"
            self.log(error_msg, "send")
            self._handle_task_error("send", error_msg)
            if "send" not in self.retry_state:
                self.retry_state["send"] = {"fail_count": 0, "last_fail": time.time()}
            else:
                self.retry_state["send"]["fail_count"] += 1
                self.retry_state["send"]["last_fail"] = time.time()

        self.log("=" * 50, "send")

    # ---------- 失败告警 (功能4) ----------
    def _handle_task_error(self, task_type, error_msg):
        """弹出失败告警弹窗 + 托盘通知"""
        error_key = f"{task_type}_{error_msg[:50]}"
        now = time.time()

        # 去重：同一类错误5分钟内只弹一次
        if error_key in self.last_error_time:
            if now - self.last_error_time[error_key] < 300:
                return
        self.last_error_time[error_key] = now

        # UI弹窗
        self.root.after(100, lambda: messagebox.showerror(
            f"定时任务失败 - {task_type}",
            f"{task_type}任务执行失败，请检查运行日志。\n\n错误信息: {error_msg[:200]}"
        ))

        # 托盘气泡通知
        if self.tray_icon:
            try:
                self.tray_icon.notify(
                    f"【邮件工具】{task_type}任务失败",
                    f" {error_msg[:100]}"
                )
            except Exception:
                pass

    # ---------- 日志（支持分类,筛选,导出,持久化） ----------
    LOG_CATEGORIES = {"download": "[下载]", "send": "[发送]", "system": "[系统]", "query": "[查询]"}

    def log(self, msg, cat="system"):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = self.LOG_CATEGORIES.get(cat, "[系统]")
        full = f"{prefix} {ts}  {msg}" if cat != "system" else msg
        self.log_entries.append({"time": ts, "cat": cat, "msg": msg})
        self._render_log(full)
        # 持久化日志 (建议F)
        persist_log_entries(self.log_entries)

    def _render_log(self, line):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _render_persisted_log(self):
        """启动时渲染已持久化的日志"""
        for entry in self.log_entries:
            ts = entry["time"]
            prefix = self.LOG_CATEGORIES.get(entry["cat"], "[系统]")
            line = f"{prefix} {ts}  {entry['msg']}" if entry["cat"] != "system" else entry["msg"]
            self._render_log(line)

    def _clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.log_entries.clear()
        # 同时清空持久化文件
        persist_log_entries([])

    def _apply_log_filter(self, category):
        self.log_filter = category
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        for entry in self.log_entries:
            if category == "all" or entry["cat"] == category:
                ts = entry["time"]
                prefix = self.LOG_CATEGORIES.get(entry["cat"], "[系统]")
                line = f"{prefix} {ts}  {entry['msg']}" if entry["cat"] != "system" else entry["msg"]
                self._render_log(line)

    def _export_log(self):
        entries = self.log_entries
        if self.log_filter != "all":
            entries = [e for e in self.log_entries if e["cat"] == self.log_filter]
        if not entries:
            messagebox.showinfo("提示", "当前筛选条件下无日志可导出")
            return
        path = filedialog.asksaveasfilename(
            title="导出日志", defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile=f"邮件工具日志_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                ts = entry["time"]
                prefix = self.LOG_CATEGORIES.get(entry["cat"], "[系统]")
                line = f"{prefix} {ts}  {entry['msg']}" if entry["cat"] != "system" else entry["msg"]
                f.write(line + "\n")
        self.log(f"日志已导出 -> {path}")
        messagebox.showinfo("导出完成", f"日志已保存到:\n{path}")

    def _auto_export_log(self):
        cfg = self.config
        folder = cfg.get("log_export_folder", "")
        if not folder:
            folder = os.path.dirname(CONFIG_FILE)
        os.makedirs(folder, exist_ok=True)
        save_path = os.path.join(folder,
            f"邮件工具日志_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        with open(save_path, "w", encoding="utf-8") as f:
            for entry in self.log_entries:
                ts = entry["time"]
                prefix = self.LOG_CATEGORIES.get(entry["cat"], "[系统]")
                line = f"{prefix} {ts}  {entry['msg']}" if entry["cat"] != "system" else entry["msg"]
                f.write(line + "\n")
        self.log(f"日志已定时导出 -> {save_path}")

    def _browse_export_folder(self):
        folder = filedialog.askdirectory(title="选择日志导出目录")
        if folder:
            self.entry_export_folder.delete(0, tk.END)
            self.entry_export_folder.insert(0, folder)

    # ---------- 配置导入导出 (建议G) ----------
    def _export_config_btn(self):
        """导出配置到文件"""
        self._save_ui_config()
        path = filedialog.asksaveasfilename(
            title="导出配置文件", defaultextension=".json",
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")],
            initialfile=f"邮件工具配置_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        if not path:
            return
        try:
            export_config_to(self.config, path)
            self.log(f"配置已导出 -> {path}")
            messagebox.showinfo("导出成功", f"配置已导出到:\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _import_config_btn(self):
        """从文件导入配置"""
        path = filedialog.askopenfilename(
            title="导入配置文件",
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")]
        )
        if not path:
            return
        if not messagebox.askyesno("确认导入", "导入配置将覆盖当前设置，是否继续？"):
            return
        try:
            new_config = import_config_from(path)
            self.config = new_config
            save_config(self.config)
            self._load_config_to_ui()
            self.log(f"配置已导入 -> {path}")
            messagebox.showinfo("导入成功", f"配置已从文件导入，请检查各项设置。")
        except Exception as e:
            messagebox.showerror("导入失败", f"无法读取配置文件:\n{e}")

    # ---------- 系统托盘 ----------
    def _create_tray_image(self):
        img = Image.new("RGB", (64, 64), (0, 120, 212))
        draw = ImageDraw.Draw(img)
        draw.rectangle([8, 18, 56, 46], fill="white", outline="white")
        draw.polygon([(8, 18), (32, 32), (56, 18)], fill=(0, 120, 212))
        draw.polygon([(8, 46), (32, 32), (56, 46)], fill="white")
        draw.ellipse([20, 22, 44, 42], outline=(0, 120, 212), width=2)
        draw.text((24, 26), "M", fill=(0, 120, 212))
        return img

    def _show_window(self, icon=None):
        self.root.after(0, self._restore_window)

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _hide_to_tray(self):
        self.root.withdraw()
        if self.tray_icon is None:
            self.tray_icon = pystray.Icon(
                "mail_tool",
                self._create_tray_image(),
                "邮件自动化工具HK",
                menu=pystray.Menu(
                    pystray.MenuItem("显示窗口", self._show_window, default=True),
                    pystray.MenuItem("退出程序", self._tray_exit)
                )
            )
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _tray_exit(self, icon=None):
        if self.tray_icon:
            self.tray_icon.stop()
        if self.running:
            self._stop_scheduler()
        # 退出前持久化日志
        persist_log_entries(self.log_entries)
        self.root.after(0, self.root.destroy)

    # ---------- 关闭 ----------
    def on_close(self):
        self.log(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 窗口已最小化到系统托盘，程序在后台运行中")
        self._hide_to_tray()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()


if __name__ == "__main__":
    app = MailAttachmentTool()
    app.run()