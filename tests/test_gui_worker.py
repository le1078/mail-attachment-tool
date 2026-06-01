"""BackgroundWorker 单元测试模块。

覆盖成功回调、错误回调、回调为 None、daemon 线程、
结果传递、异常传递等所有场景。
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gui_worker import BackgroundWorker


class TestBackgroundWorkerInit:
    """BackgroundWorker 初始化测试。"""

    def test_init_stores_root_and_callbacks(self) -> None:
        """验证 __init__ 正确存储 root 和回调函数。"""
        root = MagicMock()
        on_complete = MagicMock()
        on_error = MagicMock()

        worker = BackgroundWorker(root, on_complete=on_complete, on_error=on_error)

        assert worker.root is root
        assert worker.on_complete is on_complete
        assert worker.on_error is on_error

    def test_init_with_none_callbacks(self) -> None:
        """验证 __init__ 接受 None 回调。"""
        root = MagicMock()
        worker = BackgroundWorker(root)

        assert worker.root is root
        assert worker.on_complete is None
        assert worker.on_error is None


class TestBackgroundWorkerRun:
    """BackgroundWorker.run() 测试。"""

    def test_run_starts_daemon_thread(self) -> None:
        """验证 run() 启动后台 daemon 线程。"""
        root = MagicMock()
        worker = BackgroundWorker(root)

        def dummy_worker() -> None:
            pass

        with patch("threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            worker.run(dummy_worker)

            mock_thread_cls.assert_called_once()
            _, kwargs = mock_thread_cls.call_args
            assert kwargs["daemon"] is True
            mock_thread.start.assert_called_once()

    def test_run_passes_args_and_kwargs(self) -> None:
        """验证 run() 正确传递参数给 worker_func。"""
        root = MagicMock()
        results = []

        def worker_run_capture(worker_func, args, kwargs):
            """拦截 _worker_wrapper 调用，捕获参数。"""
            results.append(("args", args))
            results.append(("kwargs", kwargs))

        worker = BackgroundWorker(root)
        worker._worker_wrapper = worker_run_capture  # type: ignore[method-assign]

        worker.run(lambda a, b, c=0: a + b + c, 1, 2, c=3)

        assert results[0] == ("args", (1, 2))
        assert results[1] == ("kwargs", {"c": 3})


class TestBackgroundWorkerSuccess:
    """BackgroundWorker 成功回调测试。"""

    def test_on_complete_called_with_result(self) -> None:
        """验证 worker 正常返回时 on_complete 被调用。"""
        root = MagicMock()
        root.after = MagicMock()
        complete_calls = []

        def on_complete(result: int) -> None:
            complete_calls.append(result)

        worker = BackgroundWorker(root, on_complete=on_complete)

        def worker_func() -> int:
            return 42

        worker.run(worker_func)

        # 等待后台线程完成
        time.sleep(0.1)

        # root.after(0, ...) 应该被调用
        root.after.assert_called()
        call_args = root.after.call_args_list[0]
        assert call_args[0][0] == 0  # 延迟 0ms

        # 手动调用 _handle_result 模拟主线程回调
        worker._handle_result(42)
        assert complete_calls == [42]

    def test_on_complete_not_called_when_none(self) -> None:
        """验证 on_complete 为 None 时不崩溃。"""
        root = MagicMock()
        root.after = MagicMock()

        worker = BackgroundWorker(root, on_complete=None)

        def worker_func() -> str:
            return "ok"

        worker.run(worker_func)
        time.sleep(0.1)

        # _handle_result 不应崩溃
        worker._handle_result("ok")


class TestBackgroundWorkerError:
    """BackgroundWorker 错误回调测试。"""

    def test_on_error_called_with_exception(self) -> None:
        """验证 worker 抛出异常时 on_error 被调用。"""
        root = MagicMock()
        root.after = MagicMock()
        error_calls = []

        def on_error(exc: Exception) -> None:
            error_calls.append(exc)

        worker = BackgroundWorker(root, on_error=on_error)

        def worker_func() -> None:
            raise ValueError("test error")

        worker.run(worker_func)
        time.sleep(0.1)

        # root.after(0, ...) 应该被调用
        root.after.assert_called()

        # 手动调用 _handle_error 模拟主线程回调
        test_exc = ValueError("test error")
        worker._handle_error(test_exc)
        assert len(error_calls) == 1
        assert isinstance(error_calls[0], ValueError)
        assert str(error_calls[0]) == "test error"

    def test_on_error_not_called_when_none(self) -> None:
        """验证 on_error 为 None 时不崩溃。"""
        root = MagicMock()
        root.after = MagicMock()

        worker = BackgroundWorker(root, on_error=None)

        def worker_func() -> None:
            raise RuntimeError("should be ignored")

        worker.run(worker_func)
        time.sleep(0.1)

        # _handle_error 不应崩溃
        worker._handle_error(RuntimeError("should be ignored"))


class TestBackgroundWorkerCallbacksOnMainThread:
    """验证回调通过 root.after 在主线程执行。"""

    def test_worker_wrapper_calls_root_after_on_success(self) -> None:
        """验证 _worker_wrapper 成功时调用 root.after(0, ...)。"""
        root = MagicMock()
        root.after = MagicMock()
        worker = BackgroundWorker(root)

        worker._worker_wrapper(lambda: "result", (), {})

        root.after.assert_called_once()
        call_args = root.after.call_args
        assert call_args[0][0] == 0
        assert call_args[0][1] == worker._handle_result
        assert call_args[0][2] == "result"

    def test_worker_wrapper_calls_root_after_on_error(self) -> None:
        """验证 _worker_wrapper 异常时调用 root.after(0, ...)。"""
        root = MagicMock()
        root.after = MagicMock()
        worker = BackgroundWorker(root)

        def failing_worker() -> None:
            raise ValueError("fail")

        worker._worker_wrapper(failing_worker, (), {})

        root.after.assert_called_once()
        call_args = root.after.call_args
        assert call_args[0][0] == 0
        assert call_args[0][1] == worker._handle_error
        assert isinstance(call_args[0][2], ValueError)


class TestBackgroundWorkerIntegration:
    """BackgroundWorker 集成测试。"""

    def test_full_workflow_success(self) -> None:
        """端到端测试：成功场景完整流程。"""
        root = MagicMock()
        root.after = MagicMock()
        complete_result = []

        def on_complete(result: int) -> None:
            complete_result.append(result)

        worker = BackgroundWorker(root, on_complete=on_complete)

        def heavy_computation(x: int, y: int) -> int:
            time.sleep(0.01)
            return x * y

        worker.run(heavy_computation, 6, 7)

        # 等待线程完成
        time.sleep(0.2)

        # root.after 应该被调用（后台线程触发）
        assert root.after.called

        # 提取 result 并手动调用 handle 模拟主线程
        call = root.after.call_args_list[0]
        result = call[0][2]
        assert result == 42

        worker._handle_result(result)
        assert complete_result == [42]

    def test_full_workflow_error(self) -> None:
        """端到端测试：错误场景完整流程。"""
        root = MagicMock()
        root.after = MagicMock()
        error_result = []

        def on_error(exc: Exception) -> None:
            error_result.append(type(exc).__name__)

        worker = BackgroundWorker(root, on_error=on_error)

        def failing_work() -> None:
            time.sleep(0.01)
            raise ConnectionError("network timeout")

        worker.run(failing_work)
        time.sleep(0.2)

        # root.after 应该被调用
        assert root.after.called

        call = root.after.call_args_list[0]
        exc = call[0][2]
        assert isinstance(exc, ConnectionError)

        worker._handle_error(exc)
        assert error_result == ["ConnectionError"]

    def test_callbacks_are_optional_both_none(self) -> None:
        """端到端测试：两个回调都为 None 时不崩溃。"""
        root = MagicMock()
        root.after = MagicMock()
        worker = BackgroundWorker(root)

        # 成功场景
        worker.run(lambda: "ok")
        time.sleep(0.1)
        assert root.after.called

        # 错误场景
        root.after.reset_mock()

        def failing() -> None:
            raise RuntimeError("ignored")

        worker.run(failing)
        time.sleep(0.1)
        assert root.after.called

    def test_worker_receives_complex_args(self) -> None:
        """验证 worker_func 正确接收复杂参数。"""
        root = MagicMock()
        root.after = MagicMock()
        received = {}

        def worker_func(a: int, b: str, c: list, d: dict) -> None:
            received["a"] = a
            received["b"] = b
            received["c"] = c
            received["d"] = d

        worker = BackgroundWorker(root)
        worker._worker_wrapper(
            worker_func, (42, "hello"), {"c": [1, 2, 3], "d": {"key": "val"}}
        )
        assert received == {"a": 42, "b": "hello", "c": [1, 2, 3], "d": {"key": "val"}}