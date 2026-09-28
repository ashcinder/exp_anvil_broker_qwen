#!/usr/bin/env python3
"""阶段驱动 v3：可续跑、端口预检、失败重试。串行执行，绝不并发。

任务清单（valid = summary.json + 臂 ctx_rows≥20000 + num_shards=16 + rate≥100）：
  6b 窗口扫描  window 5..50(步5)   目标 2 场/窗口   根目录 sweep_fig6b/w{W}
  6a ε 扫描    eps .05/.1/.2/.3/.4/.5  目标 2 场/eps  根目录 sweep_fig6a
  5c 带费对照  plain+topup@0.95@20，15000 笔  目标 2 场  fees_fig5c
  6c 装备比例  tdr frac .1..1.0    目标 2 场/frac   frac_fig6c
全部完成后写 sweep_fig6b/DRIVER3_DONE。
"""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

QWEN = Path("/workspace/research_broker_rebalance/exp_anvil_broker_qwen/exp_figure_origin_qwen")
RUN = Path("/workspace/research_broker_rebalance/exp_anvil_broker_qwen/experiments/exp003_tdr_on_off/run.py")
LOG = QWEN / "sweep_fig6b" / "driver3.log"
N_SHARDS = 16
BASE_PORT = 8600
MAX_ATTEMPTS = 3


def log(msg):
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def wait_ports_free(timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        busy = []
        for p in range(BASE_PORT, BASE_PORT + N_SHARDS):
            s = socket.socket()
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                busy.append(p)
            finally:
                s.close()
        if not busy:
            return True
        log(f"  端口占用 {busy[:3]}…等待")
        time.sleep(10)
    return False


def valid_dirs(root: Path, min_ctx=20000):
    """root 下 session 目录（直接含臂）计数有效场。"""
    n = 0
    for sd in sorted(root.glob("2026*")):
        summ = sd / "summary.json"
        if not summ.exists():
            continue
        try:
            p = json.loads(summ.read_text())["params"]
        except Exception:
            continue
        if int(p["chain"]["num_shards"]) != 16 or float(p["exp"]["rate"]) < 100:
            continue
        for a in sd.iterdir():
            ctx = a / "ctx_rows.csv"
            if a.is_dir() and ctx.exists() \
                    and sum(1 for _ in ctx.open("rb")) - 1 >= min_ctx:
                n += 1
                break
    return n


def run_session(out_root: Path, arms: str, extra):
    out_root.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(RUN), "--out-root", str(out_root),
           "--set", f"exp.arms={arms}",
           "--set", "chain.num_shards=16",
           "--set", "exp.pool_scan=200000", "--set", "exp.rate=120",
           "--set", "exp.ctx_per_broker=800",
           "--set", "exp.max_backlog=45000", "--set", "exp.drain_tail_s=900",
           *[x for kv in extra for x in ("--set", kv)]]
    if not wait_ports_free():
        return False
    with LOG.open("a") as f:
        rc = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT)
    time.sleep(20)          # TIME_WAIT 缓冲
    return rc == 0


def ensure(target: int, root: Path, arms: str, extra, label, min_ctx=20000):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        have = valid_dirs(root, min_ctx)
        if have >= target:
            log(f"{label}: 已有 {have}/{target}，跳过")
            return True
        log(f"{label}: 第 {attempt} 轮，补 {target-have} 场")
        need = target - have
        for k in range(need):
            ok = run_session(root, arms, extra)
            if not ok:
                log(f"{label}: session 失败（rc≠0 或端口未就绪）")
        if valid_dirs(root, min_ctx) >= target:
            return True
    log(f"!! {label}: 三轮后仍未达标 ({valid_dirs(root, min_ctx)}/{target})")
    return False


def main():
    log("driver3 启动")
    for w in (5, 10, 15, 20, 25, 30, 35, 40, 45, 50):
        ensure(2, QWEN / f"sweep_fig6b/w{w}", "tdr@@@0.2",
               [f"exp.tdr_window_blocks={w}"], f"6b w{w}")
    for eps in ("0.05", "0.1", "0.2", "0.3", "0.4", "0.5"):
        ensure(2, QWEN / "sweep_fig6a", f"tdr@@@{eps}",
               [f"exp.route_seed={800 + int(float(eps) * 100)}"], f"6a eps{eps}")
    ensure(2, QWEN / "fees_fig5c", "plain,topup@@@0.95@@20",
           ["exp.ctx_per_broker=300", "b2e.enabled=true",
            "b2e.ref_gas_price_wei=400000000000", "exp.route_seed=512"], "5c 带费off/on", min_ctx=14000)
    for f100 in range(1, 11):
        frac = f"0.{f100}" if f100 < 10 else "1.0"
        ensure(2, QWEN / f"frac_fig6c/p{f100}", f"tdr@@@@@@@{frac}",
               ["b2e.enabled=true", "b2e.ref_gas_price_wei=100000000000",
                f"exp.route_seed={600+f100}"], f"6c frac{frac}")
    ok6b = all(valid_dirs(QWEN / f"sweep_fig6b/w{w}") >= 2
               for w in (5, 10, 15, 20, 25, 30, 35, 40, 45, 50))
    ok6a = valid_dirs(QWEN / "sweep_fig6a") >= 12
    ok5c = valid_dirs(QWEN / "fees_fig5c") >= 2
    ok6c = all(valid_dirs(QWEN / f"frac_fig6c/p{p}") >= 2 for p in range(1, 11))
    if ok6b and ok6a and ok5c and ok6c:
        (QWEN / "sweep_fig6b" / "DRIVER3_DONE").write_text(str(datetime.now()))
    log("driver3 结束")


if __name__ == "__main__":
    main()
