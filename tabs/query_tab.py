import tkinter as tk
from tkinter import scrolledtext, ttk

from tabs.base_tab import BaseTab, TabCallbacks
from tabs.query_actions import QueryActionsMixin


class QueryTab(BaseTab, QueryActionsMixin):
    def __init__(self, parent: ttk.Notebook, callbacks: TabCallbacks) -> None:
        super().__init__(parent, callbacks)
        self.querying: bool = False
        self._query_results_cache: dict[str, dict[str, str]] = {}
        self.query_detail_data: dict | None = None
        self._tree_sort_col: str | None = None
        self._tree_sort_reverse: bool = False
        self.build_ui()

    def build_ui(self) -> None:
        query_ctrl1 = ttk.Frame(self.frame)
        query_ctrl1.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(query_ctrl1, text="文件夹:").pack(side=tk.LEFT)
        self.query_folder_var = tk.StringVar(value="INBOX")
        self.combo_query_folder = ttk.Combobox(
            query_ctrl1, width=15, state="readonly",
            values=["INBOX", "已发送"],
        )
        self.combo_query_folder.pack(side=tk.LEFT, padx=3)
        self.combo_query_folder.set("INBOX")

        ttk.Label(query_ctrl1, text="搜索关键词:").pack(side=tk.LEFT, padx=(10, 0))
        self.entry_query_keyword = ttk.Entry(query_ctrl1, width=20)
        self.entry_query_keyword.pack(side=tk.LEFT, padx=3)

        self.var_search_body = tk.BooleanVar(value=False)
        self.chk_search_body = ttk.Checkbutton(
            query_ctrl1, text="搜索正文", variable=self.var_search_body,
        )
        self.chk_search_body.pack(side=tk.LEFT, padx=3)

        ttk.Label(query_ctrl1, text="已读/未读:").pack(side=tk.LEFT, padx=(10, 0))
        self.combo_read_filter = ttk.Combobox(
            query_ctrl1, width=6, state="readonly",
            values=["全部", "已读", "未读"],
        )
        self.combo_read_filter.pack(side=tk.LEFT, padx=3)
        self.combo_read_filter.set("全部")

        self.btn_query_mail = ttk.Button(
            query_ctrl1, text="查询", command=self._do_mail_query, width=8,
        )
        self.btn_query_mail.pack(side=tk.LEFT, padx=3)
        self.btn_refresh_mail = ttk.Button(
            query_ctrl1, text="刷新列表", command=self._do_mail_query, width=8,
        )
        self.btn_refresh_mail.pack(side=tk.LEFT, padx=3)
        self.btn_sent_mail = ttk.Button(
            query_ctrl1, text="查看已发送", command=self._do_sent_mail_query, width=10,
        )
        self.btn_sent_mail.pack(side=tk.LEFT, padx=3)

        query_ctrl2 = ttk.Frame(self.frame)
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
        self.spin_query_count = ttk.Spinbox(
            query_ctrl2, from_=10, to=200, width=5, justify=tk.CENTER,
        )
        self.spin_query_count.pack(side=tk.LEFT, padx=(2, 5))
        self.spin_query_count.set("50")

        query_ctrl3 = ttk.Frame(self.frame)
        query_ctrl3.pack(fill=tk.X, pady=(3, 3))
        ttk.Button(
            query_ctrl3, text="全选", command=self._select_all_mails, width=6,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            query_ctrl3, text="取消全选", command=self._deselect_all_mails, width=8,
        ).pack(side=tk.LEFT, padx=2)
        self.lbl_selected_count = ttk.Label(query_ctrl3, text="", foreground="#0078D4")
        self.lbl_selected_count.pack(side=tk.LEFT, padx=(10, 0))
        self.btn_export_excel = ttk.Button(
            query_ctrl3, text="导出Excel",
            command=self._export_query_excel, width=12,
        )
        self.btn_export_excel.pack(side=tk.RIGHT, padx=2)
        self.btn_batch_download = ttk.Button(
            query_ctrl3, text="批量下载所选附件",
            command=self._batch_download_query_attachments, width=18,
        )
        self.btn_batch_download.pack(side=tk.RIGHT, padx=2)

        query_paned = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        query_paned.pack(fill=tk.BOTH, expand=True)

        list_frame = ttk.Frame(query_paned)
        query_paned.add(list_frame, weight=3)

        columns = ("状态", "发件人/收件人", "主题", "日期")
        self.mail_tree = ttk.Treeview(
            list_frame, columns=columns, show="headings",
            selectmode="extended", height=15,
        )
        self.mail_tree.heading(
            "状态", text="状态",
            command=lambda: self._sort_treeview("状态"),
        )
        self.mail_tree.heading(
            "发件人/收件人", text="发件人/收件人",
            command=lambda: self._sort_treeview("发件人/收件人"),
        )
        self.mail_tree.heading(
            "主题", text="主题",
            command=lambda: self._sort_treeview("主题"),
        )
        self.mail_tree.heading(
            "日期", text="日期",
            command=lambda: self._sort_treeview("日期"),
        )
        self.mail_tree.column("状态", width=48, anchor=tk.CENTER)
        self.mail_tree.column("发件人/收件人", width=150)
        self.mail_tree.column("主题", width=190)
        self.mail_tree.column("日期", width=110)
        self.mail_tree.tag_configure("unseen", font=("", 9, "bold"))
        self.mail_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll = ttk.Scrollbar(list_frame, command=self.mail_tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.mail_tree.config(yscrollcommand=tree_scroll.set)
        self.mail_tree.bind("<<TreeviewSelect>>", self._on_mail_select)

        self._tree_context_menu = tk.Menu(self.mail_tree, tearoff=0)
        self._tree_context_menu.add_command(
            label="标记已读", command=self._mark_selected_as_read,
        )
        self._tree_context_menu.add_command(
            label="标记未读", command=self._mark_selected_as_unread,
        )
        self._tree_context_menu.add_command(
            label="下载所选邮件附件", command=self._download_query_attachment,
        )
        self._tree_context_menu.add_command(
            label="批量下载所选邮件附件",
            command=self._batch_download_query_attachments,
        )
        self._tree_context_menu.add_separator()
        self._tree_context_menu.add_command(
            label="导出查询结果为CSV", command=self._export_query_csv,
        )
        self._tree_context_menu.add_command(
            label="导出查询结果为Excel", command=self._export_query_excel,
        )
        self.mail_tree.bind("<Button-3>", self._on_tree_right_click)

        detail_frame = ttk.Frame(query_paned)
        query_paned.add(detail_frame, weight=2)

        ttk.Label(
            detail_frame, text="邮件详情", font=("", 10, "bold"),
        ).pack(anchor=tk.W, pady=(0, 5))
        self.mail_detail_text = scrolledtext.ScrolledText(
            detail_frame, wrap=tk.WORD, font=("Consolas", 9),
            state=tk.DISABLED, height=20,
        )
        self.mail_detail_text.pack(fill=tk.BOTH, expand=True)

        detail_btn_frame = ttk.Frame(detail_frame)
        detail_btn_frame.pack(fill=tk.X, pady=(5, 0))
        self.btn_query_download = ttk.Button(
            detail_btn_frame, text="下载此邮件附件",
            command=self._download_query_attachment, width=16,
        )
        self.btn_query_download.pack(side=tk.LEFT, padx=3)
        ttk.Button(
            detail_btn_frame, text="标记已读",
            command=self._mark_selected_as_read, width=10,
        ).pack(side=tk.LEFT, padx=3)
        ttk.Button(
            detail_btn_frame, text="标记未读",
            command=self._mark_selected_as_unread, width=10,
        ).pack(side=tk.LEFT, padx=3)
        ttk.Button(
            detail_btn_frame, text="导出CSV",
            command=self._export_query_csv, width=8,
        ).pack(side=tk.LEFT, padx=3)
        ttk.Button(
            detail_btn_frame, text="导出Excel",
            command=self._export_query_excel, width=9,
        ).pack(side=tk.LEFT, padx=3)

    def _update_selected_count(self) -> None:
        count = len(self.mail_tree.selection())
        if count > 0:
            self.lbl_selected_count.config(text=f"已选 {count} 封")
        else:
            self.lbl_selected_count.config(text="")

    def _select_all_mails(self) -> None:
        for item in self.mail_tree.get_children():
            self.mail_tree.selection_add(item)
        self._update_selected_count()

    def _deselect_all_mails(self) -> None:
        self.mail_tree.selection_remove(*self.mail_tree.selection())
        self._update_selected_count()

    def _sort_treeview(self, col: str) -> None:
        if self._tree_sort_col == col:
            self._tree_sort_reverse = not self._tree_sort_reverse
        else:
            self._tree_sort_reverse = False
        self._tree_sort_col = col
        col_idx = {"状态": 0, "发件人/收件人": 1, "主题": 2, "日期": 3}
        idx = col_idx.get(col, 0)
        items = [
            (self.mail_tree.set(k, col), k)
            for k in self.mail_tree.get_children("")
        ]
        items.sort(reverse=self._tree_sort_reverse, key=lambda x: x[0].lower())
        for i, (_, item) in enumerate(items):
            self.mail_tree.move(item, "", i)

    def _on_tree_right_click(self, event) -> None:
        iid = self.mail_tree.identify_row(event.y)
        if iid:
            if iid not in self.mail_tree.selection():
                self.mail_tree.selection_set(iid)
            self._tree_context_menu.tk_popup(event.x_root, event.y_root)