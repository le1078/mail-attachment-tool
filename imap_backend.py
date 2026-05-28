import imaplib
import email
import ssl
import socket
import time
import re
import datetime
import functools
import tempfile
import os
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, unquote_to_bytes


# ==================== 邮件工具函数 ====================
def decode_str(s):
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


_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_FILENAME_LEN = 200


def clean_filename(name):
    name = re.sub(r'[\x00-\x1f\x7f]', "", name)
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = name.strip(" .")
    if len(name) > _MAX_FILENAME_LEN:
        base, ext = os.path.splitext(name)
        name = base[:_MAX_FILENAME_LEN - len(ext)] + ext
    base_upper = os.path.splitext(name)[0].upper()
    if base_upper in _WIN_RESERVED:
        name = f"_{name}"
    if not name:
        name = "unnamed"
    return name


def _try_decode_bytes(raw_bytes):
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


def _get_attachment_filenames_from_raw(raw_bytes):
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


def decode_attachment_filename(part):
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


# ==================== 重试装饰器 ====================
def retry_on_network_error(max_attempts=3, delay_seconds=2):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except (imaplib.IMAP4.error, socket.error, ssl.SSLError) as e:
                    last_exception = e
                    if attempt < max_attempts:
                        wait = delay_seconds * (2 ** (attempt - 1))
                        time.sleep(wait)
                    else:
                        raise
            if last_exception:
                raise last_exception
        return wrapper
    return decorator


# ==================== 批量获取已读状态 ====================
def batch_flags_fetch(mail, mail_ids):
    result = {}
    if not mail_ids:
        return result
    fetch_set = b",".join(mail_ids if isinstance(mail_ids[0], bytes) else [mid.encode() for mid in mail_ids])
    try:
        status, data = mail.fetch(fetch_set, "(FLAGS)")
        if status != "OK":
            return result
        for item in data:
            if isinstance(item, tuple):
                raw = item[0]
                if isinstance(raw, bytes):
                    raw_str = raw.decode('utf-8', errors='replace')
                else:
                    raw_str = str(raw)
                m = re.search(r'(\d+)\s+\(FLAGS\s+\(([^)]*)\)\)', raw_str)
                if m:
                    mail_id = m.group(1)
                    flags = m.group(2)
                    result[mail_id] = '\\Seen' in flags
    except Exception:
        pass
    return result


# ==================== IMAP 连接上下文管理器 ====================
class ImapConnection:
    def __init__(self, server, port, user, password, skip_ssl_verify=False):
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.skip_ssl_verify = skip_ssl_verify
        self.mail = None
        self._current_folder = None

    def __enter__(self):
        self.mail = connect_imap(
            self.server, self.port, self.user, self.password,
            skip_ssl_verify=self.skip_ssl_verify
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.mail:
            try:
                self.mail.logout()
            except Exception:
                pass
        return False

    def select_folder(self, folder):
        if " " in folder or "/" in folder or any(ord(c) > 127 for c in folder):
            select_name = f'"{folder}"'
        else:
            select_name = folder
        try:
            self.mail.select(select_name)
        except Exception:
            try:
                self.mail.select(folder)
            except Exception as e:
                raise e
        self._current_folder = folder


# ==================== SSL 上下文 ====================
def _create_ssl_context(skip_verify=False):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    ctx.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3
    if skip_verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ==================== IMAP 连接与测试 ====================
@retry_on_network_error(max_attempts=3, delay_seconds=2)
def connect_imap(server, port, user, password, skip_ssl_verify=False):
    ctx = _create_ssl_context(skip_verify=skip_ssl_verify)
    try:
        mail = imaplib.IMAP4_SSL(server, port, ssl_context=ctx)
        mail.login(user, password)
        return mail
    except ssl.SSLError as e:
        if not skip_ssl_verify:
            ctx = _create_ssl_context(skip_verify=True)
            mail = imaplib.IMAP4_SSL(server, port, ssl_context=ctx)
            mail.login(user, password)
            return mail
        else:
            raise
    except imaplib.IMAP4.error:
        raise


@retry_on_network_error(max_attempts=3, delay_seconds=2)
def test_imap_connection(server, port, user, password, skip_ssl_verify=False):
    mail = connect_imap(server, port, user, password, skip_ssl_verify)
    mail.logout()
    return True


# ==================== 文件夹检测 ====================
def get_sent_folder_name(mail, log_func=None):
    status, folders = mail.list()
    if status != "OK":
        return None

    all_folders = []
    for folder_info in folders:
        folder_str = folder_info.decode('utf-8', errors='replace') if isinstance(folder_info, bytes) else folder_info
        parts = folder_str.split('"')
        if len(parts) >= 4:
            all_folders.append(parts[-2])

    if log_func:
        log_func(f"服务器文件夹列表: {all_folders}")

    candidates = [
        'Sent Messages',
        'Sent Items',
        'Sent Mail',
        'Sent',
        '已发送',
        '已发送邮件',
        '&XfJT0ZAB-',
        '&XfJT0ZABkK5O9g-',
    ]

    for candidate in candidates:
        for f in all_folders:
            if candidate.lower() in f.lower():
                if log_func:
                    log_func(f"匹配到已发送文件夹: {f}")
                return f

    for f in all_folders:
        f_lower = f.lower()
        if 'sent' in f_lower or '已发送' in f:
            if log_func:
                log_func(f"模糊匹配到已发送文件夹: {f}")
            return f

    if log_func:
        log_func(f"未匹配到已发送文件夹，可用文件夹: {all_folders}")
    return None


# ==================== 存档到已发送 ====================
def archive_to_sent(config, raw_email_bytes, log_func):
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


# ==================== 附件下载 ====================
@retry_on_network_error(max_attempts=3, delay_seconds=2)
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
                raw_email_bytes = response_part[1]
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


# ==================== 邮件查询 ====================
@retry_on_network_error(max_attempts=3, delay_seconds=2)
def query_emails(mail, folder, search_criteria, max_count=50, log_func=None,
                 start_date=None, end_date=None, read_filter="all"):
    if " " in folder or "/" in folder or any(ord(c) > 127 for c in folder):
        select_name = f'"{folder}"'
    else:
        select_name = folder
    try:
        mail.select(select_name)
    except Exception:
        try:
            mail.select(folder)
        except Exception as e:
            if log_func:
                log_func(f"无法选择文件夹 {folder}: {e}")
            return []

    parts = []
    if read_filter == "seen":
        parts.append("SEEN")
    elif read_filter == "unseen":
        parts.append("UNSEEN")
    if search_criteria and search_criteria != "ALL":
        parts.append(search_criteria)
    if start_date:
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
        seen = False
        try:
            f_status, f_data = mail.fetch(mail_id, "(FLAGS)")
            if f_status == "OK":
                for item in f_data:
                    if isinstance(item, bytes) and b'\\Seen' in item:
                        seen = True
                        break
        except Exception:
            pass

        status, msg_data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            continue
        raw_bytes = None
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                raw_bytes = response_part[1]
                break
        if raw_bytes is None:
            continue
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
            "seen": seen,
        })
    return result


