"""anvil 集群的启停管理 + Web3 连接池（Linux 优先）。

本模块承自旧三代 core/network.py，并按审计 F8 的要求做了移植修正：
- 禁止全局 `pkill -f anvil` / taskkill：平台只终止自家启动的子进程，
  PID 记录在运行目录的 pids.json 里；
- 禁止硬编码盘符路径：节点日志写进运行目录，链状态只存内存
  （anvil 默认行为，什么都不落盘）；
- anvil 可执行文件的查找顺序：配置值 → ~/.foundry/bin → PATH；
- 启动任何一个节点前，先逐端口做 bind 探测（占用即快速失败，并报出端口号）。
"""
import json
import os
import shutil
import socket
import subprocess
import time
import typing as t
from pathlib import Path

from web3 import Web3
from web3.providers import HTTPProvider

from .config import ChainCfg


def resolve_anvil_bin(configured: t.Optional[str] = None) -> str:
    """返回一个可执行的 anvil 路径；全部候选都找不到时，报错并列出所有试过的位置。"""
    home = os.path.expanduser("~")
    candidates: t.List[str] = []
    if configured:
        candidates.append(configured)
    candidates += [
        os.path.join(home, ".foundry", "bin", "anvil"),
        os.path.join(home, ".cargo", "bin", "anvil"),
        "anvil",
    ]
    for cand in candidates:
        found = shutil.which(cand)
        if found:
            return found
    raise FileNotFoundError(f"anvil not found; tried: {candidates}")


def _port_free(port: int) -> bool:
    # bind-test 只是"启动前哨兵"：测完到 spawn 之间存在理论竞态（TOCTOU），
    # 可接受——同机的真撞车概率极低，而失败会立刻表现为 shard 启动报错+日志。
    #
    # SO_REUSEADDR 必须与 anvil 真实 bind 语义一致，否则会误报占用：
    # 上一轮实验退出后，端口上的残留连接会留 ~60s TIME_WAIT；不设该选项的
    # bind() 遇到 TIME_WAIT 直接 EADDRINUSE（我们 2026-09-03 就踩了这个假阳性），
    # 而 anvil 自己设了 SO_REUSEADDR、实际能正常 bind。活跃监听者仍然会正确
    # 报占用——该选项不放过真冲突。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class AnvilCluster:
    """每个分片启一个 anvil 节点，负责其启停。平台只拥有自己 spawn 出来的子进程。"""

    def __init__(self, cfg: ChainCfg, log_dir: Path) -> None:
        self._cfg = cfg
        self._log_dir = Path(log_dir)
        self._log_files: t.Dict[int, t.IO] = {}
        self._procs: t.Dict[int, subprocess.Popen] = {}

    # ------------------------------------------------------------------
    def start(self) -> None:
        cfg = self._cfg
        anvil = resolve_anvil_bin(cfg.anvil_bin)
        ports = [cfg.base_port + i for i in range(cfg.num_shards)]
        busy = [p for p in ports if not _port_free(p)]
        if busy:
            raise RuntimeError(
                f"ports already in use: {busy} — pick another chain.base_port "
                "or stop the conflicting processes (this tool never kills "
                "foreign processes)")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        for i, port in enumerate(ports):
            log_path = self._log_dir / f"shard_{i}.log"
            fh = open(log_path, "w")
            # 节点参数语义：
            #   --block-base-fee-per-gas=0 + 交易 gasPrice=0 → 转账完全免费，
            #     M1 的余额差分校验因此可以做到 wei 级精确（gas 模型 M-later 再接入）；
            #   --hardfork=shanghai：允许 legacy 签名交易（我们全部用 legacy）；
            #   --order=fifo：出块按提交序，减少入块随机性；
            #   --prune-history：长 run 内存有界；链状态不落盘，每次 demo 全新开始；
            #   --mnemonic=<共享> + 每分片独立 chainId：所有分片地址空间"同名不同账"
            #     ——一个 broker 地址在各分片各有一份余额/nonce，即 BrokerChain 的
            #     子账户模型；独立 chainId 防止一笔已签交易被重放到别的分片。
            cmd = [
                anvil,
                f"--port={port}",
                "--host=127.0.0.1",
                f"--chain-id={cfg.chain_id_base + i}",
                f"--block-time={cfg.block_time_s}",
                f"--mnemonic={cfg.mnemonic}",
                f"--accounts={cfg.num_funded_accounts}",
                f"--balance={cfg.accounts_balance_eth}",
                f"--gas-limit={cfg.gas_limit}",
                "--block-base-fee-per-gas=0",
                "--hardfork=shanghai",
                "--order=fifo",
                "--prune-history",
            ]
            proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT)
            self._log_files[i] = fh
            self._procs[i] = proc
        # 等 1 秒让节点稳定，同时检测启动即崩溃的节点（崩溃者带日志路径报错）
        time.sleep(1.0)
        for i, proc in self._procs.items():
            if proc.poll() is not None:
                self.stop()
                raise RuntimeError(
                    f"anvil shard_{i} exited early (rc={proc.returncode}); "
                    f"see {self._log_dir / f'shard_{i}.log'}")

    def write_pids(self, path: Path) -> None:
        """把 {分片号: PID} 写进运行目录，供关停与人工排查使用。"""
        Path(path).write_text(json.dumps(
            {f"shard_{i}": p.pid for i, p in self._procs.items()}, indent=2))

    def stop(self, grace_s: float = 5.0) -> None:
        """先 terminate 全部自家子进程；超过宽限期还活着才 kill；最后关闭日志句柄。"""
        for proc in self._procs.values():
            if proc.poll() is None:
                proc.terminate()
        deadline = time.time() + grace_s
        for proc in self._procs.values():
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            if proc.poll() is None:      # terminate 超时才升级 kill；只处置自家子进程
                proc.kill()
        for fh in self._log_files.values():
            try:
                fh.close()
            except OSError:
                pass
        self._procs.clear()
        self._log_files.clear()


class Connections:
    """每个分片持有一个 Web3 句柄；首次使用时才创建，之后缓存复用。"""

    def __init__(self, cfg: ChainCfg) -> None:
        self._urls = {
            i: f"http://127.0.0.1:{cfg.base_port + i}" for i in range(cfg.num_shards)
        }
        self._web3: t.Dict[int, Web3] = {}

    def web3(self, shard: int) -> Web3:
        w3 = self._web3.get(shard)
        if w3 is None:
            w3 = Web3(HTTPProvider(self._urls[shard], request_kwargs={"timeout": 60}))
            self._web3[shard] = w3
        return w3

    def wait_all_ready(self, timeout_s: float = 90.0) -> None:
        """逐个分片轮询 RPC，直到能读到块高为止；整体超时抛 TimeoutError。"""
        start = time.time()
        for shard in self._urls:
            while True:
                if time.time() - start > timeout_s:
                    raise TimeoutError(f"shard_{shard} not reachable within {timeout_s}s")
                try:
                    if self.web3(shard).is_connected():
                        _ = self.web3(shard).eth.block_number
                        break
                except Exception:
                    time.sleep(0.5)
