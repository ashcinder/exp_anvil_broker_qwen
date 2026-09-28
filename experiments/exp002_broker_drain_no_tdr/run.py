#!/usr/bin/env python3
"""exp002 · broker 资金耗尽基线（无 TDR）—— 独立启动脚本（执行 + 绘图）。

设计/判据（对照手稿 C1–C4）见本目录 README.md；参数与数据源见 config.yaml。
运行：python run.py [--set exp.count=200 --set exp.balances=150,300]
产物：out/<ts>/{bal<档>/drain_trace.csv, summary.json, figs/drain_vs_manuscript.png}
"""
import argparse
import csv
import json
import random
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from brokerlab.brokerchain import (BURN_ADDRESS, BrokerChain, CTX,
                                   ROUTE_BROKER, ROUTE_RELAY)
from brokerlab.chain import AnvilCluster, Connections
from brokerlab.config import ETH, apply_overrides, load_config, to_params_dict
from brokerlab.identity import UserManager
from brokerlab.matching import select_broker
from brokerlab.real_data import extract
from brokerlab.tx import TxService


def exp_param(cfg, key, kind=int):
    """实验参数的唯一来源是 config.yaml 的 exp: 段——缺键即报错。
    代码里绝不保留隐藏默认值（同 exp001 的纪律）。"""
    if key not in cfg.exp:
        sys.exit(f"config.yaml 的 exp: 段缺少参数 {key!r}（本实验参数必须全部显式声明）")
    return kind(cfg.exp[key])


