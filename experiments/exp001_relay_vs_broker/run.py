#!/usr/bin/env python3
"""exp001 · relay vs broker 机制对比 —— 独立启动脚本（执行 + 绘图）。

设计/假设/判据见本目录 README.md；参数与数据源见 config.yaml。
运行：python run.py [--set exp.pairs=20]（覆盖任意配置项）
产物：out/<ts>/{ctx_rows.csv, summary.json, figs/exp001_overview.png}
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
ROOT = HERE.parents[1]                      # 项目根（experiments/expNNN/ 的上两级）
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
    代码里绝不保留隐藏默认值：默认值藏进代码 = 复现者从配置读不出真实参数。"""
    if key not in cfg.exp:
        sys.exit(f"config.yaml 的 exp: 段缺少参数 {key!r}（本实验参数必须全部显式声明）")
    return kind(cfg.exp[key])


def pct(vals, q):
    """分位数：把序列排序后按 q（0-100）取位。"""
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(q / 100 * (len(s) - 1))))]


def dist(vals):
    """一组数字的分布摘要：均值/中位/95 分位/极值/样本数。"""
    return {"mean": round(statistics.fmean(vals), 4), "p50": pct(vals, 50),
            "p95": pct(vals, 95), "min": min(vals), "max": max(vals), "n": len(vals)}


