"""
邮件附件自动下载 & 定时发送工具
- 定时下载指定发件人的邮件附件
- 定时发送邮件（带附件）给指定收件人
"""
import imaplib
import smtplib
import email
import os
import re
import json
import ssl
import threading
import time
import datetime
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formatdate, make_msgid, parsedate_to_datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# 系统托盘
import pystray
from PIL import Image, ImageDraw

# ==================== 配置管理 ====================
CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    # === 下载配置 ===
    "imap_server": "",
    "imap_port": 993,
    "email_user": "",
    "email_pass": "",
    "sender_filter_list": [],    # 发件人筛选列表，一行一个
    "save_folder": "",
    "skip_ssl_verify": False,
    "schedule_download": {
        "days": [],
        "hour": 9,
        "minute": 0,
        "second": 0,
        "enabled": False
    },
    # === 邮件下载时间段过滤（可选） ===
    "download_filter_days": [],           # 仅下载这些星期几的邮件，空=不限
    "download_filter_time_enabled": False, # 启用时间段过滤
    "download_filter_time_start": "00:00", # 起始时间
    "download_filter_time_end": "23:59",   # 截止时间
    # === 邮件下载日期范围过滤（可选） ===
    "download_filter_date_enabled": False,  # 启用日期范围过滤
    "download_filter_date_start": "",       # 起始日期 YYYY-MM-DD
    "download_filter_date_end": "",         # 截止日期 YYYY-MM-DD
    # === 发送配置 ===
    "smtp_server": "",
    "smtp_port": 465,
    "smtp_ssl": True,
    "skip_ssl_smtp": False,
    "send_user": "",
    "send_pass": "",
    "send_to": "",
    "send_subject": "",
    "send_body": "",
    "send_attachment_mode": "single",  # single / multi / folder
    "send_attachment_list": [],         # 多文件或文件夹模式下的路径列表
    "send_attachment": "",             # 单文件模式（兼容旧配置）
    "schedule_send": {
        "days": [],
        "hour": 8,
        "minute": 0,
        "second": 0,
        "enabled": False
    },
    # === 日志导出 ===
    "log_export_enabled": False,
    "log_export_folder": "",
    "log_export_days": [],
    "log_export_hour": 23,
    "log_export_minute": 59,
    "log_export_second": 0,
}


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return DEFAULT_CONFIG.copy()


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ==================== 邮件处理核心 ====================
def decode_str(s):
    """解码邮件头"""
    if s is None:
        return ""
    decoded_parts = decode_header(s)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(charset or "utf-8", errors="replace"))
            except Exception:
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def clean_filename(name):
    """清理文件名中的非法字符"""
    return re.sub(r'[\\/:*?"<>|]', "_", name)


# ==================== SSL兼容处理 ====================
def _create_ssl_context(skip_verify=False):
    """创建兼容的SSL上下文，解决Python 3.12+ SSL handshake failure"""
    # 使用PROTOCOL_TLS_CLIENT自动协商最佳协议
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # 兼容旧服务器的cipher suite
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    # 允许传统重协商（Coremail等旧服务器需要）
    ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    ctx.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3  # 禁用不安全协议

    if skip_verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def connect_imap(server, port, user, password, skip_ssl_verify=False):
    """连接IMAP服务器"""
    ctx = _create_ssl_context(skip_verify=skip_ssl_verify)
    mail = imaplib.IMAP4_SSL(server, port, ssl_context=ctx)
    mail.login(user, password)
    return mail


