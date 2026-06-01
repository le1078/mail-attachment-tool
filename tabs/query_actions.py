import csv
import datetime
import os
import tkinter as tk
from tkinter import filedialog, messagebox

from export_utils import export_to_excel
from imap_backend import get_sent_folder_name
from mail_utils import clean_filename


class QueryActionsMixin:
    """QueryTab 业务逻辑 Mixin：查询、下载、标记、导出等操作。"""

    def _do_mail_query(self) -> None:
        if self.querying:
            return

        cfg = self.cb.config()
        if not cfg.get("email_user") or not cfg.get("email_pass"):
            messagebox.showerror("错误", "请先在下载配置页填写IMAP邮箱账号和密码")
            return

        keyword = self.entry_query_keyword.get().strip()
        max_count = int(self.spin_query_count.get())
        folder = self.combo_query_folder.get()
        start_date = self.entry_query_start_date.get().strip() or None
        end_date = self.entry_query_end_date.get().strip() or None

        read_filter_map = {"全部": "all", "已读": "seen", "未读": "unseen"}
        read_filter = read_filter_map.get(self.combo_read_filter.get(), "all")
        search_body = self.var_search_body.get()

        self.querying = True
        self.btn_query_mail.config(state=tk.DISABLED, text="查询中...")
        self.btn_refresh_mail.config(state=tk.DISABLED, text="查询中...")
        self.btn_sent_mail.config(state=tk.DISABLED, text="查询中...")

        for item in self.mail_tree.get_children():
            self.mail_tree.delete(item)
        self.query_detail_data = None
        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.insert(tk.END, "正在查询邮件，请稍候...")
        self.mail_detail_text.config(state=tk.DISABLED)

        self.cb.log(f"正在查询 {folder}...", "query")

        def on_complete(emails):
            self._query_success(emails, folder)

        def on_error(exc):
            msg = str(exc) if isinstance(exc, Exception) else str(exc)
            self._query_failed(msg)

        self.cb.coordinator.run_query_mail(
            folder=folder,
            keyword=keyword,
            max_count=max_count,
            start_date=start_date,
            end_date=end_date,
            read_filter=read_filter,
            search_body=search_body,
            on_complete=on_complete,
            on_error=on_error,
        )

    def _query_success(self, emails, folder) -> None:
        self.querying = False
        self.btn_query_mail.config(state=tk.NORMAL, text="查询")
        self.btn_refresh_mail.config(state=tk.NORMAL, text="刷新列表")
        self.btn_sent_mail.config(state=tk.NORMAL, text="查看已发送")

        self._query_results_cache = {em["id"]: em for em in emails}

        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.config(state=tk.DISABLED)

        for em in emails:
            person = em["from"] if folder != "已发送" else em["to"]
            status_text = "未读" if not em.get("seen") else "已读"
            tags = ("unseen",) if not em.get("seen") else ()
            self.mail_tree.insert(
                "", tk.END,
                values=(status_text, person, em["subject"], em["date"]),
                iid=em["id"], tags=tags,
            )

        self.cb.log(f"查询完成，共 {len(emails)} 封邮件", "query")
        self.cb.log("=" * 50, "query")

    def _query_failed(self, error_msg: str) -> None:
        self.querying = False
        self.btn_query_mail.config(state=tk.NORMAL, text="查询")
        self.btn_refresh_mail.config(state=tk.NORMAL, text="刷新列表")
        self.btn_sent_mail.config(state=tk.NORMAL, text="查看已发送")

        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.insert(tk.END, f"查询失败:\n{error_msg}")
        self.mail_detail_text.config(state=tk.DISABLED)

        self.cb.log(f"查询失败: {error_msg}", "query")
        self.cb.log("=" * 50, "query")

        if "未找到已发送文件夹" not in error_msg:
            messagebox.showerror("查询失败", error_msg)

    def _do_sent_mail_query(self) -> None:
        self.combo_query_folder.set("已发送")
        self.combo_read_filter.set("全部")
        self._do_mail_query()

    def _on_mail_select(self, event) -> None:
        self._update_selected_count()
        selection = self.mail_tree.selection()
        if not selection:
            return
        mail_id = selection[0]

        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.insert(tk.END, "正在加载邮件详情...")
        self.mail_detail_text.config(state=tk.DISABLED)

        folder = self.combo_query_folder.get()

        def on_complete(detail):
            if detail:
                detail["id"] = mail_id
            self._show_mail_detail(detail)

        def on_error(exc):
            self._show_mail_detail_error(str(exc))

        self.cb.coordinator.run_fetch_mail_detail(
            mail_id=mail_id,
            folder=folder,
            on_complete=on_complete,
            on_error=on_error,
        )

    def _show_mail_detail(self, detail) -> None:
        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        if detail:
            self.query_detail_data = detail
            text = f"发件人: {detail['from']}\n"
            text += f"收件人: {detail['to']}\n"
            if detail.get("cc"):
                text += f"抄送: {detail['cc']}\n"
            text += f"主题: {detail['subject']}\n"
            text += f"日期: {detail['date']}\n"
            seen_text = "已读" if detail.get("seen") else "未读"
            text += f"状态: {seen_text}\n"
            text += f"{'─' * 40}\n"
            if detail.get("body"):
                text += detail["body"]
            else:
                text += "(无文本正文，可能为HTML格式)"
            text += f"\n{'─' * 40}\n"
            if detail.get("attachments"):
                text += f"附件 ({len(detail['attachments'])} 个):\n"
                for att in detail["attachments"]:
                    text += f"  - {att['filename']} ({att['size']} 字节)\n"
            else:
                text += "无附件"
            self.mail_detail_text.insert(tk.END, text)
        else:
            self.mail_detail_text.insert(tk.END, "无法获取邮件详情")
        self.mail_detail_text.config(state=tk.DISABLED)

    def _show_mail_detail_error(self, error: str) -> None:
        self.mail_detail_text.config(state=tk.NORMAL)
        self.mail_detail_text.delete(1.0, tk.END)
        self.mail_detail_text.insert(tk.END, f"加载失败: {error}")
        self.mail_detail_text.config(state=tk.DISABLED)

    def _build_download_record(self, record_dict: dict) -> dict:
        """构建下载记录字典，供 _record_func 闭包复用。

        Args:
            record_dict: 包含下载详细信息的字典。

        Returns:
            标准化格式的下载记录字典。
        """
        return {
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "filename": record_dict.get("filename", ""),
            "subject": record_dict.get("subject", ""),
            "sender": record_dict.get("sender", ""),
            "save_path": record_dict.get("save_path", ""),
            "size": record_dict.get("size", -1),
            "status": record_dict.get("status", "success"),
            "email_uid": record_dict.get("email_uid", ""),
        }

    def _download_query_attachment(self) -> None:
        if not self.query_detail_data or not self.query_detail_data.get("attachments"):
            messagebox.showinfo("提示", "此邮件无附件")
            return

        folder = filedialog.askdirectory(title="选择附件保存目录")
        if not folder:
            return

        detail_snapshot = self.query_detail_data
        self.btn_query_download.config(state=tk.DISABLED, text="下载中...")

        def _record_func(record_dict):
            self.cb.record_download(self._build_download_record(record_dict))

        def on_complete(count):
            self.btn_query_download.config(state=tk.NORMAL, text="下载此邮件附件")
            self.cb.log(f"下载完成: 成功下载 {count} 个附件 -> {folder}", "query")

        def on_error(exc):
            self.btn_query_download.config(state=tk.NORMAL, text="下载此邮件附件")
            self.cb.log(f"下载附件失败: {exc}", "query")

        self.cb.coordinator.run_download_single_attachment(
            detail=detail_snapshot,
            save_folder=folder,
            record_func=_record_func,
            on_complete=on_complete,
            on_error=on_error,
        )

    def _batch_download_query_attachments(self) -> None:
        selection = self.mail_tree.selection()
        if not selection:
            messagebox.showinfo(
                "提示",
                "请先在左侧列表中选中要下载的邮件（支持 Ctrl+点击 多选）",
            )
            return

        folder = filedialog.askdirectory(title="选择批量下载附件保存目录")
        if not folder:
            return

        query_folder = self.combo_query_folder.get()
        total_mails = len(selection)
        self.cb.log("=" * 50, "query")
        self.cb.log(f"开始批量下载: {total_mails} 封邮件的附件...", "query")
        self.btn_batch_download.config(state=tk.DISABLED, text="下载中...")

        def _record_func(record_dict):
            self.cb.record_download(self._build_download_record(record_dict))

        def on_complete(result):
            self.btn_batch_download.config(state=tk.NORMAL, text="批量下载所选附件")
            success_mails = result.get("success_mails", 0)
            total_mails_val = result.get("total_mails", 0)
            total_attachments = result.get("total_attachments", 0)
            failed_mails = result.get("failed_mails", 0)
            status_parts = [f"处理邮件: {success_mails}/{total_mails_val} 封"]
            if failed_mails > 0:
                status_parts.append(f"获取失败: {failed_mails} 封")
            status_parts.append(f"下载附件: {total_attachments} 个")
            self.cb.log(
                f"批量下载完成: {success_mails}/{total_mails_val} 封邮件, "
                f"共 {total_attachments} 个附件", "query",
            )
            self.cb.log("=" * 50, "query")
            self.cb.log(f"批量下载完成: {', '.join(status_parts)}, 保存目录: {folder}", "query")
            if total_attachments == 0:
                self.cb.log("所选邮件均无附件", "query")

        def on_error(exc):
            self.btn_batch_download.config(state=tk.NORMAL, text="批量下载所选附件")
            self.cb.log(f"批量下载失败: {exc}", "query")
            self.cb.log("=" * 50, "query")
            messagebox.showerror("批量下载失败", f"批量下载出错:\n{exc}")

        self.cb.coordinator.run_batch_download_attachments(
            mail_ids=list(selection),
            folder=query_folder,
            save_folder=folder,
            record_func=_record_func,
            on_complete=on_complete,
            on_error=on_error,
        )

    def _mark_selected_as_read(self) -> None:
        selection = self.mail_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中邮件")
            return

        unseen_ids = [
            mid for mid in selection
            if self.mail_tree.item(mid, "values")
            and self.mail_tree.item(mid, "values")[0] == "未读"
        ]
        if not unseen_ids:
            messagebox.showinfo("提示", "所选邮件均已读")
            return

        folder = self.combo_query_folder.get()

        def on_complete(_result):
            for mid in unseen_ids:
                self._on_mark_read_success(mid)
            self.cb.log(
                f"批量标记已读完成: {len(unseen_ids)} 封", "query",
            )

        def on_error(exc):
            messagebox.showerror("错误", f"标记已读失败: {exc}")

        self.cb.coordinator.run_mark_as_read(
            mail_ids=unseen_ids,
            folder=folder,
            on_complete=on_complete,
            on_error=on_error,
        )

    def _on_mark_read_success(self, mail_id: str) -> None:
        values = list(self.mail_tree.item(mail_id, "values"))
        if values:
            values[0] = "已读"
            self.mail_tree.item(mail_id, values=tuple(values), tags=())
        if self.query_detail_data:
            self.query_detail_data["seen"] = True
            self._show_mail_detail(self.query_detail_data)
        self.cb.log(f"邮件 {mail_id} 已标记为已读", "query")

    def _mark_selected_as_unread(self) -> None:
        selection = self.mail_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中邮件")
            return

        seen_ids = [
            mid for mid in selection
            if self.mail_tree.item(mid, "values")
            and self.mail_tree.item(mid, "values")[0] == "已读"
        ]
        if not seen_ids:
            messagebox.showinfo("提示", "所选邮件均已是未读状态")
            return

        folder = self.combo_query_folder.get()

        def on_complete(_result):
            for mid in seen_ids:
                self._on_mark_unread_success(mid)
            self.cb.log(
                f"批量标记未读完成: {len(seen_ids)} 封", "query",
            )

        def on_error(exc):
            messagebox.showerror("错误", f"标记未读失败: {exc}")

        self.cb.coordinator.run_mark_as_unread(
            mail_ids=seen_ids,
            folder=folder,
            on_complete=on_complete,
            on_error=on_error,
        )

    def _on_mark_unread_success(self, mail_id: str) -> None:
        values = list(self.mail_tree.item(mail_id, "values"))
        if values:
            values[0] = "未读"
            self.mail_tree.item(mail_id, values=tuple(values), tags=("unseen",))
        if self.query_detail_data:
            self.query_detail_data["seen"] = False
            self._show_mail_detail(self.query_detail_data)
        self.cb.log(f"邮件 {mail_id} 已标记为未读", "query")

    def _export_query_csv(self) -> None:
        children = self.mail_tree.get_children()
        if not children:
            messagebox.showinfo("提示", "查询列表为空，请先查询邮件")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV文件", "*.csv")],
            initialfile=(
                f"邮件查询结果_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv"
            ),
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["状态", "发件人/收件人", "主题", "日期"])
            for item in children:
                writer.writerow(self.mail_tree.item(item, "values"))
        self.cb.log(f"查询结果已导出到 {path}", "query")

    def _export_query_excel(self) -> None:
        children = self.mail_tree.get_children()
        if not children:
            messagebox.showinfo("提示", "查询列表为空，请先查询邮件")
            return

        path = filedialog.asksaveasfilename(
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
            mail_id = str(item)

            cached = self._query_results_cache.get(mail_id, {})

            has_attachments_str = "—"
            attachment_count_str = "—"

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

        self.cb.log(f"查询结果已导出为Excel -> {path}", "query")