def run(cfg, args_pairs, ctrl_bytes, seed, out_dir: Path):
    """主执行体：同一行 trace 双路径各跑一次（逐对交替先后），
    采集延迟/通信/足迹三类实测度量，判 H1-H3，返回判据是否全过。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    users = UserManager(cfg.chain.mnemonic)
    cluster = AnvilCluster(cfg.chain, out_dir / "logs")
    cluster.start()
    cluster.write_pids(out_dir / "pids.json")
    conns = Connections(cfg.chain)
    conns.wait_all_ready()
    tx = TxService(conns, users, cfg.chain)
    shards = list(range(cfg.chain.num_shards))
    try:
        pool = extract(cfg.traffic.real_csv_path,
                       num_shards=cfg.chain.num_shards, num_users=cfg.scale.num_users,
                       user_base_index=cfg.scale.user_base_index,
                       value_floor_wei=int(cfg.traffic.value_floor_eth * ETH),
                       value_cap_wei=int(cfg.traffic.value_cap_eth * ETH),
                       limit=args_pairs)
        if len(pool) < 2:
            raise RuntimeError(f"need >=2 real cross-shard rows, got {len(pool)}")
        print(f"  real trace rows: {len(pool)} (each run on BOTH routes)")

        # 资金预算：每个 broker/分片严格使用 config.yaml 的 initial_balance_eth。
        # 若该规模下流动性不足，no_qualified 会显式计数并使实验失败，避免自动上浮
        # 资金后让配置中的初始余额失去实际含义。
        # 匹配走共享策略 static_broker（brokerlab/matching.py）：
        # eligible = 目的分片可用余额 ≥ v；种子 RNG 均匀选一个。num_brokers=1 时退化为
        # 恒选 0 号，行为与本实验旧版逐位一致；scale.num_brokers>1 时随机分流才生效。
        num_b = cfg.scale.num_brokers
        rng = random.Random(seed)
        outlay, coord_need = {}, {s: 0 for s in shards}
        for r in pool:
            k = (r.sender_idx, r.src_shard)
            outlay[k] = outlay.get(k, 0) + 2 * r.amount_wei
            coord_need[r.dst_shard] += r.amount_wei
        actor_idx = [cfg.scale.coordinator_index] + \
            [cfg.scale.broker_base_index + b for b in range(num_b)]
        tx.sync_nonces(actor_idx, shards)
        per_broker = {s: cfg.broker_initial_balance_wei for s in shards}
        avail = {b: {s: per_broker[s] for s in shards} for b in range(num_b)}
        for s in shards:
            for b in range(num_b):
                tx.set_balance_setup(users.address(cfg.scale.broker_base_index + b),
                                     s, per_broker[s])
            tx.set_balance_setup(users.address(cfg.scale.coordinator_index), s,
                                 coord_need[s] * 2 + 10 * ETH)
        for (idx, sh), need in outlay.items():
            tx.set_balance_setup(users.address(idx), sh, need + ETH)
        tx.sync_nonces(actor_idx, shards)

        bc = BrokerChain(tx, users, coordinator_index=cfg.scale.coordinator_index,
                         broker_base_index=cfg.scale.broker_base_index)
        before = {(a, s): tx.get_balance(a, s)
                  for a in sorted({users.address(i) for (i, _) in outlay}
                                  | {users.address(r.receiver_idx) for r in pool}
                                  | {users.address(cfg.scale.broker_base_index + b)
                                     for b in range(num_b)}
                                  | {users.address(cfg.scale.coordinator_index),
                                     BURN_ADDRESS})
                  for s in shards}

        rows = []
        no_qualified = 0

        def run_one(real, route):
            nonlocal no_qualified
            ctx = CTX(f"{real.ctx_id}:{'b' if route == ROUTE_BROKER else 'r'}",
                      real.sender_idx, real.receiver_idx,
                      real.src_shard, real.dst_shard, real.amount_wei,
                      origin=f"eth_mainnet_trace:{real.orig_from}->{real.orig_to}")
            chosen = -1
            if route == ROUTE_BROKER:
                chosen = select_broker({b: avail[b][real.dst_shard]
                                        for b in range(num_b)},
                                       real.amount_wei, rng)
                if chosen is None:      # 资金预算充足 ⇒ 理论上到不了；真到了计 no_qualified
                    no_qualified += 1
                    route = ROUTE_RELAY
                    res = bc.execute(ctx, route)
                else:
                    res = bc.execute(ctx, route, broker_idx=chosen)
                    if res.ok:
                        avail[chosen][real.src_shard] += real.amount_wei
                        avail[chosen][real.dst_shard] -= real.amount_wei
            else:
                res = bc.execute(ctx, route)
            t1, t2 = res.theta1, res.theta2
            # 跨片通信账单：relay=Θ1 本体+回执证明代理（实测）；broker=显式常数
            xmit = (t1.tx_bytes + t1.receipt_bytes) if route == ROUTE_RELAY else ctrl_bytes
            rows.append({
                "ctx_id": ctx.ctx_id, "route": route, "broker_id": chosen,
                "src": ctx.src_shard, "dst": ctx.dst_shard,
                "amount_wei": ctx.amount_wei, "ok": res.ok,
                "t1_block": t1.block, "t1_hops": t1.hops,
                "t1_secs": round(t1.confirm_ts - t1.submit_ts, 3),
                "t1_tx_bytes": t1.tx_bytes, "t1_receipt_bytes": t1.receipt_bytes,
                "t2_block": t2.block if t2 else None,
                "t2_hops": t2.hops if t2 else None,
                "t2_secs": round(t2.confirm_ts - t2.submit_ts, 3) if t2 else None,
                "t2_tx_bytes": t2.tx_bytes if t2 else None,
                "e2e_secs": round(t2.confirm_ts - t1.submit_ts, 3) if t2 and t2.ok else None,
                "hops_total": (t1.hops + t2.hops) if (t2 and t2.ok) else None,
                "xmit_bytes": xmit if res.ok else None,
            })

        t0 = time.monotonic()
        for i, real in enumerate(pool):
            order = [ROUTE_BROKER, ROUTE_RELAY] if i % 2 == 0 else [ROUTE_RELAY, ROUTE_BROKER]
            for route in order:
                run_one(real, route)
            if (i + 1) % 10 == 0:
                print(f"  {i+1}/{len(pool)} pairs ({len(rows)} CTX, "
                      f"{time.monotonic()-t0:.0f}s)")

        after = {(a, s): tx.get_balance(a, s) for (a, s) in before}
        system_drift = sum(after[k] - before[k] for k in before)
        failed = [r for r in rows if not r["ok"]]
        if failed:
            print(f"  !! {len(failed)} CTX failed (聚合仅统计成功行)")

        ok_rows = [r for r in rows if r["ok"]]
        by_route = {rt: [r for r in ok_rows if r["route"] == rt]
                    for rt in (ROUTE_BROKER, ROUTE_RELAY)}
        lat = {rt: {"e2e_secs": dist([r["e2e_secs"] for r in rs]),
                    "hops_total": dist([r["hops_total"] for r in rs]),
                    "t1_secs": dist([r["t1_secs"] for r in rs]),
                    "t2_secs": dist([r["t2_secs"] for r in rs])}
               for rt, rs in by_route.items()}
        comm = {rt: dist([r["xmit_bytes"] for r in rs]) for rt, rs in by_route.items()}
        footprint = {}
        for rt, rs in by_route.items():
            fp = {s: {"legs": 0, "tx_bytes": 0} for s in shards}
            for r in rs:
                fp[r["src"]]["legs"] += 1
                fp[r["src"]]["tx_bytes"] += r["t1_tx_bytes"]
                fp[r["dst"]]["legs"] += 1
                fp[r["dst"]]["tx_bytes"] += (r["t2_tx_bytes"] or 0)
            footprint[rt] = fp

        mb, mr = lat[ROUTE_BROKER]["e2e_secs"]["mean"], lat[ROUTE_RELAY]["e2e_secs"]["mean"]
        rel = abs(mb - mr) / max(mr, 1e-9)
        h1 = rel < 0.20
        cb, cr = comm[ROUTE_BROKER]["mean"], comm[ROUTE_RELAY]["mean"]
        h2 = cr > cb
        legs_b = sum(v["legs"] for v in footprint[ROUTE_BROKER].values())
        legs_r = sum(v["legs"] for v in footprint[ROUTE_RELAY].values())
        h3 = legs_b == legs_r

        print("\n" + "=" * 74)
        print("  判读（全部实测；唯一模型常数 = match_ctrl_bytes）")
        print("=" * 74)
        print(f"  H1 延迟  broker {mb:.3f}s vs relay {mr:.3f}s（相对差 {rel*100:.1f}%）→ {'VERIFIED' if h1 else 'NOT MET'}")
        print(f"  H2 跨片通信  broker {cb:.0f} B/CTX vs relay {cr:.0f} B/CTX（{cr/max(cb,1):.1f}×）→ {'VERIFIED' if h2 else 'NOT MET'}")
        print(f"  H3 链上足迹  broker {legs_b} legs vs relay {legs_r} legs → {'VERIFIED' if h3 else 'NOT MET'}")
        print(f"  全局净和  {system_drift} wei（应=0）→ {'OK' if system_drift == 0 else 'BROKEN'}")
        print("=" * 74)

        with open(out_dir / "ctx_rows.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        summary = {
            "params": to_params_dict(cfg, extra={
                "match_ctrl_bytes": ctrl_bytes, "seed": seed,
                "num_brokers": num_b, "no_qualified": no_qualified,
                "wall_s": round(time.monotonic()-t0, 1),
                "ctx_failed": len(failed)}),
            "latency": lat, "comm_xmit_bytes": comm,
            "onchain_footprint": footprint,
            "system_drift_wei": system_drift,
            "hypotheses": {"H1_latency_equal": h1, "H2_relay_comm_higher": h2,
                           "H3_footprint_parity": h3, "latency_rel_diff": round(rel, 4)},
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        make_figure(out_dir, rows, lat, comm, footprint, mb, mr, rel, cb, cr, h1, h2, h3)
        return (not failed) and no_qualified == 0 \
            and system_drift == 0 and h1 and h2 and h3
    finally:
        cluster.stop()


def make_figure(out_dir, rows, lat, comm, footprint, mb, mr, rel, cb, cr, h1, h2, h3):
    """三面板草图：(a) 延迟 ECDF 对比；(b) 每笔跨片通信账单；(c) 链上足迹。"""
    from brokerlab import plotting as P
    import matplotlib.pyplot as plt

    figs = out_dir / "figs"
    figs.mkdir(exist_ok=True)
    ok = [r for r in rows if r["ok"]]
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(13, 4), dpi=140)

    # (a) e2e 延迟 ECDF
    for rt, color, lab in ((ROUTE_BROKER, P.COLOR_BROKER, "Broker Path"),
                           (ROUTE_RELAY, P.COLOR_RELAY, "Relay Path")):
        vals = sorted(r["e2e_secs"] for r in ok if r["route"] == rt)
        ys = [(i + 1) / len(vals) for i in range(len(vals))]
        a.step(vals, ys, where="post", color=color, lw=1.6, label=lab)
    a.set_xlabel("End-to-End Confirmation Time (seconds)")
    a.set_ylabel("Cumulative Fraction of Completed CTXs")
    a.set_title(f"(a) End-to-End Latency Distribution\n"
                f"Broker mean: {mb:.2f} s; Relay mean: {mr:.2f} s")
    a.legend(title="Execution Path"); a.set_ylim(0, 1.02)

    # (b) 每笔跨片通信字节
    xs_b = [r["xmit_bytes"] for r in ok if r["route"] == ROUTE_BROKER]
    xs_r = [r["xmit_bytes"] for r in ok if r["route"] == ROUTE_RELAY]
    b.scatter([1] * len(xs_b), xs_b, s=8, alpha=.5, color=P.COLOR_BROKER)
    b.scatter([2] * len(xs_r), xs_r, s=8, alpha=.5, color=P.COLOR_RELAY)
    b.axhline(cb, color=P.COLOR_BROKER, ls=":", lw=1)
    b.text(1.06, cb + 20, f"{cb:.0f} B", color=P.COLOR_BROKER, fontsize=8)
    b.text(2.06, cr + 20, f"{cr:.0f} B", color=P.COLOR_RELAY, fontsize=8)
    b.set_xticks([1, 2])
    b.set_xticklabels(["Broker Path\n(Model Constant)",
                       "Relay Path\n(Measured)"])
    b.set_xlabel("Cross-Shard Execution Path")
    b.set_ylabel("Cross-Shard Communication per CTX (bytes)")
    b.set_title(f"(b) Cross-Shard Communication Cost\n"
                f"Relay/Broker mean ratio: {cr/max(cb,1):.1f}x")

    # (c) 链上足迹：每分片段字节
    labels, bv, rv = [], [], []
    for s, fp_b, fp_r in sorted(zip(footprint[ROUTE_BROKER],
                                    footprint[ROUTE_BROKER].values(),
                                    footprint[ROUTE_RELAY].values()),
                                key=lambda z: z[0]):
        labels.append(f"S{s}")
        bv.append(fp_b["tx_bytes"]); rv.append(fp_r["tx_bytes"])
    x = range(len(labels))
    c.bar([i - .18 for i in x], bv, width=.36, color=P.COLOR_BROKER,
          label="Broker Path")
    c.bar([i + .18 for i in x], rv, width=.36, color=P.COLOR_RELAY,
          label="Relay Path")
    c.set_xticks(list(x)); c.set_xticklabels(labels)
    c.set_xlabel("Shard")
    c.set_ylabel("Total On-Chain Transaction Size (bytes)")
    c.set_title("(c) On-Chain Transaction Footprint by Shard")
    c.legend(title="Execution Path")

    P.save(fig, figs / "exp001_overview.png")
    print(f"  figure: {figs / 'exp001_overview.png'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    args = ap.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)
    out = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    pairs = exp_param(cfg, "pairs", int)
    ctrl = exp_param(cfg, "match_ctrl_bytes", int)
    seed = exp_param(cfg, "seed", int)
    print(f"  exp001 · out: {out}")
    try:
        passed = run(cfg, pairs, ctrl, seed, out)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
    sys.exit(0 if passed else 1)