# ==================== 邮件详情 ====================
@retry_on_network_error(max_attempts=3, delay_seconds=2)
def fetch_email_detail(mail, mail_id, log_func=None):
    seen = False
    try:
        f_status, f_data = mail.fetch(mail_id.encode(), "(FLAGS)")
        if f_status == "OK":
            for item in f_data:
                if isinstance(item, bytes) and b'\\Seen' in item:
                    seen = True
                    break
    except Exception:
        pass

    status, msg_data = mail.fetch(mail_id.encode(), "(RFC822)")
    if status != "OK":
        return None
    raw_email_bytes = None
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            raw_email_bytes = response_part[1]
            break
    if raw_email_bytes is None:
        return None
    msg = email.message_from_bytes(raw_email_bytes)
    detail = {
        "from": decode_str(msg.get("From", "")),
        "to": decode_str(msg.get("To", "")),
        "cc": decode_str(msg.get("Cc", "")),
        "subject": decode_str(msg.get("Subject", "")),
        "date": msg.get("Date", ""),
        "body": "",
        "attachments": [],
        "seen": seen,
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


# ==================== 批量获取邮件详情 ====================
@retry_on_network_error(max_attempts=3, delay_seconds=2)
def batch_fetch_email_details(mail, mail_ids, log_func=None):
    results = []
    failed = 0
    total = len(mail_ids)
    for idx, mail_id in enumerate(mail_ids):
        if log_func:
            log_func(f"  [{idx + 1}/{total}] 正在获取邮件 {mail_id}...")
        detail = fetch_email_detail(mail, mail_id)
        if detail:
            results.append(detail)
        else:
            failed += 1
            if log_func:
                log_func(f"  [{idx + 1}/{total}] 获取邮件 {mail_id} 失败，跳过")
    return results, failed


# ==================== 标记已读 ====================
@retry_on_network_error(max_attempts=3, delay_seconds=2)
def mark_email_as_read(mail, folder, mail_id, log_func=None):
    if " " in folder or "/" in folder or any(ord(c) > 127 for c in folder):
        select_name = f'"{folder}"'
    else:
        select_name = folder
    try:
        mail.select(select_name)
    except Exception:
        try:
            mail.select(folder)
        except Exception:
            if log_func:
                log_func(f"无法选择文件夹 {folder}")
            return False
    try:
        mail.store(mail_id.encode() if isinstance(mail_id, str) else mail_id, '+FLAGS', '\\Seen')
        if log_func:
            log_func(f"邮件 {mail_id} 已标记为已读")
        return True
    except Exception as e:
        if log_func:
            log_func(f"标记已读失败: {e}")
        return False