def fetch_attachments(mail, sender_filter_list, save_folder, log_func,
                      filter_days=None, filter_time_enabled=False,
                      filter_time_start="00:00", filter_time_end="23:59",
                      filter_date_enabled=False, filter_date_start="", filter_date_end=""):
    """
    从收件箱中查找指定发件人的邮件，下载附件
    sender_filter_list: 发件人筛选列表（每个元素是一个关键词）
    filter_days: 仅下载这些星期几（1=周一..7=周日）的邮件，None/空=不限
    filter_time_start, filter_time_end: 仅下载此时间段内的邮件（HH:MM），filter_time_enabled=False=不限
    filter_date_start, filter_date_end: 仅下载此日期范围内的邮件（YYYY-MM-DD），filter_date_enabled=False=不限
    """
    mail.select("INBOX")
    # 搜索所有邮件
    status, messages = mail.search(None, "ALL")
    if status != "OK":
        log_func("搜索邮件失败")
        return 0

    mail_ids = messages[0].split()
    if not mail_ids:
        log_func("收件箱为空")
        return 0

    log_func(f"收件箱共 {len(mail_ids)} 封邮件，筛选条件: {sender_filter_list}，开始扫描...")
    download_count = 0

    # 只检查最近的邮件（避免每次都扫描全部）
    # 读取已处理的邮件ID记录
    processed_file = Path(__file__).parent / "processed_ids.txt"
    processed_ids = set()
    if processed_file.exists():
        with open(processed_file, "r", encoding="utf-8") as f:
            processed_ids = set(line.strip() for line in f)

    # 从最新开始检查，最多检查100封
    new_processed = set()
    for mail_id in reversed(mail_ids[-100:]):
        mail_id_str = mail_id.decode()
        if mail_id_str in processed_ids:
            continue

        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            continue

        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                # 获取发件人
                from_ = decode_str(msg.get("From", ""))
                subject = decode_str(msg.get("Subject", ""))

                # 检查发件人是否匹配任一筛选条件
                from_lower = from_.lower()
                matched = any(f.strip().lower() in from_lower for f in sender_filter_list if f.strip())
                if not matched:
                    continue

                # 邮件日期/时间过滤
                date_str = msg.get("Date", "")
                if date_str and ((filter_days and len(filter_days) < 7) or filter_time_enabled or filter_date_enabled):
                    try:
                        dt = parsedate_to_datetime(date_str)
                        # 星期过滤
                        if filter_days and dt.isoweekday() not in filter_days:
                            continue
                        # 时间段过滤
                        if filter_time_enabled:
                            h, m = dt.hour, dt.minute
                            t_start = int(filter_time_start.split(":")[0]) * 60 + int(filter_time_start.split(":")[1])
                            t_end = int(filter_time_end.split(":")[0]) * 60 + int(filter_time_end.split(":")[1])
                            now_t = h * 60 + m
                            if t_start <= t_end:
                                if not (t_start <= now_t <= t_end):
                                    continue
                            else:
                                # 跨日（如22:00~06:00）
                                if not (now_t >= t_start or now_t <= t_end):
                                    continue
                        # 日期范围过滤（年月日-年月日）
                        if filter_date_enabled and filter_date_start and filter_date_end:
                            dt_date = dt.date()
                            d_start = datetime.datetime.strptime(filter_date_start, "%Y-%m-%d").date()
                            d_end = datetime.datetime.strptime(filter_date_end, "%Y-%m-%d").date()
                            if not (d_start <= dt_date <= d_end):
                                continue
                    except Exception:
                        pass  # 解析日期失败则跳过过滤

                log_func(f"  匹配发件人: {from_} | 主题: {subject}")

                # 遍历邮件各部分，提取附件
                if msg.is_multipart():
                    for part in msg.walk():
                        content_disposition = str(part.get("Content-Disposition", ""))
                        if "attachment" in content_disposition:
                            filename = part.get_filename()
                            if filename:
                                filename = clean_filename(decode_str(filename))
                                filepath = os.path.join(save_folder, filename)
                                # 处理重名：追加下载时间戳
                                if os.path.exists(filepath):
                                    base, ext = os.path.splitext(filename)
                                    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                                    filepath = os.path.join(save_folder, f"{base}_{ts}{ext}")
                                with open(filepath, "wb") as f:
                                    f.write(part.get_payload(decode=True))
                                log_func(f"    -> 已下载: {os.path.basename(filepath)}")
                                download_count += 1
                else:
                    # 非multipart也可能是附件
                    content_type = msg.get_content_type()
                    content_disposition = str(msg.get("Content-Disposition", ""))
                    if "attachment" in content_disposition:
                        filename = msg.get_filename()
                        if filename:
                            filename = clean_filename(decode_str(filename))
                            filepath = os.path.join(save_folder, filename)
                            if os.path.exists(filepath):
                                base, ext = os.path.splitext(filename)
                                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                                filepath = os.path.join(save_folder, f"{base}_{ts}{ext}")
                            with open(filepath, "wb") as f:
                                f.write(msg.get_payload(decode=True))
                            log_func(f"    -> 已下载: {os.path.basename(filepath)}")
                            download_count += 1

                new_processed.add(mail_id_str)

    # 更新已处理记录
    all_processed = processed_ids | new_processed
    # 只保留最近1000条
    all_processed_list = list(all_processed)
    if len(all_processed_list) > 1000:
        all_processed_list = all_processed_list[-1000:]
    with open(processed_file, "w", encoding="utf-8") as f:
        for mid in all_processed_list:
            f.write(mid + "\n")

    return download_count


