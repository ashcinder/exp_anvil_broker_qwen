#!/usr/bin/env python3
"""driver4：修 driver3 两个坑的续跑驱动（串行、端口预检、可续跑）。
  ① 6a 计数串桶（共享根导致 eps0.1 的旧场冒充所有 eps）→ 每 eps 独立目录 e{eps}/
  ② 6c 混合舰队在 40000 笔+tail900 下收口超时 → 全场统一 20000 笔 + tail1500，
     费用名义价 2e11（全服务 ΣβF=8.4 ETH，适配断轴画幅）。
目标：6a eps{.2,.3,.4,.5} 各 2 场（.05 已有 2、.1 已有 5）；6c p{10..100} 各 2 场。"""
import json, socket, subprocess, sys, time
from datetime import datetime
from pathlib import Path

QWEN = Path("/workspace/research_broker_rebalance/exp_anvil_broker_qwen/exp_figure_origin_qwen")
RUN = Path("/workspace/research_broker_rebalance/exp_anvil_broker_qwen/experiments/exp003_tdr_on_off/run.py")
LOG = QWEN / "sweep_fig6b" / "driver4.log"
BASE_PORT, N_SHARDS = 8600, 16


def log(msg):
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def wait_ports_free(timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        busy = []
        for p in range(BASE_PORT, BASE_PORT + N_SHARDS):
            sk = socket.socket()
            try:
                sk.bind(("127.0.0.1", p))
            except OSError:
                busy.append(p)
            finally:
                sk.close()
        if not busy:
            return True
        log(f"  端口占用 {busy[:3]}…等待")
        time.sleep(15)
    return False


def count_valid(root: Path, arm_name: str, min_ctx: int):
    n = 0
    for sd in sorted(root.glob("2026*")) if root.exists() else []:
        ctx = sd / arm_name / "ctx_rows.csv"
        if ctx.exists() and sum(1 for _ in ctx.open("rb")) - 1 >= min_ctx:
            n += 1
    return n


def session(root: Path, arms: str, extra):
    cmd = [sys.executable, str(RUN), "--out-root", str(root),
           "--set", f"exp.arms={arms}",
           "--set", "chain.num_shards=16",
           "--set", "exp.pool_scan=200000", "--set", "exp.rate=120",
           "--set", "exp.max_backlog=45000",
           *[x for kv in extra for x in ("--set", kv)]]
    if not wait_ports_free():
        return False
    with LOG.open("a") as f:
        rc = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT)
    time.sleep(25)
    return rc == 0


def ensure(target, root, arm_name, arms, extra, label, min_ctx):
    for _ in range(3):
        have = count_valid(root, arm_name, min_ctx)
        if have >= target:
            log(f"{label}: {have}/{target} 达标")
            return True
        log(f"{label}: 补 {target-have} 场（现 {have}）")
        for _k in range(target - have):
            if not session(root, arms, extra):
                log(f"{label}: 本场失败/端口未就绪")
        if count_valid(root, arm_name, min_ctx) >= target:
            return True
    log(f"!! {label}: 仍未达标 {count_valid(root, arm_name, min_ctx)}/{target}")
    return False


def main():
    log("driver4 启动")
    # 6a：缺的四个 ε 各自独立目录、各 2 场
    for i, eps in enumerate(("0.2", "0.3", "0.4", "0.5")):
        ensure(2, QWEN / f"sweep_fig6a/e{eps}", f"tdr@{eps}", f"tdr@@@{eps}",
               ["exp.ctx_per_broker=800", "exp.drain_tail_s=900",
                f"exp.route_seed={810+i}"], f"6a eps{eps}", 20000)
    # 6c：frac 10%..100%，统一 20000 笔 + 费用 2e11
    for j, p10 in enumerate(range(1, 11)):
        frac = f"0.{p10}" if p10 < 10 else "1.0"
        for k in (0, 1):
            ensure(2, QWEN / f"frac_fig6c/p{p10*10}", f"tdr@p{p10*10}",
                   f"tdr@@@@@@@{frac}",
                   ["exp.ctx_per_broker=400", "exp.drain_tail_s=1500",
                    "b2e.enabled=true", "b2e.ref_gas_price_wei=200000000000",
                    f"exp.route_seed={850+p10*10+k}"],
                   f"6c p{p10*10}", 20000)
    done = all(count_valid(QWEN / f"sweep_fig6a/e{e}", f"tdr@{e}", 20000) >= 2
               for e in ("0.2", "0.3", "0.4", "0.5")) and \
        all(count_valid(QWEN / f"frac_fig6c/p{p}", f"tdr@p{p}", 20000) >= 2
            for p in range(10, 101, 10))
    if done:
        (QWEN / "sweep_fig6b" / "DRIVER4_DONE").write_text(str(datetime.now()))
        log("driver4 全部达标")
    else:
        log("driver4 结束（存在未达标项，见上）")


if __name__ == "__main__":
    main()
