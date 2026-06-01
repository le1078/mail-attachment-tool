"""任务协调器模块。

作为 Application 层，协调 GUI 层和 Domain 层之间的交互：
- 提供统一的异步任务执行接口
- 管理后台任务生命周期
- 处理重试逻辑

所有公开方法均通过 BackgroundWorker 实现非阻塞异步执行。
"""

import os
import threading
import tkinter as tk
from typing import Any, Callable, Optional

from gui_worker import BackgroundWorker
from imap_backend import (
    connect_imap,
    query_emails,
    test_imap_connection,
)
from imap_operations import download_attachments
from smtp_backend import send_email, test_smtp_connection


class TaskCoordinator:
    """任务协调器，管理下载/发送任务的执行。

    使用 BackgroundWorker 实现后台执行，通过回调与 GUI 层交互。
    所有公开方法均立即返回，不阻塞主线程。

    Attributes:
        root: Tkinter 根窗口实例。
        config: 配置字典，结构与 config_manager.DEFAULT_CONFIG 一致。
        log_func: 日志回调函数，签名为 (msg: str, category: str) -> None。
    """

    def __init__(
        self,
        root: tk.Tk,
        config: dict,
        log_func: Callable[[str, str], None],
        record_func: Optional[Callable] = None,
    ) -> None:
        """初始化任务协调器。

        Args:
            root: Tkinter 根窗口，供 BackgroundWorker 进行主线程回调调度。
            config: 配置字典，包含 IMAP/SMTP 连接参数及下载/发送选项。
            log_func: 日志回调函数，接收 (消息文本, 类别) 两个参数。
            record_func: 可选，下载记录回调函数。
        """
        self.root = root
        self.config = config
        self.log_func = log_func
        self.record_func = record_func

    # ------------------------------------------------------------------
    # 连接测试
    # ------------------------------------------------------------------

    def test_imap(
        self,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """测试 IMAP 连接（后台执行）。

        Args:
            on_complete: 连接成功的回调，接收 test_imap_connection 的返回值。
            on_error: 连接失败的回调，接收捕获的 Exception 对象。
        """
        worker = BackgroundWorker(
            self.root,
            on_complete=on_complete,
            on_error=on_error,
        )
        worker.run(
            test_imap_connection,
            self.config["imap_server"],
            self.config["imap_port"],
            self.config["email_user"],
            self.config["email_pass"],
            self.config.get("skip_ssl_verify", False),
        )

    def test_smtp(
        self,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """测试 SMTP 连接（后台执行）。

        Args:
            on_complete: 连接成功的回调，接收 test_smtp_connection 的返回值。
            on_error: 连接失败的回调，接收捕获的 Exception 对象。
        """
        worker = BackgroundWorker(
            self.root,
            on_complete=on_complete,
            on_error=on_error,
        )
        worker.run(
            test_smtp_connection,
            self.config["smtp_server"],
            self.config["smtp_port"],
            self.config.get("smtp_ssl", True),
            self.config.get("send_user") or self.config.get("email_user", ""),
            self.config.get("send_pass") or self.config.get("email_pass", ""),
            self.config.get("skip_ssl_smtp", False),
        )

    # ------------------------------------------------------------------
    # 下载 / 发送
    # ------------------------------------------------------------------

    def run_download_once(
        self,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """执行一次附件下载（后台执行）。

        连接 IMAP 服务器并调用 fetch_attachments，完成后通过回调通知结果。

        Args:
            on_complete: 下载完成的回调，接收下载数量 (int)。
            on_error: 发生异常的回调，接收捕获的 Exception 对象。
        """
        worker = BackgroundWorker(
            self.root,
            on_complete=on_complete,
            on_error=on_error,
        )
        worker.run(self._do_download)

    def _do_download(self) -> int:
        """在后台线程中执行下载逻辑。

        连接 IMAP 服务器，调用 download_attachments 完成附件下载。

        Returns:
            本次下载的附件数量。

        Raises:
            Exception: IMAP 连接或下载过程中的任何异常。
        """
        mail = connect_imap(
            self.config["imap_server"],
            self.config["imap_port"],
            self.config["email_user"],
            self.config["email_pass"],
            skip_ssl_verify=self.config.get("skip_ssl_verify", False),
        )
        try:
            count = download_attachments(
                mail,
                self.config,
                log_func=lambda msg: self.log_func(msg, "download"),
                record_func=self.record_func,
            )
            return count
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def run_send_once(
        self,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """执行一次邮件发送（后台执行）。

        通过 SMTP 发送邮件，支持附件。完成后通过回调通知结果。

        Args:
            on_complete: 发送完成的回调，接收 send_email 的返回值。
            on_error: 发生异常的回调，接收捕获的 Exception 对象。
        """
        worker = BackgroundWorker(
            self.root,
            on_complete=on_complete,
            on_error=on_error,
        )
        worker.run(self._do_send)

    def _do_send(self) -> None:
        """在后台线程中执行发送逻辑。

        Raises:
            Exception: SMTP 连接或发送过程中的任何异常。
        """
        attachment_paths = self._resolve_send_attachments()
        formatted_subject = self._format_subject(self.config.get("send_subject", ""))
        send_email(
            self.config["smtp_server"],
            self.config["smtp_port"],
            self.config.get("smtp_ssl", True),
            self.config.get("send_user") or self.config.get("email_user", ""),
            self.config.get("send_pass") or self.config.get("email_pass", ""),
            self.config["send_to"],
            formatted_subject,
            self.config.get("send_body", ""),
            attachment_paths,
            self.config.get("skip_ssl_smtp", False),
            lambda msg: self.log_func(msg, "send"),
            config=self.config,
        )

    def _resolve_send_attachments(self) -> list:
        """根据配置解析发送附件路径列表。

        支持三种模式：
        - single: 单个附件路径。
        - multi: 多个附件路径列表。
        - folder: 文件夹内所有文件路径。

        Returns:
            附件路径字符串列表。
        """
        mode = self.config.get("send_attachment_mode", "single")
        if mode == "single":
            path = self.config.get("send_attachment", "")
            return [path] if path else []
        if mode == "multi":
            return list(self.config.get("send_attachment_list", []))
        if mode == "folder":
            folder = self.config.get("send_attachment", "")
            if not folder:
                return []
            return [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if os.path.isfile(os.path.join(folder, f))
            ]
        return []

    @staticmethod
    def _format_subject(subject: str) -> str:
        import datetime
        now = datetime.datetime.now()
        return subject.replace("YYYY", str(now.year)).replace("MM", str(now.month)).replace("DD", str(now.day))

    # ------------------------------------------------------------------
    # 邮件查询
    # ------------------------------------------------------------------

    def run_query(
        self,
        folder: str,
        criteria: str,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """执行邮件查询（后台执行）。

        连接 IMAP 服务器，在指定文件夹中按条件搜索邮件。

        Args:
            folder: 要搜索的文件夹名称（如 "INBOX"）。
            criteria: IMAP 搜索条件字符串（如 "ALL"、"UNSEEN"）。
            on_complete: 查询完成的回调，接收邮件列表 (list[dict])。
            on_error: 发生异常的回调，接收捕获的 Exception 对象。
        """
        worker = BackgroundWorker(
            self.root,
            on_complete=on_complete,
            on_error=on_error,
        )
        worker.run(self._do_query, folder, criteria)

    def _do_query(self, folder: str, criteria: str) -> list:
        """在后台线程中执行邮件查询。

        Args:
            folder: 要搜索的文件夹名称。
            criteria: IMAP 搜索条件字符串。

        Returns:
            邮件信息字典列表，每项包含 id/from/to/subject/date/has_attachments/seen。

        Raises:
            Exception: IMAP 连接或查询过程中的任何异常。
        """
        mail = connect_imap(
            self.config["imap_server"],
            self.config["imap_port"],
            self.config["email_user"],
            self.config["email_pass"],
            skip_ssl_verify=self.config.get("skip_ssl_verify", False),
        )
        try:
            results = query_emails(
                mail,
                folder,
                criteria,
                log_func=lambda msg: self.log_func(msg, "query"),
            )
            return results
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def run_query_mail(
        self,
        folder: str,
        keyword: str,
        max_count: int,
        start_date: str,
        end_date: str,
        read_filter: str,
        search_body: bool,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """执行带关键词过滤的邮件查询（后台执行）。

        连接 IMAP 服务器，在指定文件夹中按条件搜索邮件，
        并支持关键词过滤。

        Args:
            folder: 要搜索的文件夹名称（如 "INBOX"、"已发送"）。
            keyword: 关键词，用于过滤邮件主题/发件人/收件人或正文。
            max_count: 最大返回邮件数量。
            start_date: 起始日期（YYYY-MM-DD 格式），为空则不限制。
            end_date: 结束日期（YYYY-MM-DD 格式），为空则不限制。
            read_filter: 已读过滤（"all"、"seen"、"unseen"）。
            search_body: 是否在邮件正文中搜索关键词。
            on_complete: 查询完成的回调，接收邮件列表 (list[dict])。
            on_error: 发生异常的回调，接收捕获的 Exception 对象。
        """
        worker = BackgroundWorker(
            self.root,
            on_complete=on_complete,
            on_error=on_error,
        )
        worker.run(
            self._do_query_mail,
            folder, keyword, max_count,
            start_date, end_date, read_filter, search_body,
        )

    def _do_query_mail(
        self,
        folder: str,
        keyword: str,
        max_count: int,
        start_date: str,
        end_date: str,
        read_filter: str,
        search_body: bool,
    ) -> list:
        from imap_backend import connect_imap as _connect_imap
        from imap_backend import get_sent_folder_name
        from imap_backend import query_emails as _query_emails

        imap_user = self.config["email_user"]
        imap_pass = self.config["email_pass"]
        if folder == "已发送":
            imap_user = self.config.get("send_user") or self.config["email_user"]
            imap_pass = self.config.get("send_pass") or self.config["email_pass"]

        mail = _connect_imap(
            self.config["imap_server"],
            self.config["imap_port"],
            imap_user,
            imap_pass,
            skip_ssl_verify=self.config.get("skip_ssl_verify", False),
        )
        try:
            actual_folder = folder
            if folder == "已发送":
                sent_folder = get_sent_folder_name(
                    mail, log_func=lambda msg: self.log_func(msg, "query"),
                )
                actual_folder = sent_folder or "INBOX"

            search_criteria = keyword if keyword and search_body else "ALL"

            results = _query_emails(
                mail,
                actual_folder,
                search_criteria,
                max_count=max_count,
                log_func=lambda msg: self.log_func(msg, "query"),
                start_date=start_date if start_date else None,
                end_date=end_date if end_date else None,
                read_filter=read_filter if read_filter else "all",
            )

            if keyword and not search_body:
                kw_lower = keyword.lower()
                results = [
                    r for r in results
                    if kw_lower in r.get("subject", "").lower()
                    or kw_lower in r.get("from", "").lower()
                    or kw_lower in r.get("to", "").lower()
                ]

            return results
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def run_fetch_mail_detail(
        self,
        mail_id: str,
        folder: str,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """获取单封邮件详情（后台执行）。

        连接 IMAP 服务器，在指定文件夹中获取邮件完整内容。

        Args:
            mail_id: 邮件 ID。
            folder: 邮件所在文件夹名称。
            on_complete: 获取完成的回调，接收邮件详情 (dict)。
            on_error: 发生异常的回调，接收捕获的 Exception 对象。
        """
        worker = BackgroundWorker(
            self.root,
            on_complete=on_complete,
            on_error=on_error,
        )
        worker.run(self._do_fetch_mail_detail, mail_id, folder)

    def _do_fetch_mail_detail(self, mail_id: str, folder: str) -> dict:
        from imap_backend import connect_imap as _connect_imap
        from imap_backend import fetch_email_detail
        from imap_backend import get_sent_folder_name

        imap_user = self.config["email_user"]
        imap_pass = self.config["email_pass"]
        if folder == "已发送":
            imap_user = self.config.get("send_user") or self.config["email_user"]
            imap_pass = self.config.get("send_pass") or self.config["email_pass"]

        mail = _connect_imap(
            self.config["imap_server"],
            self.config["imap_port"],
            imap_user,
            imap_pass,
            skip_ssl_verify=self.config.get("skip_ssl_verify", False),
        )
        try:
            actual_folder = folder
            if folder == "已发送":
                sent_folder = get_sent_folder_name(
                    mail, log_func=lambda msg: self.log_func(msg, "query"),
                )
                actual_folder = sent_folder or "INBOX"

            if any(c in actual_folder for c in " /") or any(ord(c) > 127 for c in actual_folder):
                select_name = f'"{actual_folder}"'
            else:
                select_name = actual_folder
            try:
                mail.select(select_name)
            except Exception:
                mail.select(actual_folder)

            detail = fetch_email_detail(
                mail,
                mail_id,
                log_func=lambda msg: self.log_func(msg, "query"),
            )
            if detail:
                detail["id"] = mail_id
            return detail or {}
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def run_download_single_attachment(
        self,
        detail: dict,
        save_folder: str,
        record_func: Optional[Callable] = None,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """下载单封邮件的附件到磁盘（后台执行）。

        将已缓存的邮件详情中的附件 payload 写入本地文件夹。

        Args:
            detail: 邮件详情字典，包含 attachments 列表。
            save_folder: 附件保存目录路径。
            record_func: 下载记录回调函数。
            on_complete: 下载完成的回调，接收下载数量 (int)。
            on_error: 发生异常的回调，接收捕获的 Exception 对象。
        """
        worker = BackgroundWorker(
            self.root,
            on_complete=on_complete,
            on_error=on_error,
        )
        worker.run(self._do_download_single_attachment, detail, save_folder, record_func)

    def _do_download_single_attachment(
        self,
        detail: dict,
        save_folder: str,
        record_func: Optional[Callable] = None,
    ) -> int:
        from pathlib import Path
        from mail_utils import clean_filename

        save_dir = Path(save_folder)
        save_dir.mkdir(parents=True, exist_ok=True)

        attachments = detail.get("attachments", [])
        count = 0
        for att in attachments:
            filename = att.get("filename", "unnamed")
            clean_name = clean_filename(filename)
            payload = att.get("payload")
            if not payload:
                continue
            file_path = save_dir / clean_name
            with open(file_path, "wb") as f:
                f.write(payload)
            count += 1
            if record_func:
                record_func({
                    "filename": clean_name,
                    "subject": detail.get("subject", ""),
                    "sender": detail.get("from", ""),
                    "save_path": str(file_path),
                    "size": att.get("size", len(payload)),
                    "status": "success",
                    "email_uid": detail.get("id", ""),
                })
            self.log_func(f"附件已保存: {clean_name}", "download")
        return count

    def run_batch_download_attachments(
        self,
        mail_ids: list,
        folder: str,
        save_folder: str,
        record_func: Optional[Callable] = None,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """批量下载多封邮件的附件（后台执行）。

        遍历邮件 ID 列表，逐封获取详情并下载附件到本地文件夹。

        Args:
            mail_ids: 邮件 ID 列表。
            folder: 邮件所在文件夹名称。
            save_folder: 附件保存目录路径。
            record_func: 下载记录回调函数。
            on_complete: 下载完成的回调，接收总下载数量 (int)。
            on_error: 发生异常的回调，接收捕获的 Exception 对象。
        """
        worker = BackgroundWorker(
            self.root,
            on_complete=on_complete,
            on_error=on_error,
        )
        worker.run(
            self._do_batch_download_attachments,
            mail_ids, folder, save_folder, record_func,
        )

    def _do_batch_download_attachments(
        self,
        mail_ids: list,
        folder: str,
        save_folder: str,
        record_func: Optional[Callable] = None,
    ) -> dict:
        from pathlib import Path
        from imap_backend import connect_imap as _connect_imap
        from imap_backend import fetch_email_detail
        from imap_backend import get_sent_folder_name
        from mail_utils import clean_filename

        imap_user = self.config["email_user"]
        imap_pass = self.config["email_pass"]
        if folder == "已发送":
            imap_user = self.config.get("send_user") or self.config["email_user"]
            imap_pass = self.config.get("send_pass") or self.config["email_pass"]

        mail = _connect_imap(
            self.config["imap_server"],
            self.config["imap_port"],
            imap_user,
            imap_pass,
            skip_ssl_verify=self.config.get("skip_ssl_verify", False),
        )
        try:
            actual_folder = folder
            if folder == "已发送":
                sent_folder = get_sent_folder_name(
                    mail, log_func=lambda msg: self.log_func(msg, "query"),
                )
                actual_folder = sent_folder or "INBOX"

            if any(c in actual_folder for c in " /") or any(ord(c) > 127 for c in actual_folder):
                select_name = f'"{actual_folder}"'
            else:
                select_name = actual_folder
            try:
                mail.select(select_name)
            except Exception:
                mail.select(actual_folder)

            save_dir = Path(save_folder)
            save_dir.mkdir(parents=True, exist_ok=True)

            total_count = 0
            success_count = 0
            failed_count = 0
            for idx, mail_id in enumerate(mail_ids):
                self.log_func(
                    f"[{idx + 1}/{len(mail_ids)}] 正在获取邮件 {mail_id}...",
                    "query",
                )
                detail = fetch_email_detail(
                    mail,
                    mail_id,
                    log_func=lambda msg: self.log_func(msg, "query"),
                )
                if not detail:
                    self.log_func(
                        f"[{idx + 1}/{len(mail_ids)}] 获取邮件 {mail_id} 失败，跳过",
                        "query",
                    )
                    failed_count += 1
                    continue
                detail["id"] = mail_id
                success_count += 1
                for att in detail.get("attachments", []):
                    filename = att.get("filename", "unnamed")
                    clean_name = clean_filename(filename)
                    payload = att.get("payload")
                    if not payload:
                        continue
                    file_path = save_dir / clean_name
                    with open(file_path, "wb") as f:
                        f.write(payload)
                    total_count += 1
                    if record_func:
                        record_func({
                            "filename": clean_name,
                            "subject": detail.get("subject", ""),
                            "sender": detail.get("from", ""),
                            "save_path": str(file_path),
                            "size": att.get("size", len(payload)),
                            "status": "success",
                            "email_uid": mail_id,
                        })
                    self.log_func(f"附件已保存: {clean_name}", "download")
            return {
                "success_mails": success_count,
                "total_mails": len(mail_ids),
                "total_attachments": total_count,
                "failed_mails": failed_count,
            }
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def run_mark_as_read(
        self,
        mail_ids: list,
        folder: str,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """批量标记邮件为已读（后台执行）。

        连接 IMAP 服务器，对每封邮件调用 mark_email_as_read。

        Args:
            mail_ids: 邮件 ID 列表。
            folder: 邮件所在文件夹名称。
            on_complete: 标记完成的回调，接收成功标记数量 (int)。
            on_error: 发生异常的回调，接收捕获的 Exception 对象。
        """
        worker = BackgroundWorker(
            self.root,
            on_complete=on_complete,
            on_error=on_error,
        )
        worker.run(self._do_mark_as_read, mail_ids, folder)

    def _do_mark_as_read(self, mail_ids: list, folder: str) -> int:
        from imap_backend import connect_imap as _connect_imap
        from imap_backend import mark_email_as_read

        mail = _connect_imap(
            self.config["imap_server"],
            self.config["imap_port"],
            self.config["email_user"],
            self.config["email_pass"],
            skip_ssl_verify=self.config.get("skip_ssl_verify", False),
        )
        try:
            success_count = 0
            for mail_id in mail_ids:
                result = mark_email_as_read(
                    mail,
                    folder,
                    mail_id,
                    log_func=lambda msg: self.log_func(msg, "query"),
                )
                if result:
                    success_count += 1
            return success_count
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def run_mark_as_unread(
        self,
        mail_ids: list,
        folder: str,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """批量标记邮件为未读（后台执行）。

        连接 IMAP 服务器，对每封邮件调用 mark_email_as_unread。

        Args:
            mail_ids: 邮件 ID 列表。
            folder: 邮件所在文件夹名称。
            on_complete: 标记完成的回调，接收成功标记数量 (int)。
            on_error: 发生异常的回调，接收捕获的 Exception 对象。
        """
        worker = BackgroundWorker(
            self.root,
            on_complete=on_complete,
            on_error=on_error,
        )
        worker.run(self._do_mark_as_unread, mail_ids, folder)

    def _do_mark_as_unread(self, mail_ids: list, folder: str) -> int:
        from imap_backend import connect_imap as _connect_imap
        from imap_backend import mark_email_as_unread

        mail = _connect_imap(
            self.config["imap_server"],
            self.config["imap_port"],
            self.config["email_user"],
            self.config["email_pass"],
            skip_ssl_verify=self.config.get("skip_ssl_verify", False),
        )
        try:
            success_count = 0
            for mail_id in mail_ids:
                result = mark_email_as_unread(
                    mail,
                    folder,
                    mail_id,
                    log_func=lambda msg: self.log_func(msg, "query"),
                )
                if result:
                    success_count += 1
            return success_count
        finally:
            try:
                mail.logout()
            except Exception:
                pass