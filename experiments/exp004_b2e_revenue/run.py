#!/usr/bin/env python3
"""exp004 · B2E 手续费收入与守恒恒等式 —— 独立启动脚本（执行 + 绘图）。

两个方案同流：b2e.off（现状基线）vs b2e.on（βF 进 broker，(1−β)F 显式烧掉）。
设计/判据见 PLAN_b2e_CN.md 与本目录 README.md；参数与费用单价见 config.yaml。
运行：python run.py [--set exp.count=40 --set exp.strategies=on]
产物：out/<ts>/{off,on}/b2e_trace.csv + summary.json + figs/exp004_revenue.png
"""
import argparse
import csv
import dataclasses
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
from brokerlab.config import ETH, apply_overrides, b2e_fees, load_config, to_params_dict
from brokerlab.identity import UserManager
from brokerlab.matching import select_broker
from brokerlab.real_data import extract
from brokerlab.tx import TxService


def exp_param(cfg, key, kind=int):
    """参数唯一来源=config.yaml 的 exp: 段，缺键即报错（exp001/002 同纪律）。"""
    if key not in cfg.exp:
        sys.exit(f"config.yaml 的 exp: 段缺少参数 {key!r}（本实验参数必须全部显式声明）")
    return kind(cfg.exp[key])


# ---------------------------------------------------------------------------
# 一个条件 = 一条全新链 + 同一 kept 流；fees 由方案决定（off ⇒ 全 0）
# ---------------------------------------------------------------------------

