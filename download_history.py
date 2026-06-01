"""下载历史记录管理模块。

提供下载历史的加载、保存、添加和清空功能，所有操作均为线程安全。
"""

import json
import os
import threading as _threading

from constants import HISTORY_FILE, MAX_HISTORY_RECORDS

_history_lock = _threading.Lock()


def load_history():
    """加载全部下载历史记录，返回按时间倒序排列的列表"""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = f.read().strip()
        if not data:
            return []
        records = json.loads(data)
        if not isinstance(records, list):
            return []
        # 按时间倒序排列
        records.sort(key=lambda r: r.get("time", ""), reverse=True)
        return records
    except Exception:
        return []


def _atomic_write(records):
    """原子写入：先写临时文件，成功后再替换"""
    tmp_path = HISTORY_FILE.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, HISTORY_FILE)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise


def _save_history_locked(records):
    if len(records) > MAX_HISTORY_RECORDS:
        records = records[:MAX_HISTORY_RECORDS]
    _atomic_write(records)


def save_history(records):
    """保存历史记录到文件（覆盖写入），自动裁剪超限部分"""
    with _history_lock:
        _save_history_locked(records)


def add_history_record(record):
    """添加一条下载历史记录。线程安全，自动裁剪超限。

    record 必须包含以下字段：
        time: str      下载时间 "YYYY-MM-DD HH:MM:SS"
        filename: str  文件名
        subject: str   邮件主题
        sender: str    发件人
        save_path: str 保存路径
        size: int      文件大小(字节)，未知时 -1
        status: str    "success" 或 "failed"
        email_uid: str IMAP UID（可选，默认空字符串）
    """
    record.setdefault("email_uid", "")
    record.setdefault("sender", "")
    record.setdefault("subject", "")
    with _history_lock:
        records = load_history()
        records.insert(0, record)
        _save_history_locked(records)


def clear_history():
    """清空所有下载历史记录"""
    with _history_lock:
        if HISTORY_FILE.exists():
            try:
                HISTORY_FILE.unlink()
            except Exception:
                pass
        _save_history_locked([])