"""
邮件附件自动下载 & 定时发送工具
- 定时下载指定发件人的邮件附件
- 定时发送邮件（带附件）给指定收件人
- 邮件查询（收件箱/已发送）
- 失败告警、重试、日志持久化
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
import traceback
import glob as glob_mod
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formatdate, make_msgid, parsedate_to_datetime
from email.policy import compat32
from pathlib import Path
from urllib.parse import unquote
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# 系统托盘
import pystray
from PIL import Image, ImageDraw

# ==================== 配置管理 ====================
CONFIG_FILE = Path(__file__).parent / "config.json"
CONFIG_BACKUP_DIR = Path(__file__).parent / "config_backups"
LOG_PERSIST_FILE = Path(__file__).parent / "mail_tool_log.txt"
MAX_LOG_LINES = 10000
MAX_CONFIG_BACKUPS = 5

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
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return DEFAULT_CONFIG.copy()


def save_config(config):
    # 自动备份旧配置
    if CONFIG_FILE.exists():
        _auto_backup_config()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _auto_backup_config():
    """自动备份配置文件，最多保留 MAX_CONFIG_BACKUPS 份"""
    try:
        CONFIG_BACKUP_DIR.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = CONFIG_BACKUP_DIR / f"config_backup_{ts}.json"
        import shutil
        shutil.copy2(CONFIG_FILE, backup_path)
        # 清理旧备份
        backups = sorted(CONFIG_BACKUP_DIR.glob("config_backup_*.json"))
        while len(backups) > MAX_CONFIG_BACKUPS:
            backups[0].unlink()
            backups.pop(0)
    except Exception:
        pass  # 备份失败不影响主流程


def export_config_to(config, dest_path):
    """导出配置到指定文件"""
    with open(dest_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def import_config_from(src_path):
    """从文件导入配置，返回合并后的配置字典"""
    with open(src_path, "r", encoding="utf-8") as f:
        imported = json.load(f)
    return {**DEFAULT_CONFIG, **imported}


# ==================== 日志持久化 ====================
def load_persisted_log():
    """启动时加载持久化日志"""
    entries = []
    if LOG_PERSIST_FILE.exists():
        try:
            with open(LOG_PERSIST_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # 格式: [cat] YYYY-MM-DD HH:MM:SS  msg  或纯文本
                    m = re.match(r'^\[(下载|发送|系统|查询)\]\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s{2}(.*)', line)
                    if m:
                        cat_map = {"下载": "download", "发送": "send", "系统": "system", "查询": "query"}
                        entries.append({"time": m.group(2), "cat": cat_map.get(m.group(1), "system"), "msg": m.group(3)})
        except Exception:
            pass
    return entries


def persist_log_entries(entries):
    """将日志写入持久化文件"""
    try:
        lines_to_keep = entries[-MAX_LOG_LINES:]
        LOG_CATEGORIES_LABELS = {"download": "下载", "send": "发送", "system": "系统", "query": "查询"}
        with open(LOG_PERSIST_FILE, "w", encoding="utf-8") as f:
            for entry in lines_to_keep:
                prefix = LOG_CATEGORIES_LABELS.get(entry["cat"], "系统")
                line = f"[{prefix}] {entry['time']}  {entry['msg']}"
                f.write(line + "\n")
    except Exception:
        pass


# ==================== 邮件处理核心 ====================
def decode_str(s):
    """解码邮件头"""
    if s is None:
        return ""
    try:
        decoded_parts = decode_header(s)
    except RecursionError:
        return str(s) if isinstance(s, str) else s.decode("utf-8", errors="replace")
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
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r'[\x00-\x1f]', "", name)
    name = name.strip(" .")
    if not name:
        name = "unnamed"
    return name


def _try_decode_bytes(raw_bytes):
    """尝试用多种编码解码字节序列"""
    for enc in ['utf-8', 'gb18030', 'gbk', 'gb2312', 'big5', 'latin-1']:
        try:
            trial = raw_bytes.decode(enc)
            if '\ufffd' not in trial:
                return trial
        except Exception:
            continue
    return raw_bytes.decode('utf-8', errors='replace')


def _parse_rfc2231_value(raw_value):
    m = re.match(r"([A-Za-z0-9_-]+)'([^']*)'(.+)", raw_value, re.DOTALL)
    if not m:
        return None
    charset = m.group(1)
    encoded_value = m.group(3)
    try:
        decoded_bytes = unquote(encoded_value).encode('latin-1')
    except Exception:
        try:
            decoded_bytes = unquote(encoded_value).encode('raw_unicode_escape')
        except Exception:
            return None
    try:
        result = decoded_bytes.decode(charset, errors='replace')
        if '\ufffd' not in result:
            return result
    except Exception:
        pass
    return _try_decode_bytes(decoded_bytes) or None


def _get_attachment_filenames_from_raw(raw_bytes):
    """
    用 email.policy.default 重解析原始邮件字节，提取所有 attachment 的正确文件名。
    default 策略原生支持 RFC 2231 解码。对于任何包含替换字符(\ufffd)的失败解码
    或 default 完全无法处理的边缘情况，回退到原始字节多编码扫描。
    """
    try:
        msg_default = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    except Exception:
        msg_default = None

    filenames = []
    has_bad_filename = False
    if msg_default is not None:
        for dp in msg_default.walk():
            cd = str(dp.get("Content-Disposition", ""))
            if "attachment" not in cd.lower():
                continue
            fn = dp.get_filename()
            if fn:
                if '=?' in fn and '?=' in fn:
                    result = decode_str(fn)
                    if result:
                        filenames.append(result)
                        continue
                if '\ufffd' in fn:
                    has_bad_filename = True
                    continue
                filenames.append(fn)

    if has_bad_filename or not filenames:
        raw_filenames = _scan_raw_for_attachment_filenames(raw_bytes)
        if has_bad_filename:
            return raw_filenames
        return raw_filenames

    return filenames


def _find_boundary_from_raw(raw_bytes):
    header_end = raw_bytes.find(b'\r\n\r\n')
    if header_end == -1:
        header_end = raw_bytes.find(b'\n\n')
    if header_end == -1:
        return None
    header = raw_bytes[:header_end]
    m = re.search(rb'boundary\s*=\s*"([^"]+)"', header, re.IGNORECASE)
    if not m:
        m = re.search(rb'boundary\s*=\s*([^\s;\r\n]+)', header, re.IGNORECASE)
    return m.group(1) if m else None


def _decode_filename_from_header_bytes(header_bytes):
    rfc2231_single = re.search(rb'filename\s*\*\s*=\s*([a-zA-Z0-9_-]+)\'[^\']*\'([^\r\n;]+)', header_bytes, re.IGNORECASE)
    if rfc2231_single:
        charset = rfc2231_single.group(1).decode('ascii', errors='replace').strip()
        encoded_part = rfc2231_single.group(2).decode('ascii', errors='replace').strip()
        try:
            decoded_str = unquote(encoded_part, encoding=charset, errors='replace')
            if '\ufffd' not in decoded_str:
                return decoded_str
        except Exception:
            pass

    rfc2231_multi = {}
    for m in re.finditer(rb'filename\s*\*\s*(\d+)\s*\*\s*=\s*([^\r\n;]+)', header_bytes, re.IGNORECASE):
        seg_idx = int(m.group(1))
        seg_val = m.group(2).decode('ascii', errors='replace').strip()
        rfc2231_multi[seg_idx] = seg_val
    if rfc2231_multi:
        sorted_indices = sorted(rfc2231_multi.keys())
        first_val = rfc2231_multi[sorted_indices[0]]
        m_charset = re.match(r'([a-zA-Z0-9_-]+)\'[^\']*\'(.+)', first_val, re.DOTALL)
        charset = None
        if m_charset:
            charset = m_charset.group(1)
            rfc2231_multi[sorted_indices[0]] = m_charset.group(2)
        combined = ''.join([rfc2231_multi[i] for i in sorted_indices if i in rfc2231_multi])
        try:
            if charset:
                decoded_str = unquote(combined, encoding=charset, errors='replace')
            else:
                decoded_str = _try_decode_bytes(unquote(combined, errors='replace').encode('latin-1'))
            if decoded_str and '\ufffd' not in decoded_str:
                return decoded_str
        except Exception:
            pass

    for tag in [b'filename', b'name']:
        m = re.search(tag + rb'\s*=\s*"([^"]+)"', header_bytes, re.IGNORECASE)
        if not m:
            m = re.search(tag + rb'\s*=\s*=?([^\r\n;\s]+)', header_bytes, re.IGNORECASE)
        if m:
            raw_filename_bytes = m.group(1)
            try:
                candidate_str = raw_filename_bytes.decode('ascii', errors='strict')
                for try_str in [candidate_str, '=?' + candidate_str]:
                    if '=?' in try_str and '?=' in try_str and ('?B?' in try_str or '?Q?' in try_str):
                        decoded_parts = decode_header(try_str)
                        result_parts = []
                        for p, cs in decoded_parts:
                            if isinstance(p, bytes):
                                result_parts.append(p.decode(cs or 'utf-8', errors='replace'))
                            else:
                                result_parts.append(str(p))
                        joined = ''.join(result_parts)
                        if joined and '\ufffd' not in joined and joined != try_str:
                            return joined
                    if candidate_str.startswith('?') and '?' in candidate_str[1:]:
                        rfc2047_candidate = '=' + candidate_str
                        if '=?' in rfc2047_candidate and '?=' in rfc2047_candidate and ('?B?' in rfc2047_candidate or '?Q?' in rfc2047_candidate):
                            decoded_parts = decode_header(rfc2047_candidate)
                            result_parts = []
                            for p, cs in decoded_parts:
                                if isinstance(p, bytes):
                                    result_parts.append(p.decode(cs or 'utf-8', errors='replace'))
                                else:
                                    result_parts.append(str(p))
                            joined = ''.join(result_parts)
                            if joined and '\ufffd' not in joined and joined != rfc2047_candidate:
                                return joined
            except Exception:
                pass
            result = _try_decode_bytes(raw_filename_bytes)
            if result and '\ufffd' not in result:
                return result
            try:
                if b'%' in raw_filename_bytes:
                    unquoted = unquote_to_bytes(raw_filename_bytes)
                    result2 = _try_decode_bytes(unquoted)
                    if result2 and '\ufffd' not in result2:
                        return result2
            except Exception:
                pass
            return result

    return None


def _scan_raw_for_attachment_filenames(raw_bytes):
    boundary = _find_boundary_from_raw(raw_bytes)
    if not boundary:
        cd_match = re.search(rb'Content-Disposition:\s*([^\r\n]+)', raw_bytes[:4096], re.IGNORECASE)
        if cd_match:
            cd_str = cd_match.group(1).decode('ascii', errors='replace')
            if 'attachment' in cd_str.lower():
                filename = _decode_filename_from_header_bytes(raw_bytes[:4096])
                if filename:
                    return [filename]
        return []

    boundary_marker = b'--' + boundary
    parts = raw_bytes.split(boundary_marker)

    filenames = []
    for part_bytes in parts[1:]:
        if part_bytes.startswith(b'--'):
            break

        part_bytes = part_bytes.lstrip(b'\r\n')
        sep = part_bytes.find(b'\r\n\r\n')
        if sep == -1:
            sep = part_bytes.find(b'\n\n')
        if sep == -1:
            continue
        part_headers = part_bytes[:sep]

        cd_match = re.search(rb'Content-Disposition:\s*([^\r\n]+)', part_headers, re.IGNORECASE)
        if not cd_match:
            continue
        cd_value = cd_match.group(1)
        cd_str = cd_value.decode('ascii', errors='replace')
        if 'attachment' not in cd_str.lower():
            continue

        filename = _decode_filename_from_header_bytes(part_headers)
        if filename:
            filenames.append(filename)

    return filenames


def decode_attachment_filename(part):
    """万能附件文件名解码"""
    cd_value = ""
    ct_value = ""
    if hasattr(part, '_headers'):
        for h_name, h_val in part._headers:
            if h_name.lower() == 'content-disposition':
                cd_value = h_val
            elif h_name.lower() == 'content-type':
                ct_value = h_val
    if not cd_value:
        cd_value = part.get('Content-Disposition', '')
    if not ct_value:
        ct_value = part.get('Content-Type', '')

    rfc2231_parts = {}
    for m in re.finditer(r"filename(\*(\d+))?\*\s*=\s*([^;]+)", cd_value, re.IGNORECASE):
        seg_index = int(m.group(2)) if m.group(2) is not None else -1
        raw_val = m.group(3).strip().strip('"')
        if seg_index == -1:
            decoded = _parse_rfc2231_value(raw_val)
            if decoded and '\ufffd' not in decoded:
                return decoded
        else:
            rfc2231_parts[seg_index] = raw_val

    if rfc2231_parts:
        sorted_indices = sorted(rfc2231_parts.keys())
        first_val = rfc2231_parts[sorted_indices[0]]
        m_charset = re.match(r"([A-Za-z0-9_-]+)'([^']*)'(.+)", first_val, re.DOTALL)
        rfc2231_charset = None
        if m_charset:
            rfc2231_charset = m_charset.group(1)
            rfc2231_parts[sorted_indices[0]] = m_charset.group(3)
        combined = "".join(rfc2231_parts[i] for i in sorted_indices if i in rfc2231_parts)
        try:
            decoded_bytes = unquote(combined).encode('latin-1')
            if rfc2231_charset:
                result = decoded_bytes.decode(rfc2231_charset, errors='replace')
            else:
                result = _try_decode_bytes(decoded_bytes)
            if result and '\ufffd' not in result:
                return result
        except Exception:
            pass

    try:
        raw_bytes = part.as_bytes()
        header_end = raw_bytes.find(b'\r\n\r\n')
        if header_end > 0:
            header_section = raw_bytes[:header_end]
        else:
            header_section = raw_bytes[:2048]
        for pattern in [
            rb'filename\*\s*=\s*([A-Za-z0-9_-]+)\'[^\']*\'([^\r\n;]+)',
            rb'filename\s*=\s*"([^"]+)"',
            rb"filename\s*=\s*([^\r\n;\s]+)",
        ]:
            m = re.search(pattern, header_section, re.IGNORECASE)
            if m:
                if b"'" in m.group(0) and m.lastindex >= 2:
                    charset_bytes = m.group(1)
                    value_bytes = m.group(2)
                    try:
                        charset = charset_bytes.decode('ascii')
                        decoded_bytes = unquote(value_bytes.decode('ascii')).encode('latin-1')
                        result = decoded_bytes.decode(charset, errors='replace')
                        if '\ufffd' not in result:
                            return result
                    except Exception:
                        pass
                else:
                    raw_filename_bytes = m.group(1)
                    if raw_filename_bytes.startswith(b'"') and raw_filename_bytes.endswith(b'"'):
                        raw_filename_bytes = raw_filename_bytes[1:-1]
                    result = _try_decode_bytes(raw_filename_bytes)
                    if result and '\ufffd' not in result:
                        return result
        for pattern in [
            rb'name\*\s*=\s*([A-Za-z0-9_-]+)\'[^\']*\'([^\r\n;]+)',
            rb'name\s*=\s*"([^"]+)"',
            rb'name\s*=\s*([^\r\n;\s]+)',
        ]:
            m = re.search(pattern, header_section, re.IGNORECASE)
            if m:
                if b"'" in m.group(0) and m.lastindex >= 2:
                    charset_bytes = m.group(1)
                    value_bytes = m.group(2)
                    try:
                        charset = charset_bytes.decode('ascii')
                        decoded_bytes = unquote(value_bytes.decode('ascii')).encode('latin-1')
                        result = decoded_bytes.decode(charset, errors='replace')
                        if '\ufffd' not in result:
                            return result
                    except Exception:
                        pass
                else:
                    raw_name_bytes = m.group(1)
                    if raw_name_bytes.startswith(b'"') and raw_name_bytes.endswith(b'"'):
                        raw_name_bytes = raw_name_bytes[1:-1]
                    result = _try_decode_bytes(raw_name_bytes)
                    if result and '\ufffd' not in result:
                        return result
    except Exception:
        pass

    filename = part.get_filename()
    if filename:
        result = decode_str(filename)
        if result and '\ufffd' not in result:
            return result
        if isinstance(filename, str) and '%' in filename:
            try:
                decoded = unquote(filename, encoding='utf-8', errors='replace')
                if decoded and '\ufffd' not in decoded and decoded != filename:
                    return decoded
            except Exception:
                pass
        for recovery_enc in ['latin-1', 'cp1252']:
            try:
                raw_bytes = filename.encode(recovery_enc, errors='surrogateescape')
                if any(b > 127 for b in raw_bytes):
                    result = _try_decode_bytes(raw_bytes)
                    if result and '\ufffd' not in result:
                        return result
            except Exception:
                continue

    candidates = []
    m = re.search(r'filename\s*=\s*=\?[^?]+\?[BQ]\?[^?]+\?=', cd_value, re.IGNORECASE)
    if m:
        candidates.append(m.group(0).split('=', 1)[1].strip())
    m = re.search(r'filename\s*=\s*"([^"]*)"', cd_value, re.IGNORECASE)
    if m:
        candidates.append(m.group(1))
    m = re.search(r'filename\s*=\s*([^;"\s]+)', cd_value, re.IGNORECASE)
    if m and '"' not in m.group(0):
        candidates.append(m.group(1))
    m = re.search(r'name\s*=\s*"([^"]*)"', ct_value, re.IGNORECASE)
    if m:
        candidates.append(m.group(1))
    m = re.search(r'name\s*=\s*([^;\s"]+)', ct_value, re.IGNORECASE)
    if m and '"' not in m.group(0):
        candidates.append(m.group(1))
    for c in candidates:
        result = decode_str(c)
        if result and '\ufffd' not in result:
            return result
        for recovery_enc in ['latin-1', 'cp1252']:
            try:
                raw_bytes = c.encode(recovery_enc, errors='surrogateescape')
                if any(b > 127 for b in raw_bytes):
                    result = _try_decode_bytes(raw_bytes)
                    if result and '\ufffd' not in result:
                        return result
            except Exception:
                continue
    for c in candidates:
        if c:
            return decode_str(c)
    return None


def text_file_preview(filepath, max_chars=500):
    """预览文本文件内容"""
    text_exts = {'.txt', '.csv', '.log', '.sql', '.json', '.xml', '.html', '.htm',
                 '.py', '.js', '.ts', '.java', '.c', '.cpp', '.h', '.css', '.md',
                 '.yaml', '.yml', '.ini', '.cfg', '.conf', '.bat', '.sh', '.ps1'}
    ext = Path(filepath).suffix.lower()
    if ext not in text_exts:
        return None
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars)
        if len(content) == 0:
            return "(空文件)"
        if len(content) >= max_chars:
            return content + "\n...(已截断)"
        return content
    except Exception:
        return None


# ==================== SSL兼容处理 ====================
def _create_ssl_context(skip_verify=False):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    ctx.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3
    if skip_verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def connect_imap(server, port, user, password, skip_ssl_verify=False):
    """连接IMAP服务器，SSL证书错误时自动回退到跳过验证模式"""
    ctx = _create_ssl_context(skip_verify=skip_ssl_verify)
    try:
        mail = imaplib.IMAP4_SSL(server, port, ssl_context=ctx)
        mail.login(user, password)
        return mail
    except ssl.SSLError as e:
        if not skip_ssl_verify:
            # 证书验证失败 → 自动用跳过验证重试
            ctx = _create_ssl_context(skip_verify=True)
            mail = imaplib.IMAP4_SSL(server, port, ssl_context=ctx)
            mail.login(user, password)
            return mail
        else:
            raise  # 已经跳过验证还失败，抛出原始异常
    except imaplib.IMAP4.error:
        # 非SSL错误直接抛出
        raise


def test_imap_connection(server, port, user, password, skip_ssl_verify=False):
    """测试IMAP连接（仅登录，不执行操作）"""
    mail = connect_imap(server, port, user, password, skip_ssl_verify)
    mail.logout()
    return True


def test_smtp_connection(smtp_server, smtp_port, use_ssl, user, password, skip_ssl_verify):
    """测试SMTP连接（仅登录，不发送）"""
    ctx = _create_ssl_context(skip_verify=skip_ssl_verify)
    if use_ssl:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port, context=ctx)
    else:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()
    server.login(user, password)
    server.quit()
    return True


def get_sent_folder_name(mail, log_func=None):
    """自动检测已发送文件夹名称，支持国内外主流邮箱"""
    status, folders = mail.list()
    if status != "OK":
        return None

    # 提取所有文件夹名
    all_folders = []
    for folder_info in folders:
        folder_str = folder_info.decode('utf-8', errors='replace') if isinstance(folder_info, bytes) else folder_info
        # IMAP LIST 格式: '(\\HasNoChildren) "/" "Sent"' 或 '(\\HasChildren) "/" "[Gmail]"'
        parts = folder_str.split('"')
        if len(parts) >= 4:
            all_folders.append(parts[-2])

    if log_func:
        log_func(f"服务器文件夹列表: {all_folders}")

    # 候选已发送文件夹名（按匹配优先级排列）
    candidates = [
        'Sent Messages',          # QQ邮箱英文
        'Sent Items',             # Outlook/Hotmail
        'Sent Mail',              # Gmail
        'Sent',                   # 通用英文
        '已发送',                  # QQ/163/Coremail 中文
        '已发送邮件',              # 部分企业邮箱
        '&XfJT0ZAB-',            # IMAP UTF-7 编码的"已发送"
        '&XfJT0ZABkK5O9g-',      # 另一种编码
    ]

    # 按优先级匹配
    for candidate in candidates:
        for f in all_folders:
            if candidate.lower() in f.lower():
                if log_func:
                    log_func(f"匹配到已发送文件夹: {f}")
                return f

    # 兜底：在文件夹名中搜索"sent"或"已发送"关键词
    for f in all_folders:
        f_lower = f.lower()
        if 'sent' in f_lower or '已发送' in f:
            if log_func:
                log_func(f"模糊匹配到已发送文件夹: {f}")
            return f

    if log_func:
        log_func(f"未匹配到已发送文件夹，可用文件夹: {all_folders}")
    return None


def archive_to_sent(config, raw_email_bytes, log_func):
    """通过IMAP将已发送邮件存档到已发送文件夹"""
    try:
        imap_server = config.get("imap_server", "")
        send_user = config.get("send_user") or config.get("email_user", "")
        send_pass = config.get("send_pass") or config.get("email_pass", "")
        if not imap_server or not send_user:
            log_func("  跳过存档: IMAP服务器或账号未配置")
            return
        mail = connect_imap(imap_server, config.get("imap_port", 993),
                            send_user, send_pass,
                            config.get("skip_ssl_verify", False))
        sent_folder = get_sent_folder_name(mail, log_func=log_func)
        if sent_folder:
            # 使用双引号包裹（支持中文文件夹名和嵌套文件夹如 [Gmail]/Sent Mail）
            quoted_folder = f'"{sent_folder}"' if ' ' in sent_folder or '/' in sent_folder else sent_folder
            result = mail.append(quoted_folder, '\\Seen',
                                 imaplib.Time2Internaldate(time.time()),
                                 raw_email_bytes)
            if result[0] == "OK":
                log_func(f"  已存档到: {sent_folder}")
            else:
                log_func(f"  存档失败: {result}")
        else:
            log_func("  未找到已发送文件夹，跳过存档（可在日志中查看服务器文件夹列表）")
        mail.logout()
    except Exception as e:
        log_func(f"  存档异常: {e}")


def fetch_attachments(mail, sender_filter_list, save_folder, log_func,
                      keyword_filter="", read_status="all",
                      filter_days=None, filter_time_enabled=False,
                      filter_time_start="00:00", filter_time_end="23:59",
                      filter_date_enabled=False, filter_date_start="", filter_date_end=""):
    mail.select("INBOX")
    if read_status == "unseen":
        search_criteria = "UNSEEN"
    else:
        search_criteria = "ALL"
    status, messages = mail.search(None, search_criteria)
    if status != "OK":
        log_func("搜索邮件失败")
        return 0

    mail_ids = messages[0].split()
    if not mail_ids:
        log_func("收件箱为空")
        return 0

    log_func(f"收件箱共 {len(mail_ids)} 封邮件，筛选条件: 发件人={sender_filter_list}, 关键词={keyword_filter or '无'}, 状态={read_status}，开始扫描...")
    download_count = 0

    processed_file = Path(__file__).parent / "processed_ids.txt"
    processed_ids = set()
    if read_status == "unseen" and processed_file.exists():
        with open(processed_file, "r", encoding="utf-8") as f:
            processed_ids = set(line.strip() for line in f)

    new_processed = set()
    for mail_id in reversed(mail_ids[-100:]):
        mail_id_str = mail_id.decode()
        if read_status == "unseen" and mail_id_str in processed_ids:
            continue
        if read_status == "seen":
            try:
                flag_status, flag_data = mail.fetch(mail_id, "(FLAGS)")
                if flag_status == "OK" and flag_data:
                    flags = str(flag_data[0])
                    if "\\Seen" not in flags:
                        continue
                else:
                    continue
            except Exception:
                continue

        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            continue

        for response_part in msg_data:
            if isinstance(response_part, tuple):
                raw_email_bytes = response_part[1]  # 整个邮件的原始IMAP字节
                msg = email.message_from_bytes(raw_email_bytes)
                from_ = decode_str(msg.get("From", ""))
                subject = decode_str(msg.get("Subject", ""))
                from_lower = from_.lower()
                sender_matched = True
                if sender_filter_list and any(f.strip() for f in sender_filter_list):
                    sender_matched = any(f.strip().lower() in from_lower for f in sender_filter_list if f.strip())
                kw = (keyword_filter or "").strip().lower()
                kw_matched = True
                if kw:
                    subj_lower = subject.lower()
                    kw_matched = kw in subj_lower
                if not (sender_matched and kw_matched):
                    continue
                date_str = msg.get("Date", "")
                if date_str and ((filter_days and len(filter_days) < 7) or filter_time_enabled or filter_date_enabled):
                    try:
                        dt = parsedate_to_datetime(date_str)
                        if filter_days and dt.isoweekday() not in filter_days:
                            continue
                        if filter_time_enabled:
                            h, m = dt.hour, dt.minute
                            t_start = int(filter_time_start.split(":")[0]) * 60 + int(filter_time_start.split(":")[1])
                            t_end = int(filter_time_end.split(":")[0]) * 60 + int(filter_time_end.split(":")[1])
                            now_t = h * 60 + m
                            if t_start <= t_end:
                                if not (t_start <= now_t <= t_end):
                                    continue
                            else:
                                if not (now_t >= t_start or now_t <= t_end):
                                    continue
                        if filter_date_enabled and filter_date_start and filter_date_end:
                            dt_date = dt.date()
                            d_start = datetime.datetime.strptime(filter_date_start, "%Y-%m-%d").date()
                            d_end = datetime.datetime.strptime(filter_date_end, "%Y-%m-%d").date()
                            if not (d_start <= dt_date <= d_end):
                                continue
                    except Exception:
                        pass

                log_func(f"  匹配发件人: {from_} | 主题: {subject}")

                raw_filenames = _get_attachment_filenames_from_raw(raw_email_bytes)
                fn_iter = iter(raw_filenames)

                if msg.is_multipart():
                    for part in msg.walk():
                        content_disposition = str(part.get("Content-Disposition", ""))
                        if "attachment" in content_disposition:
                            try:
                                filename = next(fn_iter)
                            except StopIteration:
                                filename = decode_attachment_filename(part)
                            if not filename:
                                log_func(f"   ⚠ 附件解码失败")
                                continue
                            filename = clean_filename(filename)
                            filepath = os.path.join(save_folder, filename)
                            if os.path.exists(filepath):
                                base, ext = os.path.splitext(filename)
                                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                                filepath = os.path.join(save_folder, f"{base}_{ts}{ext}")
                            with open(filepath, "wb") as f:
                                f.write(part.get_payload(decode=True))
                            log_func(f"    -> 已下载: {os.path.basename(filepath)}")
                            download_count += 1
                else:
                    content_disposition = str(msg.get("Content-Disposition", ""))
                    if "attachment" in content_disposition:
                        try:
                            filename = next(fn_iter)
                        except StopIteration:
                            filename = decode_attachment_filename(msg)
                        if not filename:
                            log_func(f"   ⚠ 附件解码失败")
                            continue
                        filename = clean_filename(filename)
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

    all_processed = processed_ids | new_processed
    all_processed_list = list(all_processed)
    if len(all_processed_list) > 1000:
        all_processed_list = all_processed_list[-1000:]
    with open(processed_file, "w", encoding="utf-8") as f:
        for mid in all_processed_list:
            f.write(mid + "\n")
    return download_count


def send_email(smtp_server, smtp_port, use_ssl, user, password, to_addr,
               subject, body, attachment_paths, skip_ssl_verify, log_func,
               config=None):
    """通过SMTP发送邮件（带附件），attachment_paths 为文件路径列表"""
    recipients = [r.strip() for r in to_addr.split(",") if r.strip()]
    if not recipients:
        raise ValueError("收件人列表为空")

    # 附件预检
    if attachment_paths:
        missing = [p for p in attachment_paths if not os.path.isfile(p)]
        if missing:
            log_func(f"  ⚠ 以下附件不存在: {missing}")
            # 过滤掉不存在的附件继续发送
            attachment_paths = [p for p in attachment_paths if os.path.isfile(p)]
            if not attachment_paths:
                log_func("  所有附件均不存在，将以无附件方式发送")

    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject or "(无主题)"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    msg.attach(MIMEText(body or " ", "plain", "utf-8"))

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
                part.add_header("Content-Disposition", "attachment",
                                filename=("utf-8", "", filename))
                msg.attach(part)
                total_size += file_size
                log_func(f"  附件: {filename} ({file_size} 字节)")
                # 文本文件预览
                preview = text_file_preview(att_path)
                if preview:
                    log_func(f"  附件预览(文本):\n{preview[:300]}")
        log_func(f"  共 {len(attachment_paths)} 个附件，总大小 {total_size} 字节")
    else:
        log_func("  无附件")

    ctx = _create_ssl_context(skip_verify=skip_ssl_verify)
    if use_ssl:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port, context=ctx)
    else:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()

    server.login(user, password)
    server.sendmail(user, recipients, msg.as_string())
    server.quit()
    log_func(f"  邮件已发送 -> {', '.join(recipients)}")

    # 存档到已发送文件夹
    if config:
        archive_to_sent(config, msg.as_bytes(), log_func)


def query_emails(mail, folder, search_criteria, max_count=50, log_func=None,
                 start_date=None, end_date=None):
    """查询指定文件夹中的邮件列表，返回邮件摘要列表
    
    start_date/end_date: 日期字符串 "YYYY-MM-DD"，可筛选日期范围
    """
    # 智能选择文件夹：简单文件夹名不加引号，含空格/特殊字符才加
    if " " in folder or "/" in folder or any(ord(c) > 127 for c in folder):
        select_name = f'"{folder}"'
    else:
        select_name = folder
    try:
        mail.select(select_name)
    except Exception:
        # 回退：尝试不带引号
        try:
            mail.select(folder)
        except Exception as e:
            if log_func:
                log_func(f"无法选择文件夹 {folder}: {e}")
            return []

    # 构建完整 IMAP 搜索条件（含日期范围）
    parts = []
    if search_criteria and search_criteria != "ALL":
        parts.append(search_criteria)
    if start_date:
        # IMAP SINCE 格式: DD-Mon-YYYY
        try:
            dt = datetime.datetime.strptime(start_date, "%Y-%m-%d")
            parts.append(f'SINCE "{dt.strftime("%d-%b-%Y")}"')
        except ValueError:
            pass
    if end_date:
        try:
            dt = datetime.datetime.strptime(end_date, "%Y-%m-%d")
            parts.append(f'BEFORE "{dt.strftime("%d-%b-%Y")}"')
        except ValueError:
            pass

    if parts:
        criteria = " ".join(parts)
    else:
        criteria = "ALL"

    if log_func:
        log_func(f"已选择文件夹: {folder}, 搜索条件: {criteria}")

    status, messages = mail.search(None, criteria)
    if status != "OK":
        if log_func:
            log_func(f"搜索失败, status={status}")
        return []

    mail_ids = messages[0].split()
    if log_func:
        log_func(f"找到 {len(mail_ids)} 封邮件")

    result = []
    for mail_id in reversed(mail_ids[-max_count:]):
        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            continue
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                # IMAP FETCH 返回 (元数据, 邮件内容) 二元组，索引 1 才是真实邮件
                raw_bytes = response_part[1]
                msg = email.message_from_bytes(raw_bytes)
                date_str = msg.get("Date", "")
                try:
                    dt = parsedate_to_datetime(date_str)
                    date_formatted = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    date_formatted = date_str
                result.append({
                    "id": mail_id.decode(),
                    "from": decode_str(msg.get("From", "")),
                    "to": decode_str(msg.get("To", "")),
                    "subject": decode_str(msg.get("Subject", "")),
                    "date": date_formatted,
                    "has_attachments": "attachment" in str(msg).lower(),
                })
    return result


def fetch_email_detail(mail, mail_id, log_func=None):
    """获取单封邮件的详细信息"""
    status, msg_data = mail.fetch(mail_id.encode(), "(RFC822)")
    if status != "OK":
        return None
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            raw_email_bytes = response_part[1]
            msg = email.message_from_bytes(raw_email_bytes)
            detail = {
                "from": decode_str(msg.get("From", "")),
                "to": decode_str(msg.get("To", "")),
                "cc": decode_str(msg.get("Cc", "")),
                "subject": decode_str(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
                "body": "",
                "attachments": [],
            }

            raw_filenames = _get_attachment_filenames_from_raw(raw_email_bytes)
            fn_iter = iter(raw_filenames)

            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition", ""))
                    if "attachment" in content_disposition:
                        try:
                            filename = next(fn_iter)
                        except StopIteration:
                            filename = decode_attachment_filename(part)
                        if filename:
                            detail["attachments"].append({
                                "filename": filename,
                                "size": len(part.get_payload(decode=True) or b""),
                                "payload": part.get_payload(decode=True),
                            })
                    elif content_type == "text/plain" and "attachment" not in content_disposition:
                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                detail["body"] = _try_decode_bytes(payload)[:5000]
                        except Exception:
                            pass
            else:
                content_type = msg.get_content_type()
                content_disposition = str(msg.get("Content-Disposition", ""))
                if "attachment" in content_disposition:
                    try:
                        filename = next(fn_iter)
                    except StopIteration:
                        filename = decode_attachment_filename(msg)
                    if filename:
                        detail["attachments"].append({
                            "filename": filename,
                            "size": len(msg.get_payload(decode=True) or b""),
                            "payload": msg.get_payload(decode=True),
                        })
                elif content_type == "text/plain":
                    try:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            detail["body"] = _try_decode_bytes(payload)[:5000]
                    except Exception:
                        pass
            return detail
    return None


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

        self._build_ui()
        self._start_clock_update()

        self.log("程序已启动，请配置参数后点击【启动定时任务】")

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

        self.btn_query_mail = ttk.Button(query_ctrl1, text="查询", command=self._do_mail_query, width=8)
        self.btn_query_mail.pack(side=tk.LEFT, padx=3)
        self.btn_refresh_mail = ttk.Button(query_ctrl1, text="刷新列表", command=self._do_mail_query, width=8)
        self.btn_refresh_mail.pack(side=tk.LEFT, padx=3)

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

        # 邮件列表 + 详情 左右分栏
        query_paned = ttk.PanedWindow(tab_query, orient=tk.HORIZONTAL)
        query_paned.pack(fill=tk.BOTH, expand=True)

        # 左侧：邮件列表 (Treeview)
        list_frame = ttk.Frame(query_paned)
        query_paned.add(list_frame, weight=3)

        columns = ("发件人/收件人", "主题", "日期")
        self.mail_tree = ttk.Treeview(list_frame, columns=columns, show="headings",
                                       selectmode="browse", height=15)
        self.mail_tree.heading("发件人/收件人", text="发件人/收件人")
        self.mail_tree.heading("主题", text="主题")
        self.mail_tree.heading("日期", text="日期")
        self.mail_tree.column("发件人/收件人", width=160)
        self.mail_tree.column("主题", width=200)
        self.mail_tree.column("日期", width=110)
        self.mail_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll = ttk.Scrollbar(list_frame, command=self.mail_tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.mail_tree.config(yscrollcommand=tree_scroll.set)
        self.mail_tree.bind("<<TreeviewSelect>>", self._on_mail_select)

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

        dl = cfg.get("schedule_download", DEFAULT_CONFIG["schedule_download"])
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

        sd = cfg.get("schedule_send", DEFAULT_CONFIG["schedule_send"])
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

        # 标记查询中，禁用按钮
        self.querying = True
        self.btn_query_mail.config(state=tk.DISABLED, text="查询中...")
        self.btn_refresh_mail.config(state=tk.DISABLED, text="查询中...")

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
                mail = connect_imap(cfg["imap_server"], cfg["imap_port"],
                                    cfg["email_user"], cfg["email_pass"],
                                    cfg.get("skip_ssl_verify", False))

                criteria = "ALL"

                actual_folder = folder
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
                                     end_date=end_date)
                mail.logout()

                # 关键词本地过滤
                if keyword:
                    kw = keyword.lower()
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

        # 清除旧详情
        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.config(state=tk.DISABLED)

        # 填充列表（已在 _do_mail_query 中清空）
        for em in emails:
            person = em["from"] if folder != "已发送" else em["to"]
            self.mail_tree.insert("", tk.END, values=(person, em["subject"], em["date"]), iid=em["id"])

        self.log(f"查询完成，共 {len(emails)} 封邮件", "query")
        self.log("=" * 50, "query")

    def _query_failed(self, error_msg):
        """主线程：查询失败处理"""
        self.querying = False
        self.btn_query_mail.config(state=tk.NORMAL, text="查询")
        self.btn_refresh_mail.config(state=tk.NORMAL, text="刷新列表")

        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.insert(tk.END, f"查询失败:\n{error_msg}")
        self.mail_detail_text.config(state=tk.DISABLED)

        self.log(f"查询失败: {error_msg}", "query")
        self.log("=" * 50, "query")

        if "未找到已发送文件夹" not in error_msg:
            messagebox.showerror("查询失败", error_msg)

    def _on_mail_select(self, event):
        """选中邮件时显示详情"""
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
            mail = connect_imap(cfg["imap_server"], cfg["imap_port"],
                                cfg["email_user"], cfg["email_pass"],
                                cfg.get("skip_ssl_verify", False))

            # 获取当前查询选中的文件夹（由调用方传入，避免线程内调用 Tkinter）
            folder = query_folder
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

    def _download_query_attachment(self):
        """下载查询邮件中的附件"""
        if not self.query_detail_data or not self.query_detail_data.get("attachments"):
            messagebox.showinfo("提示", "此邮件无附件")
            return
        folder = filedialog.askdirectory(title="选择附件保存目录")
        if not folder:
            return
        count = 0
        for att in self.query_detail_data["attachments"]:
            filename = clean_filename(att["filename"])
            filepath = os.path.join(folder, filename)
            if os.path.exists(filepath):
                base, ext = os.path.splitext(filename)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filepath = os.path.join(folder, f"{base}_{ts}{ext}")
            with open(filepath, "wb") as f:
                f.write(att["payload"])
            self.log(f"已下载附件: {filename}", "query")
            count += 1
        messagebox.showinfo("下载完成", f"成功下载 {count} 个附件到:\n{folder}")
        self.log(f"本次下载了 {count} 个附件", "query")

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