def run_condition(cfg, kept, balance_eth, seed, cond_dir: Path,
                  fee_broker: int, fee_burn: int):
    """顺序执行 kept，逐笔按 static_broker 路由，费用按方案注入机制层。

    off 方案 fee 全 0 ⇒ brokerchain 走 B2E 之前的旧路径（回归不变式）。
    两个方案同 seed ⇒ 选择序列一致；费用不改变 dst 侧 drain 动态
    （βF 留在 src 子账户，路由只看 dst），因此两个方案逐笔可比。"""
    F = fee_broker + fee_burn
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
    broker_addrs = [users.address(cfg.scale.broker_base_index + b) for b in range(num_b)]
    coord_addr = users.address(cfg.scale.coordinator_index)
    try:
        # —— setup 充值（唯一合法 setBalance 窗口）——
        init_wei = int(balance_eth * ETH)
        per_sender = {}          # sender_idx -> [Σv, n_ctx]（每笔无论路径都付 v+F）
        for k in kept:
            agg = per_sender.setdefault(k.sender_idx, [0, 0])
            agg[0] += k.amount_wei
            agg[1] += 1
        total_out = sum(k.amount_wei for k in kept)
        actors = ([cfg.scale.coordinator_index]
                  + [cfg.scale.broker_base_index + b for b in range(num_b)]
                  + list(per_sender))
        tx.sync_nonces(actors, shards)
        for s in shards:
            for addr in broker_addrs:
                tx.set_balance_setup(addr, s, init_wei)
            tx.set_balance_setup(coord_addr, s, total_out + 10 * ETH)
        for idx, (amt, cnt) in per_sender.items():
            tx.set_balance_setup(users.address(idx), kept[0].src_shard,
                                 amt + cnt * F + ETH)
        tx.sync_nonces(actors, shards)

        # 观察账户全集的 before 快照（sender/receiver/broker/coord/BURN）
        idxs = set(per_sender) | {k.receiver_idx for k in kept} \
            | {cfg.scale.broker_base_index + b for b in range(num_b)} \
            | {cfg.scale.coordinator_index}
        addrs = sorted({users.address(i) for i in idxs} | {BURN_ADDRESS})
        before = {(a, s): tx.get_balance(a, s) for a in addrs for s in shards}

        rng = random.Random(seed)
        bal = {b: {s: init_wei for s in shards} for b in range(num_b)}
        rows = []
        for i, k in enumerate(kept):
            chosen = select_broker({b: bal[b][k.dst_shard] for b in range(num_b)},
                                   k.amount_wei, rng)
            if chosen is None:
                route, fb, fx = ROUTE_RELAY, 0, F      # relay 政策：F 全额随 v 烧
            else:
                route, fb, fx = ROUTE_BROKER, fee_broker, fee_burn
            ctx = CTX(f"b2e_{i:04d}", k.sender_idx, k.receiver_idx,
                      k.src_shard, k.dst_shard, k.amount_wei,
                      origin=f"eth_mainnet_trace:{k.orig_from}->{k.orig_to}")
            res = bc.execute(ctx, route, max(chosen or 0, 0),
                             fee_broker_wei=fb, fee_burn_wei=fx)
            t1, t2, t1b = res.theta1, res.theta2, res.theta1b
            if res.ok and chosen is not None:
                bal[chosen][k.src_shard] += k.amount_wei      # 镜像只跟踪 v 流动
                bal[chosen][k.dst_shard] -= k.amount_wei
            rows.append({
                "i": i, "ctx_id": ctx.ctx_id, "block": t1.submit_block,
                "route": route, "broker_id": chosen if chosen is not None else -1,
                "ok": res.ok, "amount_wei": k.amount_wei,
                "fee_broker_wei": fb if res.ok else 0,
                "fee_burn_wei": fx if res.ok else 0,
                "legs": (2 if t1b is None else 3) if t2 is not None else (1 + (t1b is not None)),
                "t1b_block": t1b.block if t1b else None,
                "bal_dst_after_wei": sum(bal[b][k.dst_shard] for b in range(num_b)),
                "e2e_secs": round(t2.confirm_ts - t1.submit_ts, 3) if t2 and t2.ok else None,
            })
            if (i + 1) % 20 == 0:
                print(f"    {i+1}/{len(kept)} (served="
                      f"{sum(1 for r in rows if r['route']=='broker')}, relay="
                      f"{sum(1 for r in rows if r['route']=='relay')})")
        after = {(a, s): tx.get_balance(a, s) for (a, s) in before}
        with open(cond_dir / "b2e_trace.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        return rows, before, after, addrs, broker_addrs
    finally:
        cluster.stop()


# ---------------------------------------------------------------------------
# 三条守恒恒等式（PLAN_b2e §2），全部从链上差分 + 行账重算
# ---------------------------------------------------------------------------

def verify(rows, before, after, broker_addrs, fee_broker, fee_burn):
    """三条恒等式 + 费用完整性 + per-broker 收入核对。

    单向流前提（kept 全为 S_src→S_dst）：broker 每笔 served 同额进出两侧，
    链上总差分 = ΣβF 精确成立；多向流时 I2b 需改为扣除空间搬运项（M4 再说）。"""
    F = fee_broker + fee_burn
    shards = sorted({k[1] for k in before})
    ok_rows = [r for r in rows if r["ok"]]
    served = [r for r in ok_rows if r["route"] == ROUTE_BROKER]
    relay = [r for r in ok_rows if r["route"] == ROUTE_RELAY]
    drift = sum(after[k] - before[k] for k in before)
    db_total = sum(after[(a, s)] - before[(a, s)]
                   for a in broker_addrs for s in shards)
    burn_delta = sum(after[(BURN_ADDRESS, s)] - before[(BURN_ADDRESS, s)]
                     for s in shards)
    i1 = drift == 0
    i2 = db_total == sum(r["fee_broker_wei"] for r in served)
    i3 = burn_delta == sum(r["amount_wei"] for r in relay) + \
        sum(r["fee_burn_wei"] for r in ok_rows)
    fee_integrity = all(r["fee_broker_wei"] + r["fee_burn_wei"] == F for r in served) \
        and all(r["fee_burn_wei"] == F for r in relay)
    per_broker = {}
    for b, a in enumerate(broker_addrs):
        rev = sum(r["fee_broker_wei"] for r in served if r["broker_id"] == b)
        delta = sum(after[(a, s)] - before[(a, s)] for s in shards)
        per_broker[b] = {"served": sum(1 for r in served if r["broker_id"] == b),
                         "revenue_wei": rev, "chain_delta_wei": delta,
                         "match": delta == rev}
    # BURN 台账期望 = relay 的 v（Θ1 随本金烧掉）+ 全部行的 fee_burn
    #（relay 行 fee_burn=F，served 行 =(1−β)F）——off 方案 F=0 时仍有 relay v 项。
    burned_total = sum(r["amount_wei"] for r in relay) + \
        sum(r["fee_burn_wei"] for r in ok_rows)
    return {
        "n": len(rows), "served": len(served), "relayed": len(relay),
        "failed": [r["i"] for r in rows if not r["ok"]],
        "first_relay_i": next((r["i"] for r in rows if r["route"] == "relay"), None),
        "eta_end": round(len(served) / len(rows), 3),
        "revenue_wei": sum(r["fee_broker_wei"] for r in served),
        "burned_wei": burn_delta,
        "extra_burn_legs": sum(1 for r in rows if r["t1b_block"] is not None),
        "identities": {"I1_global_net_zero": i1,
                       "I2_dBroker_eq_sum_betaF": i2,
                       "I3_dBURN_eq_ledger": i3,
                       "I2b_per_broker_chain_eq_revenue":
                           all(v["match"] for v in per_broker.values()),
                       "fee_integrity_split_equals_F": fee_integrity},
        "global_net_sum_wei": drift,
        "broker_chain_delta_total_wei": db_total,
        "burn_chain_delta_wei": burn_delta,
        "burn_ledger_expected_wei": burned_total,
        "per_broker": per_broker,
        "e2e_mean_s": round(statistics.fmean([r["e2e_secs"] for r in ok_rows]), 3)
            if ok_rows else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    args = ap.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)

    count = exp_param(cfg, "count", int)
    scan = exp_param(cfg, "pool_scan", int)
    seed = exp_param(cfg, "seed", int)
    bal = exp_param(cfg, "balances_eth", float)
    strategies = [s.strip() for s in exp_param(cfg, "strategies", str).split(",")]
    if not set(strategies) <= {"off", "on"}:
        sys.exit(f"strategies 只能是 off/on 的子集，得到 {strategies}")

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
    F, fb, fx = b2e_fees(cfg)
    print(f"  多数方向 S{maj_src}->S{maj_dst}：{len(kept)} 笔，"
          f"合计 {sum(k.amount_wei for k in kept)/ETH:.1f} ETH")
    print(f"  b2e.on：F = {F/ETH:.6f} ETH/笔，β={cfg.b2e.fee_share} ⇒ βF = {fb/ETH:.6f}，"
          f"(1−β)F = {fx/ETH:.6f}")

    out_root = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    results, rows_by_tag = {}, {}
    for strat in strategies:
        enabled = strat == "on"
        sb, sf = (fb, fx) if enabled else (0, 0)
        print(f"\n  ==== 方案 b2e.{strat} ====")
        rows, before, after, _addrs, broker_addrs = run_condition(
            cfg, kept, bal, seed, out_root / strat, sb, sf)
        results[strat] = verify(rows, before, after, broker_addrs, sb, sf)
        rows_by_tag[strat] = rows
        st = results[strat]
        print(f"  {strat}: served {st['served']}/{st['n']} | relay 首次 i={st['first_relay_i']} "
              f"| 收入 {st['revenue_wei']/ETH:.6f} ETH | 恒等式 "
              f"{[v for v in st['identities'].values()]}")

    # β 敏感性（同一 served 流的外推表，纯算术、标注为 off-chain）
    on = results.get("on")
    beta_table = {}
    if on and F:
        for beta in ("0", "0.10", "0.25", "0.50", "1.0"):
            b9 = int(round(float(beta) * 10**9))
            beta_table[beta] = round(F * b9 // 10**9 * on["served"] / ETH, 6)

    all_ids = [v for st in results.values() for v in st["identities"].values()]
    no_fail = all(not st["failed"] for st in results.values())
    legs_ok = (on is None) or (on["extra_burn_legs"] == on["served"])
    off_ok = ("off" not in results) or all(
        r["legs"] == 2 and r["fee_broker_wei"] == 0 for r in rows_by_tag["off"])
    rev_ok = (on is None) or on["revenue_wei"] > 0
    passed = all(all_ids) and no_fail and legs_ok and off_ok and rev_ok

    print("\n" + "=" * 74)
    for strat, st in results.items():
        print(f"  b2e.{strat}: I1 {st['identities']['I1_global_net_zero']} | "
              f"I2 Δbroker={st['broker_chain_delta_total_wei']/ETH:.6f} ≟ "
              f"ΣβF={st['revenue_wei']/ETH:.6f} → "
              f"{st['identities']['I2_dBroker_eq_sum_betaF']} | "
              f"I3 ΔBURN={st['burn_chain_delta_wei']/ETH:.6f} ≟ 台账 "
              f"{st['burn_ledger_expected_wei']/ETH:.6f} → "
              f"{st['identities']['I3_dBURN_eq_ledger']}")
    if on:
        print(f"  on 方案额外烧币段：{on['extra_burn_legs']} 条 ≟ served {on['served']} → {legs_ok}")
        print(f"  per-broker 收入（served）：",
              {f"b{k}": f"{v['revenue_wei']/ETH:.6f}({v['served']})"
               for k, v in on["per_broker"].items()})
    print(f"  失败 CTX：{sum(len(st['failed']) for st in results.values())}")
    print("=" * 74)

    try:
        make_figure(out_root, results, rows_by_tag)
    except ImportError:
        print("  (matplotlib 不可用，跳过绘图)")

    (out_root / "summary.json").write_text(json.dumps({
        "params": to_params_dict(cfg, extra={"strategies": strategies,
                                             "F_wei": F, "beta": cfg.b2e.fee_share,
                                             "fee_broker_wei": fb, "fee_burn_wei": fx,
                                             "direction": f"S{maj_src}->S{maj_dst}",
                                             "stream_total_eth": round(
                                                 sum(k.amount_wei for k in kept) / ETH, 2)}),
        "conditions": results,
        "beta_sensitivity_offchain": beta_table,
        "gates": {"identities_all": all(all_ids), "no_failed_ctx": no_fail,
                  "burn_legs_eq_served": legs_ok, "off_arm_leg_sequence_pristine": off_ok,
                  "on_revenue_positive": rev_ok},
    }, indent=2))
    print(f"  artifacts: {out_root}/")
    return 0 if passed else 1


def make_figure(out_root, results, rows_by_tag):
    from brokerlab import plotting as P
    import matplotlib.pyplot as plt

    if "on" not in rows_by_tag:
        return
    figs = out_root / "figs"
    figs.mkdir(exist_ok=True)
    rows = rows_by_tag["on"]
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(13.5, 4), dpi=140)

    xs, ys, acc = [], [], 0
    for r in rows:
        if r["ok"] and r["route"] == "broker":
            acc += r["fee_broker_wei"] / ETH
        xs.append(r["i"])
        ys.append(acc)
    a.step(xs, ys, where="post", color=P.COLOR_BROKER, lw=1.6)
    a.set_xlabel("CTX index")
    a.set_ylabel("cumulative broker revenue (ETH)")
    a.set_title("(a) b2e.on: revenue accrues only via served CTX")

    on = results["on"]
    keys = sorted(on["per_broker"])
    srv = [on["per_broker"][k]["served"] for k in keys]
    rev = [on["per_broker"][k]["revenue_wei"] / ETH for k in keys]
    b.bar(range(len(keys)), srv, color=P.COLOR_BROKER, alpha=.55, label="served CTX")
    b2 = b.twinx()
    b2.plot(range(len(keys)), rev, "o--", color=P.COLOR_DATA, label="revenue ETH")
    b.set_xticks(range(len(keys)))
    b.set_xticklabels([f"B{k}" for k in keys])
    b.set_ylabel("served CTX")
    b2.set_ylabel("revenue (ETH)")
    b.set_title("(b) per-broker: uniform split -> exchangeable income")
    b.legend(fontsize=8, loc="upper left")

    st_off = results.get("off")
    cats = ["served", "relay", "burn legs"]
    offv = [st_off["served"], st_off["relayed"], st_off["extra_burn_legs"]] if st_off else []
    onv = [on["served"], on["relayed"], on["extra_burn_legs"]]
    if st_off:
        x = range(len(cats))
        c.bar([i - .18 for i in x], offv, width=.36, color=P.COLOR_GRAY, label="b2e.off")
        c.bar([i + .18 for i in x], onv, width=.36, color=P.COLOR_BROKER, label="b2e.on")
        c.set_xticks(list(x))
        c.set_xticklabels(cats)
        c.legend(fontsize=8)
    else:
        c.bar(cats, onv, color=P.COLOR_BROKER)
    c.set_title("(c) on-chain leg counts: same served/relay, +burn legs")
    P.save(fig, figs / "exp004_revenue.png")
    print(f"  figure: {figs / 'exp004_revenue.png'}")


if __name__ == "__main__":
    sys.exit(main())