def send_email(smtp_server, smtp_port, use_ssl, user, password, to_addr,
               subject, body, attachment_paths, skip_ssl_verify, log_func):
    """通过SMTP发送邮件（带附件），attachment_paths 为文件路径列表"""
    # 解析收件人（去空格、去空串）
    recipients = [r.strip() for r in to_addr.split(",") if r.strip()]
    if not recipients:
        raise ValueError("收件人列表为空")

    # 构建邮件
    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject or "(无主题)"
    # 添加 Coremail 等服务器需要的标准头，避免 550 Mail rejected
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    # 正文（无正文时间发一个空行，避免空邮件被拒）
    msg.attach(MIMEText(body or " ", "plain", "utf-8"))

    # 附件（支持多个）
    total_size = 0
    if attachment_paths:
        for att_path in attachment_paths:
            if os.path.isfile(att_path):
                filename = os.path.basename(att_path)
                file_size = os.path.getsize(att_path)
                with open(att_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                # 文件名使用 RFC 5987 编码，兼容中文和特殊字符
                part.add_header("Content-Disposition", "attachment",
                                filename=("utf-8", "", filename))
                msg.attach(part)
                total_size += file_size
                log_func(f"  附件: {filename} ({file_size} 字节)")
        log_func(f"  共 {len(attachment_paths)} 个附件，总大小 {total_size} 字节")
    else:
        log_func("  无附件")

    # 发送
    ctx = _create_ssl_context(skip_verify=skip_ssl_verify)
    if use_ssl:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port, context=ctx)
    else:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()

    server.login(user, password)
    # from_addr 必须与登录用户一致，否则 Coremail 会 550 拒绝
    server.sendmail(user, recipients, msg.as_string())
    server.quit()
    log_func(f"  邮件已发送 -> {', '.join(recipients)}")


