#!/usr/bin/env python3
"""exp032 · 并行可行性探针 —— 多组实验各起独立链、并行跑。

动机：串行跑 5 臂（exp008 口径）实测 63 min，而单跑只用了约 25% CPU
（python 452% + anvil 46%，共 20 核）。机器有余量，值得试并行。

隔离条件（读 chain.py 得到，**不需要改代码**）：
  · 端口：ports = base_port + i ⇒ 换 base_port 整段错开（启动前有 bind 探测）
  · 链 ID：chain_id_base + i ⇒ 换基址即不同链（防跨链重放）
  · 状态：anvil 只存内存、不落盘 ⇒ 无共享数据目录
  · 日志/PID：按 --out-root 下的 arm 目录分开
三个 --set 即可完全隔离：chain.base_port / chain_id_base / --out-root。

判据：
  · 各组 gates 全 True（并行不得破坏正确性）
  · 每组每臂墙钟不显著劣于串行基线 ⇒ 并行有效
  · 记录 RSS / CPU，确认没有把机器压垮

用法：
  python parallel_probe.py --n 40000 --groups 2
  python parallel_probe.py --n 40000 --split "plain@150,valve@150@1.3|tdr@150@@0.1,topup@150@@0.1,topup@150@@0.95@@20"
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXP003_RUN = HERE.parent / "exp003_tdr_on_off" / "run.py"
BASE_CFG = HERE.parent / "exp008_tdr_schedule_final" / "config.yaml"

# 串行基线（A1_fixed_baseline，2026-09-29 实测）：5 臂 63.1 min，约 12.6 min/臂
SERIAL_5ARM_MIN = 63.1
SERIAL_PER_ARM_MIN = 12.6

DEFAULT_SPLIT = ("plain@150,valve@150@1.3|"
                 "tdr@150@@0.1,topup@150@@0.1,topup@150@@0.95@20")


def launch(idx, arms, out_base, base_port, chain_id, n_ctx, drain_tail, cores=None):
    sess = out_base / f"G{idx}"
    sess.mkdir(parents=True, exist_ok=True)
    sets = [f"exp.arms={arms}", f"chain.base_port={base_port}",
            f"chain.chain_id_base={chain_id}",
            f"exp.ctx_per_broker={n_ctx // 50}", "exp.pool_scan=200000",
            "exp.rate=120", "exp.max_backlog=40000",
            f"exp.drain_tail_s={drain_tail}",
            "exp.refresh_every_blocks=10", "exp.refresh_workers=16"]
    cmd = [sys.executable, str(EXP003_RUN), "--config", str(BASE_CFG),
           "--out-root", str(sess)]
    for x in sets:
        cmd += ["--set", x]
    # CPU 绑核：把整棵进程树钉在互不重叠的核上。affinity 会随 fork/exec 继承，
    # 所以父进程绑住 = 它 spawn 的 50 引擎 + 16 anvil 全部跟着绑。
    if cores:
        cmd = ["taskset", "-c", cores] + cmd
    log = open(sess / "driver.log", "w")
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), sess, log


def collect(sess):
    """从一组的产物里取每臂 relay/搬量/gates。"""
    subs = sorted([d for d in sess.iterdir() if d.is_dir()],
                  key=lambda d: d.stat().st_mtime)
    if not subs:
        return None
    sfile = subs[-1] / "summary.json"
    if not sfile.exists():
        return None
    d = json.loads(sfile.read_text())
    out = {}
    for a in d["arms"]:
        out[a["arm"]] = {
            "relay": a["relayed"],
            "moved": a["tdr"]["moved_wei"] / 1e18,
            "gates": all(a["gates"].values()) and a["burn_reconcile"]["ok"],
            "aborted": a["coord"]["aborted"],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40000)
    ap.add_argument("--split", default=DEFAULT_SPLIT, help="用 | 分隔各组臂")
    ap.add_argument("--base-port", type=int, default=8600)
    ap.add_argument("--port-stride", type=int, default=100)
    ap.add_argument("--chain-id-base", type=int, default=43000)
    ap.add_argument("--chain-id-stride", type=int, default=1000)
    ap.add_argument("--drain-tail", type=float, default=1200.0,
                    help="并行会互相拖慢，deadline 要放宽（否则假 abort）")
    ap.add_argument("--cpu-split", default=None,
                    help="逗号分隔的核区间，如 '0-9,10-19'；给定则用 taskset 绑核隔离。"
                         "用于验证：漂移是否来自 CPU 争抢导致的 RPC 延迟")
    args = ap.parse_args()

    groups = [g.strip() for g in args.split.split("|") if g.strip()]
    cores = [c.strip() for c in args.cpu_split.split(",")] if args.cpu_split else [None] * len(groups)
    out_base = HERE / "out" / f"par_{datetime.now():%Y%m%d_%H%M%S}"
    out_base.mkdir(parents=True)
    print(f"  exp032 并行探针 · out: {out_base}")
    print(f"  N={args.n} | 组数={len(groups)} | 串行基线 5 臂 = {SERIAL_5ARM_MIN} min")
    for i, g in enumerate(groups):
        print(f"    G{i+1}: port={args.base_port + i*args.port_stride} "
              f"chainId={args.chain_id_base + i*args.chain_id_stride} "
              f"cores={cores[i] if i < len(cores) else None} arms={g}")

    t0 = time.monotonic()
    procs = []
    for i, g in enumerate(groups):
        p, sess, log = launch(i + 1, g,
                              out_base,
                              args.base_port + i * args.port_stride,
                              args.chain_id_base + i * args.chain_id_stride,
                              args.n, args.drain_tail,
                              cores[i] if i < len(cores) else None)
        procs.append((i + 1, g, p, sess, log))
    print(f"\n  ==== 全部 {len(procs)} 组已并发启动 ====", flush=True)

    results = {}
    for idx, arms, p, sess, log in procs:
        rc = p.wait()
        log.close()
        dur = time.monotonic() - t0
        res = collect(sess)
        results[idx] = {"arms": arms, "rc": rc, "wall_min": round(dur / 60, 1),
                        "res": res}
        n_arm = len([a for a in arms.split(",") if a])
        print(f"  G{idx} rc={rc} 用时 {dur/60:.1f} min（{n_arm} 臂，"
              f"{dur/60/max(n_arm,1):.1f} min/臂）", flush=True)

    total = time.monotonic() - t0
    print("\n" + "=" * 86)
    for idx, r in sorted(results.items()):
        n_arm = len([a for a in r["arms"].split(",") if a])
        print(f"  G{idx}（{n_arm} 臂）墙钟 {r['wall_min']} min")
        if not r["res"]:
            print("     !! 无 summary")
            continue
        for arm, a in sorted(r["res"].items()):
            print(f"     {arm:<24} relay {a['relay']:>7} | 搬量 {a['moved']:>10,.0f} "
                  f"| gates {'OK' if a['gates'] else 'FAIL'} | {a['aborted'] or ''}")
    print("-" * 86)
    print(f"  并行总墙钟 {total/60:.1f} min  vs  串行 5 臂 {SERIAL_5ARM_MIN} min")
    if total / 60 < SERIAL_5ARM_MIN:
        print(f"  => 并行有收益：{SERIAL_5ARM_MIN/(total/60):.2f}×")
    else:
        print(f"  => 并行无收益（更慢 {(total/60)/SERIAL_5ARM_MIN:.2f}×）")
    print("=" * 86)
    (out_base / "parallel_report.json").write_text(
        json.dumps({"n": args.n, "groups": groups, "total_min": round(total / 60, 1),
                    "serial_5arm_min": SERIAL_5ARM_MIN, "results": results},
                   indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
