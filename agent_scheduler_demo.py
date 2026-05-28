#!/usr/bin/env python3
"""TPM 子代理动态拆分调度演示脚本.

模拟 TPM（技术项目经理）根据任务复杂度（测试用例数量）动态拆分子任务，
并行启动多个 QA 子代理执行，支持超时裂变和结果汇总。
"""

import itertools
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

# ============================================================================
# 配置常量
# ============================================================================

# 拆分规则: (用例区间) -> 拆分份数
SPLIT_RULES: List[Tuple[Tuple[int, int], int]] = [
    ((0, 20), 1),
    ((21, 50), 2),
    ((51, 100), 3),
    ((101, 99_999_999), 4),
]

SIM_TIMEOUT: float = 1.5              # 模拟超时阈值（对应真实场景 45s）
MIN_SPLIT_SIZE: int = 2                # 最小可拆分用例数
MAX_SPLIT_PARTS: int = 3               # 单次裂变最大拆分份数
SERIAL_TIME_PER_TEST: float = 0.018    # 单用例串行预估耗时（秒）
SIM_MIN_DURATION: float = 0.3          # 模拟执行最短耗时（秒）
SIM_MAX_DURATION: float = 2.2          # 模拟执行最长耗时（秒）
MONITOR_POLL_INTERVAL: float = 0.03    # 监控轮询间隔（秒）


# ============================================================================
# 终端风格工具
# ============================================================================