# ==================== GUI ====================
class MailAttachmentTool:
    WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("邮件自动化工具")
        self.root.geometry("720x680")
        self.root.resizable(True, True)

        self.config = load_config()
        self.running = False
        self.stop_event = threading.Event()
        self.scheduler_thread = None
        self.tray_icon = None  # 托盘图标
        self.log_entries = []  # 结构化日志: [{"time":..., "cat":..., "msg":...}]
        self.log_filter = "all"  # all / download / send

        self._build_ui()
        self._load_config_to_ui()

        # 启动日志
        self.log("程序已启动，请配置参数后点击【启动定时任务】")

    # ---------- UI构建 ----------
    def _build_ui(self):
        # 主容器
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- 标题 ---
        title = ttk.Label(main_frame, text="邮件附件定时下载 & 邮件定时发送",
                          font=("Microsoft YaHei", 14, "bold"))
        title.pack(pady=(0, 10))

        # 笔记本（选项卡）
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # ================================================================
        # Tab 1: 下载配置（IMAP + 定时）
        # ================================================================
        tab_dl = ttk.Frame(notebook, padding=10)
        notebook.add(tab_dl, text="下载配置")

        dl_inner = ttk.Frame(tab_dl)
        dl_inner.pack(fill=tk.BOTH, expand=True)
        dl_inner.columnconfigure(1, weight=1)

        row = 0
        # --- 服务器 ---
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

        # --- 下载定时 ---
        ttk.Label(dl_inner, text="定时设置",
                  font=("", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky=tk.W)
        row += 1

        self.var_dl_enabled = tk.BooleanVar(value=True)
        self.cb_dl_enabled = ttk.Checkbutton(dl_inner, text="启用定时下载",
                                             variable=self.var_dl_enabled)
        self.cb_dl_enabled.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=2)
        row += 1

        # 星期选择
        dl_day_frame = ttk.Frame(dl_inner)
        dl_day_frame.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=2)
        self.day_vars = []
        for i, name in enumerate(self.WEEKDAY_NAMES):
            var = tk.BooleanVar()
            self.day_vars.append(var)
            ttk.Checkbutton(dl_day_frame, text=name, variable=var).pack(side=tk.LEFT, padx=4)
        row += 1

        # 时间
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

        # --- 邮件筛选（可选） ---
        ttk.Separator(dl_inner, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=3,
                                                            sticky=tk.EW, pady=8)
        row += 1
        ttk.Label(dl_inner, text="邮件筛选（可选：仅下载符合条件的邮件）",
                  font=("", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky=tk.W)
        row += 1

        # 邮件接收星期
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

        # 邮件接收时间段
        ttk.Label(dl_inner, text="接收时间段:").grid(row=row, column=0, sticky=tk.W, pady=2)
        dl_email_time_frame = ttk.Frame(dl_inner)
        dl_email_time_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=2)
        self.var_dl_email_time = tk.BooleanVar()
        ttk.Checkbutton(dl_email_time_frame, text="启用",
                        variable=self.var_dl_email_time).pack(side=tk.LEFT, padx=(0, 5))
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

        # 邮件接收日期范围
        ttk.Label(dl_inner, text="接收日期范围:").grid(row=row, column=0, sticky=tk.W, pady=2)
        dl_email_date_frame = ttk.Frame(dl_inner)
        dl_email_date_frame.grid(row=row, column=1, columnspan=2, sticky=tk.W, pady=2)
        self.var_dl_email_date = tk.BooleanVar()
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
                   width=16).pack(side=tk.LEFT, padx=3)
        ttk.Button(dl_btn_frame, text="保存配置", command=self._save_download_config,
                   width=12).pack(side=tk.LEFT, padx=3)
        ttk.Button(dl_btn_frame, text="清除配置", command=self._clear_download_config,
                   width=12).pack(side=tk.LEFT, padx=3)

        # ================================================================
        # Tab 2: 发送配置（SMTP + 定时）
        # ================================================================
        tab_send = ttk.Frame(notebook, padding=10)
        notebook.add(tab_send, text="发送配置")

        send_inner = ttk.Frame(tab_send)
        send_inner.pack(fill=tk.BOTH, expand=True)
        send_inner.columnconfigure(1, weight=1)

        srow = 0
        # --- SMTP服务器 ---
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
        self.text_send_body = tk.Text(body_frame, width=35, height=5, wrap=tk.WORD,
                                       font=("", 9))
        self.text_send_body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        body_scroll = ttk.Scrollbar(body_frame, command=self.text_send_body.yview)
        body_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text_send_body.config(yscrollcommand=body_scroll.set)
        srow += 1

        # 附件
        ttk.Label(send_inner, text="附件:").grid(row=srow, column=0, sticky=tk.NW, pady=2)
        att_mode_frame = ttk.Frame(send_inner)
        att_mode_frame.grid(row=srow, column=1, columnspan=2, sticky=tk.W, pady=2, padx=(5, 0))
        self.var_att_mode = tk.StringVar(value="single")
        ttk.Radiobutton(att_mode_frame, text="单文件", variable=self.var_att_mode,
                        value="single").pack(side=tk.LEFT)
        ttk.Radiobutton(att_mode_frame, text="多文件", variable=self.var_att_mode,
                        value="multi").pack(side=tk.LEFT, padx=8)
        ttk.Radiobutton(att_mode_frame, text="文件夹", variable=self.var_att_mode,
                        value="folder").pack(side=tk.LEFT, padx=8)
        srow += 1

        self.entry_send_attachment = ttk.Entry(send_inner, width=35)
        self.entry_send_attachment.grid(row=srow, column=1, sticky=tk.EW, pady=2, padx=(5, 0))
        att_btn_frame = ttk.Frame(send_inner)
        att_btn_frame.grid(row=srow, column=2, sticky=tk.W, pady=2, padx=5)
        ttk.Button(att_btn_frame, text="浏览...", command=self._browse_send_att,
                   width=8).pack(side=tk.LEFT)
        ttk.Button(att_btn_frame, text="清空",
                   command=lambda: self.entry_send_attachment.delete(0, tk.END),
                   width=6).pack(side=tk.LEFT, padx=3)
        srow += 1

        ttk.Separator(send_inner, orient=tk.HORIZONTAL).grid(row=srow, column=0, columnspan=3,
                                                              sticky=tk.EW, pady=8)
        srow += 1

        # --- 发送定时 ---
        ttk.Label(send_inner, text="定时设置",
                  font=("", 10, "bold")).grid(row=srow, column=0, columnspan=3, sticky=tk.W)
        srow += 1

        self.var_sd_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(send_inner, text="启用定时发送",
                        variable=self.var_sd_enabled).grid(
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
        self.spin_send_hour = ttk.Spinbox(sd_time_frame, from_=0, to=23, width=4,
                                          justify=tk.CENTER)
        self.spin_send_hour.pack(side=tk.LEFT, padx=(2, 5)); self.spin_send_hour.set("8")
        ttk.Label(sd_time_frame, text="分").pack(side=tk.LEFT)
        self.spin_send_min = ttk.Spinbox(sd_time_frame, from_=0, to=59, width=4,
                                         justify=tk.CENTER)
        self.spin_send_min.pack(side=tk.LEFT, padx=(2, 5)); self.spin_send_min.set("0")
        ttk.Label(sd_time_frame, text="秒").pack(side=tk.LEFT)
        self.spin_send_sec = ttk.Spinbox(sd_time_frame, from_=0, to=59, width=4,
                                         justify=tk.CENTER)
        self.spin_send_sec.pack(side=tk.LEFT, padx=(2, 5)); self.spin_send_sec.set("0")
        srow += 1

        sd_btn_frame = ttk.Frame(send_inner)
        sd_btn_frame.grid(row=srow, column=0, columnspan=3, pady=8)
        ttk.Button(sd_btn_frame, text="立即执行一次", command=self._test_send,
                   width=16).pack(side=tk.LEFT, padx=3)
        ttk.Button(sd_btn_frame, text="保存配置", command=self._save_send_config,
                   width=12).pack(side=tk.LEFT, padx=3)
        ttk.Button(sd_btn_frame, text="清除配置", command=self._clear_send_config,
                   width=12).pack(side=tk.LEFT, padx=3)

        # ================================================================
        # Tab 3: 运行日志
        # ================================================================
        tab_log = ttk.Frame(notebook, padding=5)
        notebook.add(tab_log, text="运行日志")

        # 筛选 + 导出按钮栏
        log_toolbar = ttk.Frame(tab_log)
        log_toolbar.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(log_toolbar, text="筛选:").pack(side=tk.LEFT)
        ttk.Button(log_toolbar, text="全部", width=6,
                   command=lambda: self._apply_log_filter("all")).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_toolbar, text="下载", width=6,
                   command=lambda: self._apply_log_filter("download")).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_toolbar, text="发送", width=6,
                   command=lambda: self._apply_log_filter("send")).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_toolbar, text="系统", width=6,
                   command=lambda: self._apply_log_filter("system")).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_toolbar, text="导出日志", width=10,
                   command=self._export_log).pack(side=tk.RIGHT, padx=2)
        ttk.Button(log_toolbar, text="清空日志", width=10,
                   command=self._clear_log).pack(side=tk.RIGHT, padx=2)

        # 日志文本框
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
        ttk.Checkbutton(er1, text="启用定时导出",
                        variable=self.var_export_enabled).pack(side=tk.LEFT)
        ttk.Label(er1, text="导出目录:").pack(side=tk.LEFT, padx=(15, 0))
        self.entry_export_folder = ttk.Entry(er1, width=28)
        self.entry_export_folder.pack(side=tk.LEFT, padx=3)
        ttk.Button(er1, text="浏览...", command=self._browse_export_folder,
                   width=7).pack(side=tk.LEFT)

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
        self.spin_export_hour.pack(side=tk.LEFT, padx=(2, 3))
        self.spin_export_hour.set("23")
        ttk.Label(er2, text="分").pack(side=tk.LEFT)
        self.spin_export_min = ttk.Spinbox(er2, from_=0, to=59, width=4, justify=tk.CENTER)
        self.spin_export_min.pack(side=tk.LEFT, padx=(2, 3))
        self.spin_export_min.set("59")
        ttk.Label(er2, text="秒").pack(side=tk.LEFT)
        self.spin_export_sec = ttk.Spinbox(er2, from_=0, to=59, width=4, justify=tk.CENTER)
        self.spin_export_sec.pack(side=tk.LEFT, padx=(2, 3))
        self.spin_export_sec.set("0")

        # --- 底部控制栏 ---
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=tk.X, pady=(10, 0))

        self.status_label = ttk.Label(bottom_frame, text="状态: 未启动", foreground="gray")
        self.status_label.pack(side=tk.LEFT)

        self.btn_start = ttk.Button(bottom_frame, text="启动定时任务",
                                     command=self._toggle_scheduler, width=16)
        self.btn_start.pack(side=tk.RIGHT, padx=5)

        ttk.Button(bottom_frame, text="全部应用配置", command=self._save_ui_config,
                   width=12).pack(side=tk.RIGHT, padx=5)

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
        # 发件人筛选（逗号分隔）
        filter_list = cfg.get("sender_filter_list", [])
        # 兼容旧版单字符串
        if not filter_list and cfg.get("sender_filter"):
            filter_list = [cfg["sender_filter"]]
        self.entry_sender.delete(0, tk.END)
        self.entry_sender.insert(0, ", ".join(filter_list))
        self.entry_folder.delete(0, tk.END)
        self.entry_folder.insert(0, cfg.get("save_folder", ""))

        self.var_skip_ssl.set(cfg.get("skip_ssl_verify", False))

        # 下载定时
        dl = cfg.get("schedule_download", DEFAULT_CONFIG["schedule_download"])
        self.var_dl_enabled.set(dl.get("enabled", False))
        days = dl.get("days", [])
        for i in range(7):
            self.day_vars[i].set((i + 1) in days)
        self.spin_hour.set(str(dl.get("hour", 9)))
        self.spin_min.set(str(dl.get("minute", 0)))
        self.spin_sec.set(str(dl.get("second", 0)))

        # 邮件筛选
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
        # 加载附件模式
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

        # 发送定时
        sd = cfg.get("schedule_send", DEFAULT_CONFIG["schedule_send"])
        self.var_sd_enabled.set(sd.get("enabled", False))
        send_days = sd.get("days", [])
        for i in range(7):
            self.send_day_vars[i].set((i + 1) in send_days)
        self.spin_send_hour.set(str(sd.get("hour", 8)))
        self.spin_send_min.set(str(sd.get("minute", 0)))
        self.spin_send_sec.set(str(sd.get("second", 0)))

        # 日志导出定时
        self.var_export_enabled.set(cfg.get("log_export_enabled", False))
        self.entry_export_folder.delete(0, tk.END)
        self.entry_export_folder.insert(0, cfg.get("log_export_folder", ""))
        exp_days = cfg.get("log_export_days", [])
        for i in range(7):
            self.export_day_vars[i].set((i + 1) in exp_days)
        self.spin_export_hour.set(str(cfg.get("log_export_hour", 23)))
        self.spin_export_min.set(str(cfg.get("log_export_minute", 59)))
        self.spin_export_sec.set(str(cfg.get("log_export_second", 0)))

    def _save_ui_config(self):
        try:
            dl_days = [i + 1 for i, var in enumerate(self.day_vars) if var.get()]
            sd_days = [i + 1 for i, var in enumerate(self.send_day_vars) if var.get()]
            exp_days = [i + 1 for i, var in enumerate(self.export_day_vars) if var.get()]

            cfg = {
                # 下载配置
                "imap_server": self.entry_server.get().strip(),
                "imap_port": int(self.entry_port.get().strip()),
                "email_user": self.entry_user.get().strip(),
                "email_pass": self.entry_pass.get().strip(),
                "sender_filter": "",  # 旧版兼容
                "sender_filter_list": [
                    s.strip() for s in self.entry_sender.get().split(",") if s.strip()
                ],
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
                # 发送配置
                "smtp_server": self.entry_smtp_server.get().strip(),
                "smtp_port": int(self.entry_smtp_port.get().strip()),
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
                # 日志导出
                "log_export_enabled": self.var_export_enabled.get(),
                "log_export_folder": self.entry_export_folder.get().strip(),
                "log_export_days": exp_days,
                "log_export_hour": int(self.spin_export_hour.get()),
                "log_export_minute": int(self.spin_export_min.get()),
                "log_export_second": int(self.spin_export_sec.get()),
            }
            save_config(cfg)
            self.config = cfg
            self.log("配置已保存")
        except ValueError:
            messagebox.showerror("错误", "端口/时间必须是数字")

    # ---------- 独立保存/清除 ----------
    def _save_download_config(self):
        """仅保存下载页配置"""
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
        self.log("下载配置已保存")

    def _clear_download_config(self):
        """清除下载页所有配置"""
        self.entry_server.delete(0, tk.END)
        self.entry_port.delete(0, tk.END); self.entry_port.insert(0, "993")
        self.entry_user.delete(0, tk.END)
        self.entry_pass.delete(0, tk.END)
        self.entry_sender.delete(0, tk.END)
        self.entry_folder.delete(0, tk.END)
        self.var_skip_ssl.set(False)
        self.var_dl_enabled.set(False)
        for var in self.day_vars:
            var.set(False)
        self.spin_hour.set("9"); self.spin_min.set("0"); self.spin_sec.set("0")
        # 清除邮件筛选
        for var in self.dl_email_day_vars:
            var.set(False)
        self.var_dl_email_time.set(False)
        self.spin_dl_email_h1.set("0"); self.spin_dl_email_m1.set("0")
        self.spin_dl_email_h2.set("23"); self.spin_dl_email_m2.set("59")
        self.var_dl_email_date.set(False)
        self.entry_dl_email_date1.delete(0, tk.END)
        self.entry_dl_email_date2.delete(0, tk.END)
        self._save_download_config()
        self.log("下载配置已清除")

    def _save_send_config(self):
        """仅保存发送页配置"""
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
        self.log("发送配置已保存")

    def _clear_send_config(self):
        """清除发送页所有配置"""
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
        self.log("发送配置已清除")

    def _browse_folder(self):
        path = filedialog.askdirectory(title="选择附件保存目录")
        if path:
            self.entry_folder.delete(0, tk.END)
            self.entry_folder.insert(0, path)

    def _browse_send_att(self):
        """根据附件模式浏览选择文件/文件夹"""
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
        """从UI输入框解析附件路径列表"""
        text = self.entry_send_attachment.get().strip()
        if not text:
            return []
        return [p.strip() for p in text.split(";") if p.strip()]

    def _resolve_attachment_paths(self, cfg):
        """根据配置解析最终要发送的附件文件路径列表"""
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

    # ---------- 核心操作 ----------
    def _test_and_run(self):
        """测试连接并立即执行一次下载"""
        self._save_ui_config()

        cfg = self.config
        if not cfg["email_user"] or not cfg["email_pass"]:
            messagebox.showerror("错误", "请先填写邮箱账号和密码/授权码")
            return
        filter_list = cfg.get("sender_filter_list", [])
        if not filter_list:
            messagebox.showerror("错误", "请填写至少一个发件人筛选条件")
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
            if filter_days or filter_time_enabled or filter_date_enabled:
                self.log(f"邮件筛选: 接收日={filter_days}, 时间段={'启用' if filter_time_enabled else '不启用'}, 日期范围={'启用' if filter_date_enabled else '不启用'}", "download")
            count = fetch_attachments(mail, filter_list,
                                      cfg["save_folder"],
                                      lambda msg: self.log(msg, "download"),
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
        """立即发送一封邮件（测试）"""
        self._save_ui_config()
        cfg = self.config

        # 发件人账号优先用发送页的，空则复用下载页的
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
            send_email(
                cfg["smtp_server"], cfg["smtp_port"], cfg["smtp_ssl"],
                send_user, send_pass,
                cfg["send_to"], cfg.get("send_subject", ""),
                cfg.get("send_body", ""), att_paths,
                cfg.get("skip_ssl_smtp", False),
                lambda msg: self.log(msg, "send")
            )
            self.log("发送成功！", "send")
        except smtplib.SMTPAuthenticationError:
            self.log("SMTP错误: 认证失败，请检查账号/密码/授权码", "send")
            messagebox.showerror("发送失败", "SMTP认证失败，请检查账号/密码/授权码")
        except Exception as e:
            self.log(f"发送出错: {e}", "send")
            messagebox.showerror("发送出错", str(e))

        self.log("=" * 50, "send")

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

        # 下载校验
        if dl_enabled:
            if not cfg.get("email_user") or not cfg.get("email_pass"):
                messagebox.showerror("错误", "下载任务已启用，请填写邮箱账号和密码")
                return
            filter_list = cfg.get("sender_filter_list", [])
            if not filter_list:
                messagebox.showerror("错误", "下载任务已启用，请填写至少一个发件人筛选条件")
                return
            if not cfg.get("save_folder"):
                messagebox.showerror("错误", "下载任务已启用，请选择附件保存目录")
                return
            os.makedirs(cfg["save_folder"], exist_ok=True)

        # 发送校验
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
        """调度循环，同时检查下载和发送两个定时任务"""
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

            time.sleep(0.5)

    def _execute_download(self):
        """在UI线程中执行下载"""
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
            if filter_days or filter_time_enabled or filter_date_enabled:
                self.log(f"邮件筛选: 接收日={filter_days}, 时间段={'启用' if filter_time_enabled else '不启用'}, 日期范围={'启用' if filter_date_enabled else '不启用'}", "download")
            count = fetch_attachments(mail, filter_list,
                                      cfg["save_folder"],
                                      lambda msg: self.log(msg, "download"),
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
        except Exception as e:
            self.log(f"执行出错: {e}", "download")

        self.log("=" * 50, "download")

    def _execute_send(self):
        """在UI线程中执行发送"""
        cfg = self.config
        self.log("=" * 50, "send")
        self.log(f"定时发送触发...", "send")

        send_user = cfg.get("send_user") or cfg["email_user"]
        send_pass = cfg.get("send_pass") or cfg["email_pass"]

        try:
            self.log(f"正在连接 {cfg['smtp_server']}:{cfg['smtp_port']} ...", "send")
            att_paths = self._resolve_attachment_paths(cfg)
            send_email(
                cfg["smtp_server"], cfg["smtp_port"], cfg["smtp_ssl"],
                send_user, send_pass,
                cfg["send_to"], cfg.get("send_subject", ""),
                cfg.get("send_body", ""), att_paths,
                cfg.get("skip_ssl_smtp", False),
                lambda msg: self.log(msg, "send")
            )
            self.log("发送成功！", "send")
        except Exception as e:
            self.log(f"发送出错: {e}", "send")

        self.log("=" * 50, "send")

    # ---------- 日志（支持分类,筛选,导出） ----------
    LOG_CATEGORIES = {"download": "[下载]", "send": "[发送]", "system": "[系统]"}

    def log(self, msg, cat="system"):
        """记录日志，cat: download / send / system"""
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = self.LOG_CATEGORIES.get(cat, "[系统]")
        full = f"{prefix} {ts}  {msg}" if cat != "system" else msg
        self.log_entries.append({"time": ts, "cat": cat, "msg": msg})
        self._render_log(full)

    def _render_log(self, line):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.log_entries.clear()

    def _apply_log_filter(self, category):
        """筛选日志显示"""
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
        """导出日志到文件（当前筛选或全部）"""
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
        """定时自动导出全部日志"""
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

    # ---------- 系统托盘 ----------
    def _create_tray_image(self):
        """生成托盘图标（信封图案）"""
        img = Image.new("RGB", (64, 64), (0, 120, 212))
        draw = ImageDraw.Draw(img)
        # 信封主体
        draw.rectangle([8, 18, 56, 46], fill="white", outline="white")
        # 信封三角折角
        draw.polygon([(8, 18), (32, 32), (56, 18)], fill=(0, 120, 212))
        draw.polygon([(8, 46), (32, 32), (56, 46)], fill="white")
        # @符号
        draw.ellipse([20, 22, 44, 42], outline=(0, 120, 212), width=2)
        draw.text((24, 26), "M", fill=(0, 120, 212))
        return img

    def _show_window(self, icon=None):
        """显示主窗口"""
        self.root.after(0, self._restore_window)

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _hide_to_tray(self):
        """最小化到托盘"""
        self.root.withdraw()
        if self.tray_icon is None:
            self.tray_icon = pystray.Icon(
                "mail_tool",
                self._create_tray_image(),
                "邮件自动化工具",
                menu=pystray.Menu(
                    pystray.MenuItem("显示窗口", self._show_window, default=True),
                    pystray.MenuItem("退出程序", self._tray_exit)
                )
            )
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _tray_exit(self, icon=None):
        """从托盘退出程序"""
        if self.tray_icon:
            self.tray_icon.stop()
        if self.running:
            self._stop_scheduler()
        self.root.after(0, self.root.destroy)

    # ---------- 关闭 ----------
    def on_close(self):
        """关闭窗口 → 最小化到托盘"""
        self.log(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 窗口已最小化到系统托盘，程序在后台运行中")
        self._hide_to_tray()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()


if __name__ == "__main__":
    app = MailAttachmentTool()
    app.run()