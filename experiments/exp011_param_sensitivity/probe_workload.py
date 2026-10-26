#!/usr/bin/env python3
"""exp011 · 工作负载体检（不跑链，纯 Python）。

目的：回答两个问题，全部用数字，不靠推测。
  ① 闸门：`group_cap_mult` 到底丢掉了什么？（哪些发送者、多少笔）
  ② 平稳性：真正喂给实验的那条流（sel），按 arrival_pos 切开后，
     目的分片分布 λ 是平的还是有结构的？

背景：exp003/run.py 的 build_workload 先按 (sender_idx, src_shard) 分组，
再按【组大小升序】拼接。所以 arrival_pos 不等于时间轴——这是体检要暴露的第一件事。

用法：
  python probe_workload.py --pool-scan 200000 --n 40000
  python probe_workload.py --pool-scan 200000 --n 40000 --cap-mult 20
"""
import argparse
import collections
import importlib.util
import sys
import types
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))


def _load_exp003():
    """把 exp003/run.py 当模块载入，直接复用它的 apply_regime_shift。

    刻意不复制一份实现：离线探针和线上管线必须跑同一段代码，
    否则"离线看着对、上链才发现不对"是迟早的事。"""
    p = HERE.parent / "exp003_tdr_on_off" / "run.py"
    spec = importlib.util.spec_from_file_location("exp003_run", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

from brokerlab.broker_engine import group_key          # noqa: E402
from brokerlab.config import ETH                        # noqa: E402
from brokerlab.real_data import extract                 # noqa: E402

BUCKETS = 20


def build(pool_scan, n, cap_mult, num_shards, num_users, user_base,
          trace, floor_eth, cap_eth):
    """逐行复刻 exp003 build_workload，额外把中间量吐出来。"""
    pool = extract(trace, num_shards=num_shards, num_users=num_users,
                   user_base_index=user_base,
                   value_floor_wei=int(floor_eth * ETH),
                   value_cap_wei=int(cap_eth * ETH), limit=pool_scan)
    groups = collections.defaultdict(list)
    for r in pool:
        groups[group_key(asdict(r))].append(asdict(r))
    glist = list(groups.values())
    mean_g = len(pool) / max(len(glist), 1)
    cap = mean_g * cap_mult
    kept = sorted((g for g in glist if len(g) <= cap), key=len)
    dropped = [g for g in glist if len(g) > cap]
    sel = []
    for g in kept:
        sel.extend(g)
        if len(sel) >= n:
            break
    sel = sel[:n]
    return pool, glist, dropped, kept, sel, mean_g, cap


def report(pool, glist, dropped, sel, mean_g, cap, n):
    print(f"\n{'='*78}\n① 闸门：group_cap_mult 丢掉了什么\n{'='*78}")
    print(f"  扫描到 CTX        {len(pool)}")
    print(f"  组数              {len(glist)}")
    print(f"  组大小 均值 {mean_g:.1f} | cap = {cap:.1f} | 中位 "
          f"{sorted(len(g) for g in glist)[len(glist)//2]}")
    big = sorted(dropped, key=len, reverse=True)
    print(f"  被丢组数 {len(dropped)} | 被丢 CTX "
          f"{sum(len(g) for g in dropped)} "
          f"({100*sum(len(g) for g in dropped)/len(pool):.1f}%)")
    if big:
        print(f"  最大的 5 个被丢组：")
        for g in big[:5]:
            print(f"    {len(g):>7} 笔 | 发送者 idx={g[0]['sender_idx']} "
                  f"@分片{g[0]['src_shard']} | 源地址 {g[0]['orig_from'][:12]}…")
    print(f"\n  选择后 N           {len(sel)}"
          f"  (说明：kept 按大小升序，sel 是【前 n 笔】)")
    print(f"  → 进入实验的组数   {len({group_key(c) for c in sel})}")
    print(f"  → 若 cap 放开，单组最大 {max(len(g) for g in glist)} 笔"
          f"（N={n}，一组就可能压过整个负载）")


def lambda_flat(sel, n, label, num_shards):
    """按 arrival_pos 切桶，看目的/源分片分布是否随流位置变化。"""
    print(f"\n{'='*78}\n② 平稳性：{label}\n{'='*78}")
    print(f"{'桶':>3} {'位置区间':>14} {'n':>7} {'top1dst':>8} {'份额':>6} "
          f"{'HHI':>6} {'top1src':>8} {'份额':>6} {'dst=8':>6}")
    dstb = collections.defaultdict(collections.Counter)
    srcb = collections.defaultdict(collections.Counter)
    for i, c in enumerate(sel):
        b = i * BUCKETS // max(len(sel), 1)
        dstb[b][c["dst_shard"]] += 1
        srcb[b][c["src_shard"]] += 1
    tops = []
    for b in range(BUCKETS):
        if not dstb[b]:
            continue
        tot = sum(dstb[b].values())
        d, dc = dstb[b].most_common(1)[0]
        s, sc = srcb[b].most_common(1)[0]
        hhi = sum((x / tot) ** 2 for x in dstb[b].values())
        tops.append(d)
        lo, hi = b * len(sel) // BUCKETS, (b + 1) * len(sel) // BUCKETS
        print(f"{b:>3} {f'{lo}-{hi}':>14} {tot:>7} {d:>8} {dc/tot:>6.1%} "
              f"{hhi:>6.3f} {s:>8} {sc/tot:>6.1%} {dstb[b][8]/tot:>6.1%}")
    print(f"  top1 目的分片切换 {sum(1 for i in range(1,len(tops)) if tops[i]!=tops[i-1])}"
          f" / {len(tops)-1} 次相邻比较")
    allc = collections.Counter(c["dst_shard"] for c in sel)
    print(f"  全流目的分布 λ: " + " ".join(
        f"{k}:{100*allc[k]/len(sel):.1f}%" for k in sorted(allc)))
    print(f"  全流目的 HHI = {sum((v/len(sel))**2 for v in allc.values()):.3f}"
          f"（16 分片均匀 = {1/num_shards:.3f}）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", default=str(ROOT / "trace" / "ETH_cleaned.csv"))
    ap.add_argument("--pool-scan", type=int, default=200000)
    ap.add_argument("--n", type=int, default=40000)
    ap.add_argument("--cap-mult", type=float, default=2.0)
    ap.add_argument("--num-shards", type=int, default=16)
    ap.add_argument("--num-users", type=int, default=50)
    ap.add_argument("--user-base", type=int, default=200)
    ap.add_argument("--floor-eth", type=float, default=0.01)
    ap.add_argument("--cap-eth", type=float, default=10.0)
    ap.add_argument("--shift-to", type=int, default=-1,
                    help="注入层：目标目的分片（-1=关闭）")
    ap.add_argument("--shift-start", type=float, default=0.5)
    ap.add_argument("--shift-span", type=float, default=1.0)
    ap.add_argument("--shift-period", type=float, default=0.0,
                    help=">0 = 周期切换的半周期（占全流比例），罚 hl 用")
    ap.add_argument("--shift-intensity", type=float, default=1.0)
    a = ap.parse_args()

    args = (a.num_shards, a.num_users, a.user_base, a.trace,
            a.floor_eth, a.cap_eth)
    pool, glist, dropped, kept, sel, mean_g, cap = build(
        a.pool_scan, a.n, a.cap_mult, *args)
    report(pool, glist, dropped, sel, mean_g, cap, a.n)
    lambda_flat(sel, a.n, f"cap_mult={a.cap_mult}（现口径）", a.num_shards)

    if a.shift_to >= 0:
        e3 = _load_exp003()
        cfg = types.SimpleNamespace(
            exp={"shift_to_shard": a.shift_to,
                 "shift_start_frac": a.shift_start,
                 "shift_span_frac": a.shift_span,
                 "shift_period_frac": a.shift_period,
                 "shift_intensity": a.shift_intensity},
            chain=types.SimpleNamespace(num_shards=a.num_shards))
        print(f"\n\n{'#'*78}\n# 注入层：apply_regime_shift（exp003/run.py 里那段真代码）\n{'#'*78}")
        sel_s = e3.apply_regime_shift([dict(c) for c in sel], cfg)
        lambda_flat(sel_s, a.n,
                    f"shift_to={a.shift_to} start={a.shift_start} "
                    f"span={a.shift_span} period={a.shift_period} "
                    f"intensity={a.shift_intensity}",
                    a.num_shards)

    if a.cap_mult < 1e9:
        print(f"\n\n{'#'*78}\n# 对照：把 cap 放开（cap_mult=1e9），负载会变成什么样\n{'#'*78}")
        _, g2, d2, _, sel2, m2, c2 = build(a.pool_scan, a.n, 1e9, *args)
        report(pool, g2, d2, sel2, m2, c2, a.n)
        lambda_flat(sel2, a.n, "cap_mult=∞（全放行）", a.num_shards)


if __name__ == "__main__":
    main()
