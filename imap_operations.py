"""
高层邮件业务操作模块。

基于 imap_backend.py 的低层 IMAP 操作封装业务逻辑，包括：
- 附件批量下载（自动标记已读）

所有 UI 交互通过回调函数完成，不直接操作 tkinter。
"""
from imap_backend import (
    fetch_attachments,
    mark_email_as_read,
)


# ==================== 附件下载（含自动标记已读） ====================

def download_attachments(mail, config, log_func, record_func=None):
    """批量下载附件，并在下载成功后自动标记邮件为已读。

    包装 fetch_attachments 的低层操作，通过 record_func 回调跟踪
    已下载附件的邮件 ID，下载完成后逐封标记为已读。
    标记已读失败仅记录警告，不中断流程。

    Args:
        mail: 已通过 connect_imap 建立的 IMAP 连接。
        config: 配置字典，需包含以下键：
            sender_filter_list, save_folder, download_keyword_filter,
            download_read_status, download_filter_days,
            download_filter_time_enabled, download_filter_time_start,
            download_filter_time_end, download_filter_date_enabled,
            download_filter_date_start, download_filter_date_end.
        log_func: 日志回调，签名 (msg: str) -> None。
        record_func: 下载记录回调，签名 (record_dict: dict) -> None，可选。

    Returns:
        int: 本次下载的附件数量。
    """
    downloaded_ids: set = set()

    def _tracking_record_func(record_dict: dict) -> None:
        """内部记录回调，捕获已下载邮件的 ID 并透传原始回调。"""
        email_uid = record_dict.get("email_uid")
        if email_uid:
            downloaded_ids.add(email_uid)
        if record_func is not None:
            record_func(record_dict)

    filter_days = config.get("download_filter_days", [])
    count = fetch_attachments(
        mail,
        config.get("sender_filter_list", []),
        config["save_folder"],
        log_func,
        keyword_filter=(config.get("download_keyword_filter", "") or "").strip(),
        read_status=config.get("download_read_status", "all"),
        filter_days=filter_days if filter_days else None,
        filter_time_enabled=config.get("download_filter_time_enabled", False),
        filter_time_start=config.get("download_filter_time_start", "00:00"),
        filter_time_end=config.get("download_filter_time_end", "23:59"),
        filter_date_enabled=config.get("download_filter_date_enabled", False),
        filter_date_start=config.get("download_filter_date_start", ""),
        filter_date_end=config.get("download_filter_date_end", ""),
        record_func=_tracking_record_func,
    )

    for mail_id in downloaded_ids:
        try:
            mark_email_as_read(mail, "INBOX", mail_id, log_func)
        except Exception:
            log_func(f"  [警告] 标记已读失败: {mail_id}")

    return count