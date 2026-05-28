import email
import re
from email.header import decode_header
from email.policy import default
from pathlib import Path
from urllib.parse import unquote, unquote_to_bytes


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