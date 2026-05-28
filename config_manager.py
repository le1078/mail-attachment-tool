import json
import os
import re
import sys
import datetime
import shutil
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"
CONFIG_BACKUP_DIR = Path(__file__).parent / "config_backups"
LOG_PERSIST_FILE = Path(__file__).parent / "mail_tool_log.txt"
MAX_LOG_LINES = 10000
MAX_CONFIG_BACKUPS = 5

LOG_CATEGORIES_LABELS = {"download": "下载", "send": "发送", "system": "系统", "query": "查询"}

DEFAULT_CONFIG = {
    # === 下载配置 ===
    "imap_server": "",
    "imap_port": 993,
    "email_user": "",
    "email_pass": "",
    "sender_filter_list": [],
    "download_keyword_filter": "",
    "download_read_status": "all",
    "save_folder": "",
    "skip_ssl_verify": False,
    "schedule_download": {
        "days": [],
        "hour": 9,
        "minute": 0,
        "second": 0,
        "enabled": False
    },
    "download_filter_days": [],
    "download_filter_time_enabled": False,
    "download_filter_time_start": "00:00",
    "download_filter_time_end": "23:59",
    "download_filter_date_enabled": False,
    "download_filter_date_start": "",
    "download_filter_date_end": "",
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
    "send_attachment_mode": "single",
    "send_attachment_list": [],
    "send_attachment": "",
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
    # === 重试配置 ===
    "retry_enabled": True,
    "retry_count": 3,
    "retry_interval_minutes": 5,
}


def load_config():
    defaults = sys.modules[__name__].DEFAULT_CONFIG
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return {**defaults, **json.load(f)}
    return defaults.copy()


def save_config(config):
    if CONFIG_FILE.exists():
        _auto_backup_config()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _auto_backup_config():
    try:
        CONFIG_BACKUP_DIR.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = CONFIG_BACKUP_DIR / f"config_backup_{ts}.json"
        shutil.copy2(CONFIG_FILE, backup_path)
        backups = sorted(CONFIG_BACKUP_DIR.glob("config_backup_*.json"))
        while len(backups) > MAX_CONFIG_BACKUPS:
            backups[0].unlink()
            backups.pop(0)
    except Exception:
        pass


def export_config_to(config, dest_path):
    with open(dest_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def import_config_from(src_path):
    defaults = sys.modules[__name__].DEFAULT_CONFIG
    with open(src_path, "r", encoding="utf-8") as f:
        imported = json.load(f)
    return {**defaults, **imported}


def load_persisted_log():
    entries = []
    if LOG_PERSIST_FILE.exists():
        try:
            with open(LOG_PERSIST_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    m = re.match(r'^\[(下载|发送|系统|查询)\]\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s{2}(.*)', line)
                    if m:
                        cat_map = {"下载": "download", "发送": "send", "系统": "system", "查询": "query"}
                        entries.append({"time": m.group(2), "cat": cat_map.get(m.group(1), "system"), "msg": m.group(3)})
        except Exception:
            pass
    return entries


def persist_log_entries(entries):
    try:
        lines_to_keep = entries[-MAX_LOG_LINES:]
        with open(LOG_PERSIST_FILE, "w", encoding="utf-8") as f:
            for entry in lines_to_keep:
                prefix = LOG_CATEGORIES_LABELS.get(entry["cat"], "系统")
                line = f"[{prefix}] {entry['time']}  {entry['msg']}"
                f.write(line + "\n")
    except Exception:
        pass


def validate_config(config):
    errors = []

    # 下载配置 - 必填字段
    if not config.get("imap_server", "").strip():
        errors.append("下载配置: IMAP服务器地址(imap_server)不能为空")
    if not isinstance(config.get("imap_port"), int) or config.get("imap_port") <= 0 or config.get("imap_port") > 65535:
        errors.append("下载配置: IMAP端口(imap_port)必须为1~65535之间的正整数")
    if not config.get("email_user", "").strip():
        errors.append("下载配置: 邮箱账号(email_user)不能为空")
    if not config.get("email_pass", "").strip():
        errors.append("下载配置: 邮箱密码(email_pass)不能为空")
    if not config.get("save_folder", "").strip():
        errors.append("下载配置: 附件保存目录(save_folder)不能为空")
    if config.get("download_read_status") not in ("all", "unread", "read"):
        errors.append("下载配置: 读取状态(download_read_status)必须为 all/unread/read 之一")

    # 发送配置 - 必填字段
    if not config.get("smtp_server", "").strip():
        errors.append("发送配置: SMTP服务器地址(smtp_server)不能为空")
    if not isinstance(config.get("smtp_port"), int) or config.get("smtp_port") <= 0 or config.get("smtp_port") > 65535:
        errors.append("发送配置: SMTP端口(smtp_port)必须为1~65535之间的正整数")
    if not config.get("send_user", "").strip():
        errors.append("发送配置: 发件人账号(send_user)不能为空")
    if not config.get("send_pass", "").strip():
        errors.append("发送配置: 发件人密码(send_pass)不能为空")
    if not config.get("send_to", "").strip():
        errors.append("发送配置: 收件人地址(send_to)不能为空")
    if config.get("send_attachment_mode") not in ("single", "list"):
        errors.append("发送配置: 附件模式(send_attachment_mode)必须为 single/list 之一")

    # 下载筛选 - 日期格式校验
    if config.get("download_filter_date_enabled"):
        date_start = config.get("download_filter_date_start", "")
        date_end = config.get("download_filter_date_end", "")
        if date_start and not re.match(r'^\d{4}-\d{2}-\d{2}$', date_start):
            errors.append("下载配置: 筛选开始日期(download_filter_date_start)格式无效，应为YYYY-MM-DD")
        if date_end and not re.match(r'^\d{4}-\d{2}-\d{2}$', date_end):
            errors.append("下载配置: 筛选结束日期(download_filter_date_end)格式无效，应为YYYY-MM-DD")

    # 下载筛选 - 时间格式校验
    if config.get("download_filter_time_enabled"):
        time_start = config.get("download_filter_time_start", "")
        time_end = config.get("download_filter_time_end", "")
        if time_start and not re.match(r'^\d{2}:\d{2}$', time_start):
            errors.append("下载配置: 筛选开始时间(download_filter_time_start)格式无效，应为HH:MM")
        if time_end and not re.match(r'^\d{2}:\d{2}$', time_end):
            errors.append("下载配置: 筛选结束时间(download_filter_time_end)格式无效，应为HH:MM")

    # 定时调度 - days列表校验
    for schedule_key, schedule_label in [("schedule_download", "下载定时"), ("schedule_send", "发送定时")]:
        schedule = config.get(schedule_key, {})
        if schedule.get("enabled", False):
            days = schedule.get("days", [])
            valid_days = set(range(7))
            invalid = [d for d in days if d not in valid_days]
            if invalid:
                errors.append(f"{schedule_label}: 星期配置包含无效值 {invalid}，必须为0-6之间的整数")

    # 重试配置
    if not isinstance(config.get("retry_count"), int) or config.get("retry_count", 0) < 0:
        errors.append("重试配置: 重试次数(retry_count)必须为非负整数")
    if not isinstance(config.get("retry_interval_minutes"), int) or config.get("retry_interval_minutes", 0) < 1:
        errors.append("重试配置: 重试间隔(retry_interval_minutes)必须为正整数")

    # 日志导出 - 定时字段校验
    if config.get("log_export_enabled", False):
        if not config.get("log_export_folder", "").strip():
            errors.append("日志导出: 已启用但导出目录(log_export_folder)未设置")

    if errors:
        raise ValueError("\n".join(errors))