"""统一的后台线程调度器模块。

提供 BackgroundWorker 类，用于将耗时的阻塞操作（网络IO、文件IO）
放到后台线程执行，操作完成后通过 root.after() 回主线程更新 UI。
"""

import threading
import tkinter as tk
from typing import Any, Callable, Optional


class BackgroundWorker:
    """后台工作线程调度器。

    将耗时的阻塞操作放到后台 daemon 线程执行，完成后通过
    root.after() 安全地回到主线程执行回调。

    Attributes:
        root: Tkinter 根窗口，用于 after() 调度。
        on_complete: 成功完成时的回调，签名为 (result) -> None。
        on_error: 发生异常时的回调，签名为 (exception) -> None。
    """

    def __init__(
        self,
        root: tk.Tk,
        on_complete: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """初始化后台工作线程调度器。

        Args:
            root: Tkinter 根窗口实例。
            on_complete: 操作成功完成时的回调，在主线程中执行。
                接收 worker_func 的返回值作为参数。
            on_error: 操作发生异常时的回调，在主线程中执行。
                接收捕获的 Exception 对象作为参数。
        """
        self.root = root
        self.on_complete = on_complete
        self.on_error = on_error

    def run(self, worker_func: Callable, *args: Any, **kwargs: Any) -> None:
        """启动后台线程执行 worker_func。

        worker_func 在后台线程中运行，完成后自动通过
        root.after() 回到主线程执行 on_complete 或 on_error 回调。

        Args:
            worker_func: 要在后台线程中执行的函数。
            *args: 传递给 worker_func 的位置参数。
            **kwargs: 传递给 worker_func 的关键字参数。
        """
        thread = threading.Thread(
            target=self._worker_wrapper,
            args=(worker_func, args, kwargs),
            daemon=True,
        )
        thread.start()

    def _worker_wrapper(
        self,
        worker_func: Callable,
        args: tuple,
        kwargs: dict,
    ) -> None:
        """后台线程执行体，负责调用 worker_func 并捕获异常。

        根据执行结果自动分发到 _handle_result 或 _handle_error。

        Args:
            worker_func: 要在后台线程中执行的函数。
            args: 位置参数元组。
            kwargs: 关键字参数字典。
        """
        try:
            result = worker_func(*args, **kwargs)
        except Exception as exc:
            self.root.after(0, self._handle_error, exc)
        else:
            self.root.after(0, self._handle_result, result)

    def _handle_result(self, result: Any) -> None:
        """在主线程中执行成功回调。

        通过 root.after() 从后台线程调度到主线程执行。
        如果 on_complete 为 None 则跳过。

        Args:
            result: worker_func 的返回值。
        """
        if self.on_complete is not None:
            self.on_complete(result)

    def _handle_error(self, exc: Exception) -> None:
        """在主线程中执行错误回调。

        通过 root.after() 从后台线程调度到主线程执行。
        如果 on_error 为 None 则跳过。

        Args:
            exc: worker_func 中捕获的异常对象。
        """
        if self.on_error is not None:
            self.on_error(exc)