def quartile_shares(rows, nbins=4):
    """按执行序四分位：relay 份额 + 每笔平均跨片字节（C2/C3 判据）。"""
    n = len(rows)
    out = []
    for q in range(nbins):
        part = rows[q * n // nbins:(q + 1) * n // nbins]
        nrel = sum(1 for r in part if r["route"] == ROUTE_RELAY)
        out.append({"quartile": q + 1,
                    "relay_share": round(nrel / max(len(part), 1), 3),
                    "xmit_bytes_per_ctx": round(
                        statistics.fmean([r["xmit_bytes"] for r in part]), 0)})
    return out


def run_condition(cfg, kept, balance_eth, ctrl_bytes, seed, cond_dir: Path):
    """一个条件 = 一次全新链 + 顺序执行 kept。返回 (rows, snap, no_qualified)。

    路由 = 共享策略 static_broker（brokerlab/matching.py）：eligible 为目的分片
    可用余额 ≥ v 的 broker，种子 RNG 均匀选一个；无 eligible ⇒ relay 回退并计
    no_qualified。exp.balances 语义 = 每 broker 每分片垫资；num_brokers=1 时与
    旧单 broker 版逐位一致。两条件用同一 seed ⇒ 选择序列跨条件可比（C4 成对）。"""
    cond_dir.mkdir(parents=True, exist_ok=True)
    users = UserManager(cfg.chain.mnemonic)
    cluster = AnvilCluster(cfg.chain, cond_dir / "logs")
    cluster.start()
    cluster.write_pids(cond_dir / "pids.json")
    conns = Connections(cfg.chain)
    conns.wait_all_ready()
    tx = TxService(conns, users, cfg.chain)
    shards = list(range(cfg.chain.num_shards))
    bc = BrokerChain(tx, users, coordinator_index=cfg.scale.coordinator_index,
                     broker_base_index=cfg.scale.broker_base_index)
    num_b = cfg.scale.num_brokers
    broker_addrs = [users.address(cfg.scale.broker_base_index + b)
                    for b in range(num_b)]
    coord_addr = users.address(cfg.scale.coordinator_index)
    try:
        src_sh, dst_sh = kept[0].src_shard, kept[0].dst_shard
        assert all(k.src_shard == src_sh and k.dst_shard == dst_sh for k in kept)
        # —— setup 充值（唯一合法 setBalance 窗口）——
        total_out = sum(k.amount_wei for k in kept)
        init_wei = int(balance_eth * ETH)
        per_sender = {}
        for k in kept:
            per_sender[k.sender_idx] = per_sender.get(k.sender_idx, 0) + k.amount_wei
        actors = ([cfg.scale.coordinator_index]
                  + [cfg.scale.broker_base_index + b for b in range(num_b)]
                  + list(per_sender))
        tx.sync_nonces(actors, shards)
        for s in shards:
            for addr in broker_addrs:
                tx.set_balance_setup(addr, s, init_wei)
            tx.set_balance_setup(coord_addr, s, total_out * 2 + 10 * ETH)
        for idx, need in per_sender.items():
            tx.set_balance_setup(users.address(idx), src_sh, need + ETH)
        tx.sync_nonces(actors, shards)

        # 本地镜像各 broker 子账户：单进程顺序 ⇒ 与链上严格一致，仅供路由决策
        rng = random.Random(seed)
        bal = {b: {s: init_wei for s in shards} for b in range(num_b)}
        rows = []
        no_qualified = 0
        for i, k in enumerate(kept):
            chosen = select_broker({b: bal[b][dst_sh] for b in range(num_b)},
                                   k.amount_wei, rng)
            if chosen is None:
                no_qualified += 1
                route = ROUTE_RELAY
            else:
                route = ROUTE_BROKER
            ctx = CTX(f"drain_{i:04d}", k.sender_idx, k.receiver_idx,
                      k.src_shard, k.dst_shard, k.amount_wei,
                      origin=f"eth_mainnet_trace:{k.orig_from}->{k.orig_to}")
            res = bc.execute(ctx, route, broker_idx=max(chosen or 0, 0))
            t1, t2 = res.theta1, res.theta2
            if res.ok and chosen is not None:
                bal[chosen][src_sh] += k.amount_wei
                bal[chosen][dst_sh] -= k.amount_wei
            xmit = (t1.tx_bytes + t1.receipt_bytes) if route == ROUTE_RELAY else ctrl_bytes
            rows.append({"i": i, "ctx_id": ctx.ctx_id, "block": t1.submit_block,
                         "route": route,
                         "broker_id": chosen if chosen is not None else -1,
                         "ok": res.ok, "amount_wei": k.amount_wei,
                         "bal_dst_after_wei": sum(bal[b][dst_sh] for b in range(num_b)),
                         "bal_src_after_wei": sum(bal[b][src_sh] for b in range(num_b)),
                         "xmit_bytes": xmit,
                         "e2e_secs": round(t2.confirm_ts - t1.submit_ts, 3)
                         if t2 and t2.ok else None})
            if (i + 1) % 50 == 0:
                print(f"    {i+1}/{len(kept)} (served="
                      f"{sum(1 for r in rows if r['route']=='broker')}, relay="
                      f"{sum(1 for r in rows if r['route']=='relay')})")
        snap = {"broker_src_wei": sum(tx.get_balance(a, src_sh) for a in broker_addrs),
                "broker_dst_wei": sum(tx.get_balance(a, dst_sh) for a in broker_addrs),
                "coord_dst_wei": tx.get_balance(coord_addr, dst_sh),
                "burn_src_wei": tx.get_balance(BURN_ADDRESS, src_sh)}
        with open(cond_dir / "drain_trace.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        return rows, snap, no_qualified
    finally:
        cluster.stop()


def make_figure(out_dir, kept, results, balances, rows_by_tag):
    """与手稿 fig1 同构的双面板草图。"""
    from brokerlab import plotting as P
    import matplotlib.pyplot as plt

    figs = out_dir / "figs"
    figs.mkdir(exist_ok=True)
    fig, (ax, axb) = plt.subplots(1, 2, figsize=(11.5, 4.2), dpi=140)
    rows = rows_by_tag[f"bal{int(balances[0])}"]
    blocks = [r["block"] for r in rows]
    bwidth = max(1, (max(blocks) - min(blocks)) // 10)
    bins = sorted(set((b - min(blocks)) // bwidth for b in blocks))
    xs = [min(blocks) + bi * bwidth for bi in bins]
    serv = [sum(1 for r in rows if (r["block"]-min(blocks))//bwidth == bi and r["route"] == "broker") for bi in bins]
    relv = [sum(1 for r in rows if (r["block"]-min(blocks))//bwidth == bi and r["route"] == "relay") for bi in bins]
    ax.bar(xs, serv, width=bwidth * .9, color=P.COLOR_BROKER,
           label="Broker Transactions")
    ax.bar(xs, relv, width=bwidth * .9, bottom=serv, color=P.COLOR_RELAY,
           label="Relay Fallbacks")
    ax2 = ax.twinx()
    xcum, acc = [], 0.0
    for r in rows:
        acc += r["xmit_bytes"] / 1024
        xcum.append(acc)
    ax2.plot(blocks, xcum, color=P.COLOR_DATA, lw=1.6,
             label="Cumulative Cross-Shard Data")
    ax2.set_ylabel("Cumulative Cross-Shard Data (KiB)", color=P.COLOR_DATA)
    ax.set_xlabel("Block Number")
    ax.set_ylabel(f"Transactions in Each {bwidth}-Block Interval")
    ax.set_title("(a) Relay Use Increases as Broker Liquidity Falls")
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper left")

    for tag, color in [(f"bal{int(balances[0])}", P.COLOR_DATA),
                       (f"bal{int(balances[1])}", P.COLOR_ALT)]:
        rs = rows_by_tag[tag]
        axb.plot([r["block"] for r in rs], [r["bal_dst_after_wei"] / ETH for r in rs],
                 color=color, lw=1.4, label=f"Initial Balance: {tag[3:]} ETH")
    axb.axhline(0.1 * balances[0], ls=":", c=P.COLOR_GRAY, lw=1,
                label=f"10% of {balances[0]:.0f} ETH")
    axb.set_xlabel("Block Number")
    axb.set_ylabel("Broker Balance on Destination Shard (ETH)")
    axb.set_title("(b) More Initial Liquidity Delays Depletion")
    axb.legend(fontsize=8)
    P.save(fig, figs / "drain_vs_manuscript.png")
    print(f"  figure: {figs / 'drain_vs_manuscript.png'}")


def main() -> int:
    """构造定向流→两档垫资各起一条全新链顺序执行→逐条判 C1-C4→出图出表。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    args = ap.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)

    count = exp_param(cfg, "count", int)
    ctrl = exp_param(cfg, "match_ctrl_bytes", int)
    scan = exp_param(cfg, "pool_scan", int)
    seed = exp_param(cfg, "seed", int)
    raw_bal = exp_param(cfg, "balances", str)
    balances = [float(x) for x in raw_bal.split(",")]
    num_b = cfg.scale.num_brokers

    # —— 真实数据 → 多数方向定向流 ——
    pool = extract(cfg.traffic.real_csv_path,
                   num_shards=cfg.chain.num_shards, num_users=cfg.scale.num_users,
                   user_base_index=cfg.scale.user_base_index,
                   value_floor_wei=int(cfg.traffic.value_floor_eth * ETH),
                   value_cap_wei=int(cfg.traffic.value_cap_eth * ETH),
                   limit=scan)
    dirs = {}
    for r in pool:
        dirs[(r.src_shard, r.dst_shard)] = dirs.get((r.src_shard, r.dst_shard), 0) + 1
    (maj_src, maj_dst), maj_n = max(dirs.items(), key=lambda kv: kv[1])
    kept = [r for r in pool if (r.src_shard, r.dst_shard) == (maj_src, maj_dst)][:count]
    drained = sum(k.amount_wei for k in kept) / ETH
    print(f"  trace rows scanned: {len(pool)} | split: {dirs}")
    print(f"  多数方向 S{maj_src}->S{maj_dst}：取 {len(kept)} 笔（占 {maj_n/len(pool)*100:.0f}%），"
          f"合计 {drained:.1f} ETH")
    for b in balances:
        if b >= drained:
            print(f"  警告: 条件 {b:.0f} ETH ≥ 流合计 {drained:.0f} ETH ⇒ 可能不耗尽（调大 exp.count）")

    out_root = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    results, rows_by_tag = {}, {}
    for b in balances:
        tag = f"bal{int(b)}"
        print(f"\n  ==== 条件: broker 初始 {b:.0f} ETH/分片 × {num_b} broker(s) ====")
        rows, snap, noq = run_condition(cfg, kept, b, ctrl, seed, out_root / tag)
        rows_by_tag[tag] = rows
        served = [r for r in rows if r["route"] == ROUTE_BROKER]
        rel = [r for r in rows if r["route"] == ROUTE_RELAY]
        first_relay = rel[0] if rel else None
        dst_pool_wei = num_b * int(b * ETH)          # 全体 broker 目的分片资金池
        thr10 = next((r for r in rows
                      if r["bal_dst_after_wei"] < 0.1 * dst_pool_wei), None)
        results[tag] = {
            "balance_eth": b, "num_brokers": num_b, "n": len(rows),
            "no_qualified": noq,
            "failed": [r["i"] for r in rows if not r["ok"]],
            "served": len(served), "relayed": len(rel),
            "eta_end": round(len(served) / len(rows), 3),
            "first_relay": None if first_relay is None else
                          {"i": first_relay["i"], "block": first_relay["block"]},
            "block_below_10pct": None if thr10 is None else thr10["block"],
            "bal_dst_final_wei": snap["broker_dst_wei"],
            "bal_src_final_wei": snap["broker_src_wei"],
            "bal_init_wei": dst_pool_wei,
            "broker_total_conserved_wei": (snap["broker_dst_wei"] + snap["broker_src_wei"])
                                          - 2 * dst_pool_wei,
            "burn_wei": snap["burn_src_wei"],
            "relayed_total_wei": sum(r["amount_wei"] for r in rel),
            "quartiles": quartile_shares(rows),
        }

    a, bb = results[f"bal{int(balances[0])}"], results[f"bal{int(balances[1])}"]
    q = a["quartiles"]
    xm = [x["xmit_bytes_per_ctx"] for x in q]
    c1 = (a["bal_dst_final_wei"] < 0.1 * a["bal_init_wei"]
          and abs(a["broker_total_conserved_wei"]) < ETH)
    c2 = q[3]["relay_share"] > q[0]["relay_share"] and a["relayed"] > 0
    c3 = xm[-1] > xm[0] * 1.5
    fb1, fb2 = (a["first_relay"] or {}).get("block"), (bb["first_relay"] or {}).get("block")
    delay = (fb2 / fb1) if (fb1 and fb2 and fb1 > 0) else None
    ratio_b = balances[1] / balances[0]
    c4 = delay is not None and delay > 1.5 and (bb["bal_dst_final_wei"] < 0.1 * bb["bal_init_wei"])
    burn_ok = all(r["burn_wei"] == r["relayed_total_wei"] for r in results.values())
    no_fail = all(not r["failed"] for r in results.values())

    print("\n" + "=" * 76)
    print("  与手稿结论逐条对照（无 TDR 基线，真实数据多数方向流）")
    print("=" * 76)
    print(f"  C1 空间失衡: dst 终值 {a['bal_dst_final_wei']/ETH:.4f} ETH（初值 {balances[0]:.0f}），"
          f"src 终值 {a['bal_src_final_wei']/ETH:.1f}，总额漂移 {a['broker_total_conserved_wei']/ETH:+.6f}"
          f"  →  {'一致' if c1 else '不一致/未耗尽'}")
    print(f"  C2 relay 占比递增: {q[0]['relay_share']:.0%} → {q[1]['relay_share']:.0%} → "
          f"{q[2]['relay_share']:.0%} → {q[3]['relay_share']:.0%}"
          f"（首次回退 idx={a['first_relay']['i'] if a['first_relay'] else 'never'}）  →  {'一致' if c2 else '不一致'}")
    print(f"  C3 跨片数据量上升: 每笔 {xm[0]:.0f} → {xm[-1]:.0f} B（末/首 {xm[-1]/max(xm[0],1):.1f}×）  →  {'一致' if c3 else '不一致'}")
    msg = (f"首次回退块 {fb1} → {fb2}（推迟 ×{delay:.2f}，余额比仅 ×{ratio_b:.1f}）"
           if delay is not None else "数据不足（两档均未耗尽？）")
    print(f"  C4 加充值只推迟不消除: {msg}；{balances[1]:.0f} 档终值 dst "
          f"{bb['bal_dst_final_wei']/ETH:.3f} ETH  →  {'一致' if c4 else '不一致/未耗尽（调大 exp.count）'}")
    print(f"  对账: burn≡relayed {'OK' if burn_ok else 'BROKEN'}；失败 CTX {sum(len(r['failed']) for r in results.values())}")
    print("=" * 76)

    try:
        make_figure(out_root, kept, results, balances, rows_by_tag)
    except ImportError:
        print("  (matplotlib 不可用，跳过绘图)")

    (out_root / "summary.json").write_text(json.dumps({
        "params": to_params_dict(cfg, extra={"count": count, "balances_eth": balances,
                                             "direction": f"S{maj_src}->S{maj_dst}",
                                             "stream_total_eth": round(drained, 2),
                                             "match_ctrl_bytes": ctrl}),
        "conditions": results,
        "manuscript_check": {"C1_depletion": c1, "C2_relay_grows": c2,
                             "C3_data_rises": c3, "C4_deposit_delays_only": c4},
        "reconciliation": {"burn_eq_relayed": burn_ok, "no_failed_ctx": no_fail},
    }, indent=2))
    print(f"  artifacts: {out_root}/")
    return 0 if (c1 and c2 and c3 and c4 and burn_ok and no_fail) else 1


if __name__ == "__main__":
    sys.exit(main())
