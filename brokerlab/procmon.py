"""跨平台的轻量级进程内存采样。

为什么要自己采：getrusage(RUSAGE_CHILDREN).ru_maxrss 报的是【单个】被等待
子进程的峰值，不是进程树的总和。要回答"50 个 broker 进程实际吃了多少 GB"，
只能逐个读取进程的常驻内存再求和。它还兼任内存看门狗：
总和越过上限就中止实验（退出码 2），好过被系统 OOM 杀掉后报出一个假上限。
"""
import os
import threading
import time
import typing as t

import psutil


def vmrss_kb(pid: int) -> t.Optional[int]:
    """读取单个进程的常驻内存 RSS（KB）；进程不可用时返回 None。"""
    try:
        return psutil.Process(pid).memory_info().rss // 1024
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


class RSSWatchdog(threading.Thread):
    """后台线程：周期性求和被跟踪进程的 RSS；超上限置 .tripped 供主循环查询。"""

    def __init__(self, cap_mb: float, interval_s: float = 1.0) -> None:
        super().__init__(daemon=True)
        self.cap_mb = cap_mb
        self.interval_s = interval_s
        self._pids: t.Set[int] = {os.getpid()}   # 默认把自家主进程也算进总账
        self._lock = threading.Lock()
        self.peak_mb = 0.0                       # 历史峰值（MB）
        self.tripped = False                     # 越过 cap_mb 的看门狗标志
        self._stop = threading.Event()

    def track(self, pid: int) -> None:
        """把一个 PID 纳入监控（anvil 节点、引擎子进程都注册到这里）。"""
        with self._lock:
            self._pids.add(pid)

    def sample_mb(self) -> float:
        """当前被跟踪进程的 RSS 之和（MB）。"""
        with self._lock:
            pids = list(self._pids)
        total = 0
        for pid in pids:
            kb = vmrss_kb(pid)
            if kb is not None:
                total += kb
        return total / 1024

    def run(self) -> None:
        """线程主体：每秒采样一次，顺带维护历史峰值 peak_mb。"""
        while not self._stop.is_set():
            mb = self.sample_mb()
            self.peak_mb = max(self.peak_mb, mb)
            if mb > self.cap_mb:
                self.tripped = True
                return
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        """请求监控线程停下（幂等，实验收尾时调用）。"""
        self._stop.set()

    def cpu_s(self) -> float:
        """本进程累计 CPU 秒（user+sys），作各容器成本对照用。"""
        t = os.times()
        return round(t[0] + t[1], 2)
