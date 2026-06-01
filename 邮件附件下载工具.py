"""邮件附件自动下载 & 定时发送工具"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import datetime
import os
import time
import pystray
from PIL import Image, ImageDraw
from config_manager import (
    CONFIG_FILE, load_config, save_config, validate_config,
    export_config_to, import_config_from, add_history_record,
    MAX_HISTORY_RECORDS,
)
from task_coordinator import TaskCoordinator
from tabs import TabCallbacks, DownloadTab, SendTab, QueryTab, LogTab, HistoryTab


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
        self.last_error_time = {}
        self.retry_state = {}
        self.coordinator = TaskCoordinator(
            self.root, self.config, self.log, record_func=add_history_record)
        self._build_ui()
        self._start_clock_update()
        self.log("程序已启动，请配置参数后点击【启动定时任务】")
        try:
            validate_config(self.config)
        except ValueError as e:
            self.log(f"⚠ 配置校验警告: {e}", "system")

    def _set_config(self, new_config):
        self.config = new_config
        self.coordinator.config = new_config

    def log(self, msg, category="system"):
        self.tabs["log"].add_log(str(msg), category)

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(main_frame, text="邮件附件定时下载 & 邮件定时发送",
                  font=("Microsoft YaHei", 14, "bold")).pack(pady=(0, 5))
        self.time_frame = ttk.Frame(main_frame)
        self.time_frame.pack(fill=tk.X, pady=(0, 8))
        self.time_label = ttk.Label(self.time_frame, text="",
                                    font=("Consolas", 10), foreground="#0078D4")
        self.time_label.pack(side=tk.LEFT)
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        cb = TabCallbacks(
            root=self.root, config=lambda: self.config,
            save_config=self._set_config, log=self.log,
            record_download=add_history_record, coordinator=self.coordinator,
            get_weekday_names=lambda: self.WEEKDAY_NAMES)
        self.tabs = {}
        self.tabs["download"] = DownloadTab(self.notebook, cb)
        self.tabs["send"] = SendTab(self.notebook, cb)
        self.tabs["query"] = QueryTab(self.notebook, cb)
        self.tabs["log"] = LogTab(self.notebook, cb)
        self.tabs["history"] = HistoryTab(self.notebook, cb)
        self.notebook.add(self.tabs["download"].frame, text="下载配置")
        self.notebook.add(self.tabs["send"].frame, text="发送配置")
        self.notebook.add(self.tabs["query"].frame, text="邮件查询")
        self.notebook.add(self.tabs["log"].frame, text="运行日志")
        self.notebook.add(self.tabs["history"].frame, text="下载历史")
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(btn_frame, text="状态:").pack(side=tk.LEFT)
        self.status_label = ttk.Label(btn_frame, text="已停止", foreground="gray")
        self.status_label.pack(side=tk.LEFT, padx=5)
        self.lbl_next_run = ttk.Label(btn_frame, text="", foreground="#0078D4")
        self.lbl_next_run.pack(side=tk.LEFT, padx=10)
        self.btn_toggle = ttk.Button(btn_frame, text="启动定时任务",
                                     command=self._toggle_scheduler)
        self.btn_toggle.pack(side=tk.RIGHT, padx=5)
        self.btn_export_config = ttk.Button(btn_frame, text="导出配置",
                                            command=self._export_config_btn)
        self.btn_export_config.pack(side=tk.RIGHT, padx=2)
        self.btn_import_config = ttk.Button(btn_frame, text="导入配置",
                                            command=self._import_config_btn)
        self.btn_import_config.pack(side=tk.RIGHT, padx=2)

    def _start_clock_update(self):
        self._update_clock()

    def _update_clock(self):
        now = datetime.datetime.now()
        self.time_label.config(
            text=f"当前时间: {now:%Y-%m-%d %H:%M:%S}  {self.WEEKDAY_NAMES[now.weekday()]}")
        parts = []
        if self.running:
            for key, label in [("schedule_download", "下载"), ("schedule_send", "发送")]:
                sc = self.config.get(key, {})
                if sc.get("enabled") and sc.get("days"):
                    dn = ",".join(self.WEEKDAY_NAMES[d - 1] for d in sc["days"])
                    parts.append(
                        f"下次{label}: {dn} {sc['hour']:02d}:{sc['minute']:02d}:{sc['second']:02d}")
        if not parts:
            parts.append("定时任务未启动")
        self.lbl_next_run.config(text=" | ".join(parts))
        self.root.after(1000, self._update_clock)

    def _toggle_scheduler(self):
        if self.running:
            self._stop_scheduler()
        else:
            self._start_scheduler()

    def _start_scheduler(self):
        self.tabs["download"]._save_download_config()
        self.tabs["send"]._save_send_config()
        save_config(self.config)
        cfg = self.config
        dl_enabled = cfg.get("schedule_download", {}).get("enabled", False)
        sd_enabled = cfg.get("schedule_send", {}).get("enabled", False)
        if not dl_enabled and not sd_enabled:
            messagebox.showerror("错误", "请至少启用一个定时任务（下载或发送）")
            return
        if dl_enabled:
            dl_days = cfg["schedule_download"].get("days", [])
            if not dl_days and not messagebox.askyesno(
                    "警告", "下载定时任务已启用但未勾选任何执行日期\n\n仍然启动？"):
                return
            if not cfg.get("email_user") or not cfg.get("email_pass"):
                messagebox.showerror("错误", "下载任务已启用，请填写邮箱账号和密码"); return
            if not (cfg.get("sender_filter_list")
                    or cfg.get("download_keyword_filter", "").strip()):
                messagebox.showerror("错误", "下载任务已启用，请填写发件人筛选或主题关键词"); return
            if not cfg.get("save_folder"):
                messagebox.showerror("错误", "下载任务已启用，请选择附件保存目录"); return
            os.makedirs(cfg["save_folder"], exist_ok=True)
        if sd_enabled:
            sd_days = cfg["schedule_send"].get("days", [])
            if not sd_days and not messagebox.askyesno(
                    "警告", "发送定时任务已启用但未勾选任何执行日期\n\n仍然启动？"):
                return
            send_user = cfg.get("send_user") or cfg.get("email_user")
            send_pass = cfg.get("send_pass") or cfg.get("email_pass")
            if not send_user or not send_pass:
                messagebox.showerror("错误", "发送任务已启用，请填写发件人账号和密码"); return
            if not cfg.get("send_to"):
                messagebox.showerror("错误", "发送任务已启用，请填写收件人"); return
        self.running = True
        self.stop_event.clear()
        self.btn_toggle.config(text="停止定时任务")
        self.status_label.config(text="状态: 运行中", foreground="green")
        self.retry_state = {}
        self.log("定时任务已启动")
        self.scheduler_thread = threading.Thread(
            target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()

    def _stop_scheduler(self):
        self.running = False
        self.stop_event.set()
        self.btn_toggle.config(text="启动定时任务")
        self.status_label.config(text="状态: 已停止", foreground="gray")
        self.log("定时任务已停止")

    def _scheduler_loop(self):
        cfg = self.config
        last_dl = last_sd = last_export = None
        while not self.stop_event.is_set():
            now = datetime.datetime.now()
            today, wd = now.date(), now.isoweekday()
            dl = cfg.get("schedule_download", {})
            if (dl.get("enabled") and dl.get("days") and wd in set(dl["days"])
                    and (now.hour, now.minute, now.second)
                    == (dl.get("hour", 0), dl.get("minute", 0), dl.get("second", 0))
                    and last_dl != today):
                last_dl = today
                self.root.after(0, self._execute_download)
            sd = cfg.get("schedule_send", {})
            if (sd.get("enabled") and sd.get("days") and wd in set(sd["days"])
                    and (now.hour, now.minute, now.second)
                    == (sd.get("hour", 0), sd.get("minute", 0), sd.get("second", 0))
                    and last_sd != today):
                last_sd = today
                self.root.after(0, self._execute_send)
            if (cfg.get("log_export_enabled") and cfg.get("log_export_days")
                    and wd in set(cfg["log_export_days"])
                    and (now.hour, now.minute, now.second)
                    == (cfg.get("log_export_hour", 23),
                        cfg.get("log_export_minute", 59),
                        cfg.get("log_export_second", 0))
                    and last_export != today):
                last_export = today
                self.root.after(0, self.tabs["log"].auto_export_log)
            if cfg.get("retry_enabled"):
                self._check_retry()
            time.sleep(0.5)

    def _check_retry(self):
        cfg = self.config
        now = time.time()
        for task_type, state in list(self.retry_state.items()):
            if state["fail_count"] >= cfg.get("retry_count", 3):
                del self.retry_state[task_type]
            elif (now - state["last_fail"]) / 60.0 >= cfg.get("retry_interval_minutes", 5):
                self.retry_state[task_type]["last_fail"] = now
                self.root.after(0, lambda t=task_type: self._retry_task(t))

    def _retry_task(self, task_type):
        state = self.retry_state.get(task_type, {})
        self.log(
            f"正在重试{task_type}任务 (第{state.get('fail_count', 0)}次失败后)...", task_type)
        if task_type == "download":
            self._execute_download()
        elif task_type == "send":
            self._execute_send()

    def _execute_download(self):
        self.log("=" * 50, "download")
        self.log("定时任务触发，开始下载...", "download")

        def on_complete(count):
            self.log(f"本次下载了 {count} 个附件", "download")
            if count == 0:
                self.log("没有新的匹配附件", "download")
            self.retry_state.pop("download", None)
            self.log("=" * 50, "download")

        def on_error(exc):
            self.log(f"下载任务执行出错: {exc}", "download")
            self._handle_task_error("download", str(exc))
            st = self.retry_state.setdefault(
                "download", {"fail_count": 0, "last_fail": 0})
            st["fail_count"] += 1
            st["last_fail"] = time.time()
            self.log("=" * 50, "download")

        self.coordinator.run_download_once(
            on_complete=on_complete, on_error=on_error)

    def _execute_send(self):
        self.log("=" * 50, "send")
        self.log("定时发送触发...", "send")

        def on_complete(_result):
            self.log("发送成功！", "send")
            self.retry_state.pop("send", None)
            self.log("=" * 50, "send")

        def on_error(exc):
            self.log(f"发送任务执行出错: {exc}", "send")
            self._handle_task_error("send", str(exc))
            st = self.retry_state.setdefault(
                "send", {"fail_count": 0, "last_fail": 0})
            st["fail_count"] += 1
            st["last_fail"] = time.time()
            self.log("=" * 50, "send")

        self.coordinator.run_send_once(
            on_complete=on_complete, on_error=on_error)

    def _handle_task_error(self, task_type, error_msg):
        error_key = f"{task_type}_{error_msg[:50]}"
        now = time.time()
        if error_key in self.last_error_time \
                and now - self.last_error_time[error_key] < 300:
            return
        self.last_error_time[error_key] = now
        self.root.after(100, lambda: messagebox.showerror(
            f"定时任务失败 - {task_type}",
            f"{task_type}任务执行失败，请检查运行日志。\n\n{error_msg[:200]}"))

    def _export_config_btn(self):
        path = filedialog.asksaveasfilename(
            title="导出配置文件", defaultextension=".json",
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")],
            initialfile=f"邮件工具配置_{datetime.datetime.now():%Y%m%d_%H%M%S}.json")
        if not path:
            return
        try:
            export_config_to(self.config, path)
            self.log(f"配置已导出 -> {path}")
            messagebox.showinfo("导出成功", f"配置已导出到:\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _import_config_btn(self):
        path = filedialog.askopenfilename(
            title="导入配置文件",
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")])
        if not path or not messagebox.askyesno(
                "确认导入", "导入配置将覆盖当前设置，是否继续？"):
            return
        try:
            new_config = import_config_from(path)
            self._set_config(new_config)
            save_config(self.config)
            self.tabs["download"]._load_config_to_ui()
            self.tabs["send"]._load_config_to_ui()
            self.log(f"配置已导入 -> {path}")
            messagebox.showinfo("导入成功", "配置已从文件导入，请检查各项设置。")
        except Exception as e:
            messagebox.showerror("导入失败", f"无法读取配置文件:\n{e}")

    def _create_tray_image(self):
        img = Image.new("RGB", (64, 64), (0, 120, 212))
        draw = ImageDraw.Draw(img)
        draw.rectangle([8, 18, 56, 46], fill="white", outline="white")
        draw.polygon([(8, 18), (32, 32), (56, 18)], fill=(0, 120, 212))
        draw.polygon([(8, 46), (32, 32), (56, 46)], fill="white")
        draw.ellipse([20, 22, 44, 42], outline=(0, 120, 212), width=2)
        draw.text((24, 26), "M", fill=(0, 120, 212))
        return img

    def _hide_to_tray(self):
        self.root.withdraw()
        if self.tray_icon is None:
            self.tray_icon = pystray.Icon(
                "mail_tool", self._create_tray_image(), "邮件自动化工具HK",
                menu=pystray.Menu(
                    pystray.MenuItem("显示窗口", self._show_window, default=True),
                    pystray.MenuItem("退出程序", self._tray_exit)))
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _show_window(self, icon=None):
        self.root.after(0, self._restore_window)

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_exit(self, icon=None):
        if self.tray_icon:
            self.tray_icon.stop()
        if self.running:
            self._stop_scheduler()
        self.tabs["log"].persist()
        self.root.after(0, self.root.destroy)

    def on_close(self):
        self.log(f"[{datetime.datetime.now():%H:%M:%S}] 窗口已最小化到系统托盘，程序在后台运行中")
        self._hide_to_tray()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()


if __name__ == "__main__":
    app = MailAttachmentTool()
    app.run()