class Style:
    """终端 ANSI 样式代码."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


def cprint(text: str, style: str = "", end: str = "\n") -> None:
    """带样式输出到终端.

    Args:
        text: 要输出的文本.
        style: ANSI 样式字符串.
        end: 行尾字符.
    """
    print(f"{style}{text}{Style.RESET}", end=end, flush=True)


def make_bar(percent: float, width: int = 24) -> str:
    """生成 ASCII 进度条.

    Args:
        percent: 进度百分比 (0.0 ~ 1.0).
        width: 进度条宽度（字符数）.

    Returns:
        格式化的进度条字符串.
    """
    filled = int(round(width * min(percent, 1.0)))
    return f"[{'█' * filled}{'░' * (width - filled)}] {percent * 100:5.1f}%"


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class SubTask:
    """子任务描述."""

    index: int
    name: str
    test_count: int


@dataclass
class AgentResult:
    """子代理执行结果."""

    agent_id: str
    task: SubTask
    start_time: float
    end_time: float
    status: str  # 'success' | 'timeout_split'
    children: List["AgentResult"] = field(default_factory=list)

    @property
    def duration(self) -> float:
        """执行耗时."""
        return self.end_time - self.start_time

    @property
    def is_leaf(self) -> bool:
        """是否为叶子结果（未被裂变）."""
        return len(self.children) == 0


# ============================================================================
# 子代理（线程模拟）
# ============================================================================

class SubAgent(threading.Thread):
    """模拟的 QA 子代理线程.

    随机休眠一段时间模拟测试执行，超过阈值则标记为超时。
    """

    _id_gen = itertools.count(1)

    def __init__(
        self,
        task: SubTask,
        on_done: Callable[["AgentResult"], None],
    ) -> None:
        """初始化子代理.

        Args:
            task: 要执行的子任务.
            on_done: 完成回调，接收 AgentResult.
        """
        super().__init__(daemon=True)
        self.task = task
        self._on_done = on_done
        self.agent_id = f"QA-{next(self._id_gen)}"
        self._start: float = 0.0

    def run(self) -> None:
        """执行任务."""
        self._start = time.time()

        # 模拟执行耗时: 0.3s ~ 2.2s 均匀分布
        duration = random.uniform(SIM_MIN_DURATION, SIM_MAX_DURATION)
        time.sleep(duration)

        end = time.time()
        status = "timeout_split" if (end - self._start) > SIM_TIMEOUT else "success"

        self._on_done(
            AgentResult(
                agent_id=self.agent_id,
                task=self.task,
                start_time=self._start,
                end_time=end,
                status=status,
            )
        )


# ============================================================================
# TPM 分类器与拆分器
# ============================================================================

class TPMClassifier:
    """TPM 分类器：根据任务规模判定拆分份数."""

    @staticmethod
    def decide_parts(count: int) -> int:
        """根据用例数决定拆分份数.

        Args:
            count: 用例总数.

        Returns:
            拆分份数.
        """
        for (lo, hi), parts in SPLIT_RULES:
            if lo <= count <= hi:
                return parts
        return 1


class TaskSplitter:
    """任务拆分器：将总量均分为若干子任务."""

    @staticmethod
    def make_tasks(
        total: int, parts: int, label_prefix: str = ""
    ) -> List[SubTask]:
        """将总用例数均分为若干子任务.

        Args:
            total: 总用例数.
            parts: 拆分份数.
            label_prefix: 任务名前缀（裂变时使用）.

        Returns:
            子任务列表.
        """
        base = total // parts
        rem = total % parts
        tasks: List[SubTask] = []
        cursor = 1
        for i in range(parts):
            size = base + (1 if i < rem else 0)
            end = cursor + size - 1
            tasks.append(SubTask(
                index=i + 1,
                name=f"{label_prefix}Case_{cursor:03d}-{end:03d}",
                test_count=size,
            ))
            cursor = end + 1
        return tasks


# ============================================================================
# TPM 调度器
# ============================================================================

class TPMScheduler:
    """TPM 主调度器.

    负责：并行派发 → 监控超时裂变 → 汇总报告.
    通过组合 TPMClassifier / TaskSplitter 完成分类与拆分.
    """

    def __init__(self, total_tests: int) -> None:
        """初始化调度器.

        Args:
            total_tests: 总测试用例数.
        """
        self.total_tests = total_tests
        self._lock = threading.Lock()
        self._results: List[AgentResult] = []
        self._split_log: List[Dict] = []
        self._split_id_gen = itertools.count(1)

    # ---- 回调 ----

    def _on_agent_done(self, result: AgentResult) -> None:
        """子代理完成回调（线程安全）."""
        with self._lock:
            self._results.append(result)

    # ---- 渲染 ----

    def _print_header(self) -> None:
        """打印头部横幅."""
        cprint("=" * 64, Style.CYAN)
        cprint("  Elite Software Corp — TPM 动态调度演示系统", Style.BOLD + Style.CYAN)
        cprint("=" * 64, Style.CYAN)
        print()
        cprint(f"  [TPM] 收到任务: 运行 {self.total_tests} 个测试用例", Style.WHITE)
        print()

    def _print_split_table(self, tasks: List[SubTask]) -> None:
        """打印初始拆分表格.

        Args:
            tasks: 拆分后的子任务列表.
        """
        parts = len(tasks)
        cprint("  ┌──────────────────────────────────────────────────┐", Style.DIM)
        cprint(f"  │  TPM 判定: {self.total_tests:>4d} 个用例  ->  拆分 {parts} 份                     │", Style.DIM)
        cprint("  ├──────┬──────────────────────┬──────────┬──────────┤", Style.DIM)
        cprint("  │  ID  │ 任务名称             │  用例数   │  占比    │", Style.DIM)
        cprint("  ├──────┼──────────────────────┼──────────┼──────────┤", Style.DIM)
        for t in tasks:
            pct = t.test_count / self.total_tests * 100
            cprint(f"  │ {t.index:>4d} │ {t.name:<20s} │ {t.test_count:>8d} │ {pct:>7.1f}% │", Style.DIM)
        cprint("  └──────┴──────────────────────┴──────────┴──────────┘", Style.DIM)
        print()

    def _print_agent_result(self, result: AgentResult) -> None:
        """打印单个代理的执行结果.

        Args:
            result: 代理执行结果.
        """
        if result.status == "success":
            icon, color = "+", Style.GREEN
        else:
            icon, color = "!", Style.YELLOW

        cprint(
            f"   [{icon}] [{result.agent_id}] {result.task.name}  "
            f"| {result.task.test_count:>3d}用例 | {result.duration:.2f}s | {result.status}",
            color,
        )

    def _on_timeout_split(self, result: AgentResult) -> List[SubAgent]:
        """处理超时裂变：将超时代理的任务拆分为更小的子任务.

        Args:
            result: 超时的代理结果.

        Returns:
            新创建的子代理列表.
        """
        if result.task.test_count <= MIN_SPLIT_SIZE:
            cprint(f"     -> 用例数 {result.task.test_count} 已达最小阈值，不再拆分", Style.DIM)
            return []

        new_parts = min(MAX_SPLIT_PARTS, result.task.test_count)
        new_tasks = TaskSplitter.make_tasks(
            result.task.test_count, new_parts,
            label_prefix=f"Split{next(self._split_id_gen)}_",
        )

        cprint(
            f"     !! 超时裂变: {result.agent_id} ({result.task.test_count}用例, "
            f"{result.duration:.2f}s) -> 拆为 {new_parts} 份",
            Style.YELLOW,
        )

        self._split_log.append({
            "src": result.agent_id,
            "tests": result.task.test_count,
            "dur": result.duration,
            "into": new_parts,
        })

        agents: List[SubAgent] = []
        for t in new_tasks:
            a = SubAgent(t, self._on_agent_done)
            result.children.append(a)  # 引用标记，实际结果通过回调收集
            agents.append(a)
        return agents

    # ---- 汇总 ----

    def _print_agent_bars(self, leaf_results: List[AgentResult]) -> None:
        """打印各代理耗时进度条（按耗时降序）.

        Args:
            leaf_results: 叶子结果列表.
        """
        for r in sorted(leaf_results, key=lambda x: x.duration, reverse=True):
            bar = make_bar(min(r.duration / SIM_MAX_DURATION, 1.0), 18)
            status_icon = "+" if r.status == "success" else "!"
            cprint(
                f"  │  [{status_icon}] [{r.agent_id}] {r.task.name:<20s} {bar} {r.duration:.2f}s │",
                Style.DIM,
            )

    def _print_split_details(self) -> None:
        """打印裂变事件详情."""
        if not self._split_log:
            return
        cprint("\n  * 裂变事件详情:", Style.YELLOW)
        for evt in self._split_log:
            cprint(
                f"    - {evt['src']}: {evt['tests']}用例 "
                f"耗时{evt['dur']:.2f}s -> 裂变为 {evt['into']} 份",
                Style.YELLOW,
            )

    def _print_summary(
        self,
        leaf_results: List[AgentResult],
        all_results: List[AgentResult],
        start: float,
        end: float,
    ) -> None:
        """打印最终汇总报告.

        Args:
            leaf_results: 叶子结果列表（实际完成工作的代理）.
            all_results: 全部结果列表（含被裂变的父代理）.
            start: 整体开始时间.
            end: 整体结束时间.
        """
        total_dur = end - start
        serial_est = self.total_tests * SERIAL_TIME_PER_TEST

        success_n = sum(1 for r in leaf_results if r.status == "success")
        timeout_n = len(leaf_results) - success_n

        cprint("  ┌──────────────────────────────────────────────────────────┐", Style.DIM)
        cprint("  │                     SUMMARY  REPORT                      │", Style.DIM)
        cprint("  ├──────────────────────────────────────────────────────────┤", Style.DIM)
        cprint(f"  │  总用例数:         {self.total_tests:>6d}                                  │", Style.DIM)
        cprint(f"  │  出动代理总数:     {len(all_results):>6d}                                  │", Style.DIM)
        cprint(f"  │  成功完成:         {success_n:>6d}                                  │", Style.DIM)
        cprint(f"  │  超时(未拆分):     {timeout_n:>6d}                                  │", Style.DIM)
        cprint(f"  │  裂变事件:         {len(self._split_log):>6d}                                  │", Style.DIM)
        cprint(f"  │  总耗时:           {total_dur:>7.2f}s                                │", Style.DIM)
        cprint(f"  │  串行预估:         {serial_est:>7.2f}s                                │", Style.DIM)
        speedup = serial_est / total_dur if total_dur > 0 else 0.0
        cprint(f"  │  加速比:           {speedup:>7.2f}x                                │", Style.DIM)
        cprint("  ├──────────────────────────────────────────────────────────┤", Style.DIM)
        cprint("  │  各代理耗时 (已排序):                                    │", Style.DIM)

        self._print_agent_bars(leaf_results)

        cprint("  └──────────────────────────────────────────────────────────┘", Style.DIM)
        self._print_split_details()

        cprint(f"\n  [TPM] 调度完成! 并行执行总耗时 {total_dur:.2f}s", Style.BOLD + Style.GREEN)
        cprint("=" * 64, Style.CYAN)

    # ---- 主流程 ----

    def _monitor_loop(self, pending: List[SubAgent]) -> None:
        """监控循环：收集结果并处理超时裂变.

        Args:
            pending: 初始待监控的代理列表（会被原地修改）.
        """
        consumed = 0

        while pending or len(self._results) > consumed:
            with self._lock:
                fresh = self._results[consumed:]
                consumed = len(self._results)

            for r in fresh:
                self._print_agent_result(r)
                if r.status == "timeout_split":
                    new_agents = self._on_timeout_split(r)
                    for na in new_agents:
                        na.start()
                        pending.append(na)
                        cprint(
                            f"      >> [{na.agent_id}] 裂变启动: {na.task.name} "
                            f"({na.task.test_count} 用例)",
                            Style.MAGENTA,
                        )

            pending[:] = [a for a in pending if a.is_alive()]
            time.sleep(MONITOR_POLL_INTERVAL)

    def execute(self) -> Dict:
        """执行完整调度流程.

        Returns:
            执行统计字典.
        """
        self._print_header()

        parts = TPMClassifier.decide_parts(self.total_tests)
        tasks = TaskSplitter.make_tasks(self.total_tests, parts)
        self._print_split_table(tasks)

        cprint("  === Phase 1: 并行执行 ===", Style.BLUE)
        cprint(f"  启动 {parts} 个子代理...\n", Style.WHITE)

        overall_start = time.time()

        pending: List[SubAgent] = []
        for t in tasks:
            a = SubAgent(t, self._on_agent_done)
            pending.append(a)
            cprint(f"    >> [{a.agent_id}] 开始: {t.name} ({t.test_count} 用例)", Style.CYAN)

        for a in pending:
            a.start()

        self._monitor_loop(pending)

        overall_end = time.time()

        with self._lock:
            all_results = list(self._results)

        leaf_results = [r for r in all_results if r.is_leaf]

        cprint("\n  === Phase 2: 汇总报告 ===", Style.BLUE)
        self._print_summary(leaf_results, all_results, overall_start, overall_end)

        return {
            "total_tests": self.total_tests,
            "initial_parts": parts,
            "total_leaf_agents": len(leaf_results),
            "split_events": len(self._split_log),
            "duration": overall_end - overall_start,
        }


# ============================================================================
# 入口
# ============================================================================

def main() -> None:
    """入口函数：创建 TPM 调度器并执行 200 用例模拟."""
    random.seed(42)

    scheduler = TPMScheduler(total_tests=200)
    result = scheduler.execute()

    # 最终断言
    assert result["total_tests"] == 200, "用例总数不一致"
    assert result["duration"] > 0, "执行耗时应大于 0"
    print(f"\n[OK] 模拟完成，最终代理数: {result['total_leaf_agents']}")


if __name__ == "__main__":
    main()