import tkinter as tk
from tkinter import ttk, messagebox
import os

from config_manager import load_history, clear_history, MAX_HISTORY_RECORDS
from tabs.base_tab import BaseTab, TabCallbacks


class HistoryTab(BaseTab):
    def __init__(self, parent: ttk.Notebook, callbacks: TabCallbacks):
        super().__init__(parent, callbacks)
        self.hist_search_var = tk.StringVar()
        self.build_ui()
        self._refresh_history_list()

    def build_ui(self):
        # 第一行：搜索
        hist_row1 = ttk.Frame(self.frame)
        hist_row1.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(hist_row1, text="搜索(文件名/主题/发件人):").pack(side=tk.LEFT)
        self.hist_search_var.trace_add("write", self._on_history_search)
        self.entry_hist_search = ttk.Entry(
            hist_row1, textvariable=self.hist_search_var, width=22
        )
        self.entry_hist_search.pack(side=tk.LEFT, padx=3)
        ttk.Button(
            hist_row1, text="查询", command=self._on_history_search_btn, width=6
        ).pack(side=tk.LEFT, padx=2)

        # 第二行：日期筛选
        hist_row2 = ttk.Frame(self.frame)
        hist_row2.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(hist_row2, text="从:").pack(side=tk.LEFT, padx=(0, 2))
        self.entry_hist_date_from = ttk.Entry(hist_row2, width=12, justify=tk.CENTER)
        self.entry_hist_date_from.pack(side=tk.LEFT, padx=2)
        self.entry_hist_date_from.insert(0, "")
        ttk.Label(hist_row2, text="到:").pack(side=tk.LEFT)
        self.entry_hist_date_to = ttk.Entry(hist_row2, width=12, justify=tk.CENTER)
        self.entry_hist_date_to.pack(side=tk.LEFT, padx=2)
        self.entry_hist_date_to.insert(0, "")
        ttk.Label(
            hist_row2, text="(YYYY-MM-DD)", foreground="gray"
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            hist_row2, text="筛选", command=self._on_history_date_filter, width=6
        ).pack(side=tk.LEFT, padx=3)
        ttk.Button(
            hist_row2, text="重置", command=self._on_history_reset_filter, width=6
        ).pack(side=tk.LEFT, padx=2)

        # 第三行：操作按钮
        hist_row3 = ttk.Frame(self.frame)
        hist_row3.pack(fill=tk.X, pady=(2, 0))

        ttk.Button(
            hist_row3, text="刷新", width=8,
            command=lambda: self._refresh_history_list()
        ).pack(side=tk.RIGHT, padx=2)
        ttk.Button(
            hist_row3, text="清空历史", width=10,
            command=self._on_clear_history
        ).pack(side=tk.RIGHT, padx=2)
        ttk.Button(
            hist_row3, text="打开文件夹", width=10,
            command=self._on_open_history_folder
        ).pack(side=tk.RIGHT, padx=2)

        self.hist_tree_frame = ttk.Frame(self.frame)
        self.hist_tree_frame.pack(fill=tk.BOTH, expand=True)

        hist_columns = (
            "time", "filename", "subject", "sender", "save_path", "size", "status"
        )
        self.hist_tree = ttk.Treeview(
            self.hist_tree_frame, columns=hist_columns,
            show="headings", selectmode="browse"
        )
        self.hist_tree.heading("time", text="下载时间", anchor=tk.CENTER)
        self.hist_tree.heading("filename", text="文件名", anchor=tk.CENTER)
        self.hist_tree.heading("subject", text="邮件主题", anchor=tk.CENTER)
        self.hist_tree.heading("sender", text="发件人", anchor=tk.CENTER)
        self.hist_tree.heading("save_path", text="保存路径", anchor=tk.CENTER)
        self.hist_tree.heading("size", text="大小", anchor=tk.CENTER)
        self.hist_tree.heading("status", text="状态", anchor=tk.CENTER)

        self.hist_tree.column("time", width=140, minwidth=60, anchor=tk.CENTER, stretch=True)
        self.hist_tree.column("filename", width=160, minwidth=50, anchor=tk.CENTER, stretch=True)
        self.hist_tree.column("subject", width=180, minwidth=50, anchor=tk.CENTER, stretch=True)
        self.hist_tree.column("sender", width=150, minwidth=50, anchor=tk.CENTER, stretch=True)
        self.hist_tree.column("save_path", width=200, minwidth=60, anchor=tk.CENTER, stretch=True)
        self.hist_tree.column("size", width=80, minwidth=40, anchor=tk.CENTER, stretch=True)
        self.hist_tree.column("status", width=60, minwidth=30, anchor=tk.CENTER, stretch=True)

        hist_scroll_y = ttk.Scrollbar(
            self.hist_tree_frame, orient=tk.VERTICAL, command=self.hist_tree.yview
        )
        hist_scroll_x = ttk.Scrollbar(
            self.hist_tree_frame, orient=tk.HORIZONTAL, command=self.hist_tree.xview
        )
        self.hist_tree.configure(
            yscrollcommand=hist_scroll_y.set, xscrollcommand=hist_scroll_x.set
        )
        self.hist_tree.grid(row=0, column=0, sticky="nsew")
        hist_scroll_y.grid(row=0, column=1, sticky="ns")
        hist_scroll_x.grid(row=1, column=0, sticky="ew")
        self.hist_tree_frame.rowconfigure(0, weight=1)
        self.hist_tree_frame.columnconfigure(0, weight=1)

        self.hist_status_label = ttk.Label(
            self.frame, text="", foreground="gray"
        )
        self.hist_status_label.pack(fill=tk.X, pady=(3, 0))

        self.hist_stat_label = ttk.Label(
            self.frame, text="", foreground="#555"
        )
        self.hist_stat_label.pack(fill=tk.X)

    def _refresh_history_list(self, keyword="", date_from="", date_to=""):
        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)
        records = load_history()
        kw_lower = keyword.lower().strip()
        df = date_from.strip()
        dt = date_to.strip()
        filtered = []
        for r in records:
            if kw_lower:
                match_kw = False
                for field in ("filename", "subject", "sender", "save_path"):
                    if kw_lower in r.get(field, "").lower():
                        match_kw = True
                        break
                if not match_kw:
                    continue
            if df and r.get("time", "")[:10] < df:
                continue
            if dt and r.get("time", "")[:10] > dt:
                continue
            filtered.append(r)

        for r in filtered:
            size_str = self._format_file_size(r.get("size", -1))
            status_display = "\u2713" if r.get("status") == "success" else "\u2717"
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
            self.hist_status_label.config(
                text=f"显示 {shown} / {total} 条记录（已筛选）"
            )
        else:
            self.hist_status_label.config(
                text=f"共 {total} 条记录（上限 {MAX_HISTORY_RECORDS} 条）"
            )

        success_count = sum(1 for r in filtered if r.get("status") == "success")
        failed_count = shown - success_count
        self.hist_stat_label.config(
            text=f"成功: {success_count} 条  |  失败: {failed_count} 条"
        )

    def _on_history_search(self, *args):
        self._refresh_history_list(
            keyword=self.hist_search_var.get(),
            date_from=self.entry_hist_date_from.get().strip(),
            date_to=self.entry_hist_date_to.get().strip()
        )

    def _on_history_search_btn(self):
        self._refresh_history_list(
            keyword=self.hist_search_var.get(),
            date_from=self.entry_hist_date_from.get().strip(),
            date_to=self.entry_hist_date_to.get().strip()
        )

    def _on_history_date_filter(self):
        self._refresh_history_list(
            keyword=self.hist_search_var.get(),
            date_from=self.entry_hist_date_from.get().strip(),
            date_to=self.entry_hist_date_to.get().strip()
        )

    def _on_history_reset_filter(self):
        self.hist_search_var.set("")
        self.entry_hist_date_from.delete(0, tk.END)
        self.entry_hist_date_to.delete(0, tk.END)
        self._refresh_history_list()

    def _on_open_history_folder(self):
        selection = self.hist_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一条记录")
            return
        values = self.hist_tree.item(selection[0], "values")
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
        if not messagebox.askyesno(
            "确认清空", "确定要清空所有下载历史记录吗？\n\n此操作不可恢复。"
        ):
            return
        clear_history()
        self._refresh_history_list()
        self.cb.log("下载历史已清空", "system")

    @staticmethod
    def _format_file_size(size_bytes: int) -> str:
        if size_bytes < 0:
            return "-"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"