#!/usr/bin/env python3
"""exp003 · TDR 开/关对照 —— M4 验收实验。

同一条 10000 笔定向流、同一个路由 coordinator（exp007 基础设施），两个方案：
  plain —— 无 TDR：dst 被抽干 → 终审改判 relay（exp007 已量到 ~27%）；
  tdr   —— 每 broker 的 TdrDynamicEngine 自主规划：src 堆积、dst 枯竭触发
           excess_only → 两段真实交易搬钱（burn 自签 + coordinator 代铸到
           自己的 dst 子账户）→ dst 回血 → 改判率下降。
看三件事：TDR 是否有效（改判率/relay 量下降）、代价多少（吞吐/延迟/代铸负载）、
全程守恒是否依然成立（burn≡mint 台账 + 逐 broker 链上==账本）。
设计/判据见本目录 README.md；参数唯一来源 config.yaml。
"""
import argparse
import csv
import json
import multiprocessing as mp
import os
import queue
import statistics
import sys
import time
import traceback
from collections import deque
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from brokerlab.broker_engine import (END, assign_sender_groups, build_services,
                                     dynamic_engine_from_payload, group_key)
from brokerlab.brokerchain import BURN_ADDRESS
from brokerlab.chain import AnvilCluster, Connections
from brokerlab.config import (ETH, apply_overrides, b2e_fees, load_config,
                             to_params_dict)
from brokerlab.identity import NonceManager, UserManager
from brokerlab.matching import select_broker
from brokerlab.procmon import RSSWatchdog
from brokerlab.real_data import extract
from brokerlab.tdr_hook import tdr_engine_from_payload
from brokerlab.tx import TxService

import random


def exp_param(cfg, key, kind=int):
    """参数唯一来源=config.yaml 的 exp: 段，缺键即报错（各实验包统一纪律）。"""
    if key not in cfg.exp:
        sys.exit(f"config.yaml 的 exp: 段缺少参数 {key!r}（参数必须全部显式声明）")
    return kind(cfg.exp[key])


def dist(vals):
    """分布摘要：None 先剔除。"""
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"mean": None, "p50": None, "p95": None, "max": None, "n": 0}
    s = sorted(vals)
    q = lambda p: s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]
    return {"mean": round(sum(vals) / len(vals), 3), "p50": q(50), "p95": q(95),
            "max": max(vals), "n": len(vals)}


# ---------------------------------------------------------------------------
# 工作负载：与 exp006/007 完全同一构造（同 scan/过滤/种子 ⇒ 同一股流可比）
# ---------------------------------------------------------------------------

def build_workload(cfg, out_root):
    n = cfg.scale.num_brokers * exp_param(cfg, "ctx_per_broker", int)
    pool = extract(cfg.traffic.real_csv_path,
                   num_shards=cfg.chain.num_shards, num_users=cfg.scale.num_users,
                   user_base_index=cfg.scale.user_base_index,
                   value_floor_wei=int(cfg.traffic.value_floor_eth * ETH),
                   value_cap_wei=int(cfg.traffic.value_cap_eth * ETH),
                   limit=exp_param(cfg, "pool_scan", int))
    groups = {}
    for r in pool:
        groups.setdefault(group_key(asdict(r)), []).append(asdict(r))
    glist = list(groups.values())
    mean_g = len(pool) / max(len(glist), 1)
    cap = mean_g * exp_param(cfg, "group_cap_mult", float)
    kept = sorted((g for g in glist if len(g) <= cap), key=len)
    sel = []
    for g in kept:
        sel.extend(g)
        if len(sel) >= n:
            break
    sel = sel[:n]
    for i, c in enumerate(sel):
        c["arrival_pos"] = i
    total = sum(int(c["amount_wei"]) for c in sel)
    print(f"  workload: {len(sel)} 笔 | 合计 {total/ETH:.0f} ETH（与 exp006/007 同流）")
    return sel, total


# ---------------------------------------------------------------------------
# 起链与充值（同 exp007；两方案各自全新链）
# ---------------------------------------------------------------------------

def fund_chain(cfg, sel, total_wei, arm_dir, watchdog, fund_eth=None):
    nb = cfg.scale.num_brokers
    shards = list(range(cfg.chain.num_shards))
    arm_dir.mkdir(parents=True, exist_ok=True)
    users = UserManager(cfg.chain.mnemonic)
    cluster = AnvilCluster(cfg.chain, arm_dir / "logs")
    cluster.start()
    cluster.write_pids(arm_dir / "pids.json")
    for pid in json.loads((arm_dir / "pids.json").read_text()).values():
        watchdog.track(pid)
    conns = Connections(cfg.chain)
    conns.wait_all_ready()
    tx = TxService(conns, users, cfg.chain)
    fund_eth = exp_param(cfg, "balances_eth", float) if fund_eth is None else fund_eth
    init_wei = int(fund_eth * ETH)
    per_sender = {}
    _F = sum(b2e_fees(cfg)[1:])            # broker 路径 sender 付 v+F；relay burn 亦 v+F
    for c in sel:
        key = (c["sender_idx"], c["src_shard"])
        per_sender[key] = per_sender.get(key, 0) + c["amount_wei"] + _F
    actors = ([cfg.scale.coordinator_index]
              + [cfg.scale.broker_base_index + b for b in range(nb)]
              + [si for (si, _) in per_sender])
    tx.sync_nonces(actors, shards)
    broker_addrs = [users.address(cfg.scale.broker_base_index + b) for b in range(nb)]
    coord_addr = users.address(cfg.scale.coordinator_index)
    for s in shards:
        for a in broker_addrs:
            tx.set_balance_setup(a, s, init_wei)
        tx.set_balance_setup(
            coord_addr, s,
            int(total_wei * exp_param(cfg, "coord_budget_mult", float)) + 10 * ETH)
    for (idx, sh), amt in per_sender.items():
        tx.set_balance_setup(users.address(idx), sh, amt + ETH)
    tx.sync_nonces(actors, shards)
    idxs = ({si for (si, _) in per_sender} | {c["receiver_idx"] for c in sel}
            | {cfg.scale.broker_base_index + b for b in range(nb)}
            | {cfg.scale.coordinator_index})
    addrs = sorted({users.address(i) for i in idxs} | {BURN_ADDRESS})
    before = {(a, s): tx.get_balance(a, s) for a in addrs for s in shards}
    return cluster, tx, users, broker_addrs, before


# ---------------------------------------------------------------------------
# 路由 coordinator（exp007 同款块循环；TDR 代铸与 relay 代铸共用请求格式，
# 这里只多记一个 minted_wei 台账）
# ---------------------------------------------------------------------------

def _routing_coordinator_worker(cfg_plain, params, rate, window, ctxs,
                                ctx_qs, rep_q, req_q, ack_qs, close_q, readyq, doneq, gate):
    try:
        from concurrent.futures import ThreadPoolExecutor
        tx, users, scale = build_services(cfg_plain)
        nb = scale.num_brokers
        shards = list(range(params["num_shards"]))
        N = len(ctxs)
        init_wei = int(params["balances_eth"] * ETH)
        est = {b: {s: init_wei for s in shards} for b in range(nb)}
        n_rw = int(params["refresh_workers"])
        refresh_pool = ThreadPoolExecutor(max_workers=n_rw)
        refresh_txs = [build_services(cfg_plain)[0] for _ in range(n_rw)]
        nonces = NonceManager()
        rng = random.Random(int(params["route_seed"]))
        pend, infl_sent, assigned = {}, {}, {}
        h0, h_cache, last_blk_check = None, None, 0.0
        last_rel = 0
        release_pos = disp = done = minted = 0
        minted_wei = 0
        nq_est = nq_local = served_ct = relay_ct = 0
        backlog_peak = 0
        declared_seq = []
        refresh_ms = []
        pending_mint = {}
        now = time.monotonic
        aborted = None
        end_sent = False
        closes: set = set()
        perf = {"send": 0.0, "send_n": 0, "probe": 0.0, "probe_n": 0,
                "blk": 0.0, "blk_n": 0, "refresh": 0.0}
        budget_denied = 0
        budget_denied_wei = 0
        readyq.put(("coord", now()))
        gate.wait()
        t0 = now()
        while True:
            # ②.5 引擎收口上报：END 已发、所有在途请求处理完才允许退出
            try:
                while True:
                    closes.add(close_q.get_nowait())
            except queue.Empty:
                pass
            if end_sent and len(closes) >= params["num_brokers"] \
                    and req_q.empty() and not pending_mint:
                break
            for _ in range(16):                         # 每轮最多受理 16 条
                try:
                    rq = req_q.get_nowait()
                except queue.Empty:
                    break
                try:
                    t_send = now()
                    h = tx.send_transfer(scale.coordinator_index,
                                         users.address(rq["receiver_idx"]),
                                         rq["amount_wei"], rq["dst_shard"])
                    perf["send"] += (now() - t_send) * 1000
                    perf["send_n"] += 1
                    pending_mint[h] = [rq["ref"], rq["engine"], rq["dst_shard"],
                                       now(), now() + params["probe_first_s"]]
                    minted += 1
                    minted_wei += int(rq["amount_wei"])
                except Exception:
                    # 预算耗尽/链异常：显式拒答——引擎把该段记终态，台账闭合，
                    # coordinator 绝不带着未答请求离开，更绝不崩。
                    ack_qs[rq["engine"]].put((rq["ref"], 0, None))
                    budget_denied += 1
                    budget_denied_wei += int(rq["amount_wei"])
            tn = now()
            for mh, p in list(pending_mint.items()):
                if tn < p[4]:
                    continue
                p[4] = tn + params["probe_s"]
                t_pr = now()
                r = tx.probe_receipt(mh, p[2])
                perf["probe"] += (now() - t_pr) * 1000
                perf["probe_n"] += 1
                if r is not None:
                    ack_qs[p[1]].put((p[0], r["status"], r["block"]))
                    pending_mint.pop(mh)
                elif tn - p[3] > params["relay_timeout_s"]:
                    ack_qs[p[1]].put((p[0], 0, None))
                    pending_mint.pop(mh)
            try:
                while True:
                    b, cid, route, ok, src, dst, v = rep_q.get_nowait()
                    done += 1
                    ab, key = assigned.pop(cid)
                    infl_sent[key] = max(0, infl_sent.get(key, 0) - 1)
                    est[ab][src] -= v
                    est[ab][dst] += v
                    if ok and route == "broker":
                        served_ct += 1
                        est[ab][src] += v
                        est[ab][dst] -= v
                    elif ok and route == "relay":
                        relay_ct += 1
                        nq_local += 1
            except queue.Empty:
                pass
            tbc = now()
            if h_cache is None or (tbc - last_blk_check) >= params["release_poll_s"]:
                t_bk = now()
                h_cache = min(tx.block_number(s) for s in shards)
                perf["blk"] += (now() - t_bk) * 1000
                perf["blk_n"] += 1
                last_blk_check = tbc
                if h0 is None:
                    h0 = h_cache
                rel_target = min(N, max(0, h_cache - h0) * rate)
                if rel_target != last_rel:
                    declared_seq.append(int(rel_target))
                    last_rel = rel_target
                if (h_cache - h0) % max(1, int(params["refresh_every_blocks"])) == 0 \
                        and rel_target > last_rel:
                    tr_ = now()
                    jobs = [(b2, s) for b2 in range(nb) for s in shards]
                    per_reader = [[] for _ in range(n_rw)]
                    for i, j in enumerate(jobs):
                        per_reader[i % n_rw].append(j)

                    def _read_chunk(ci_chunk):
                        ci, chunk = ci_chunk
                        rt = refresh_txs[ci]
                        return [(b2, s, rt.get_balance(
                            users.address(scale.broker_base_index + b2), s))
                            for b2, s in chunk]
                    results = {}
                    for lst in refresh_pool.map(_read_chunk, list(enumerate(per_reader))):
                        for b2, s, val in lst:
                            results[(b2, s)] = val
                    for b2 in range(nb):
                        est[b2] = {s: results[(b2, s)] for s in shards}
                    refresh_ms.append((now() - tr_) * 1000)
                    perf["refresh"] += refresh_ms[-1]
            else:
                rel_target = last_rel
            while release_pos < rel_target:
                c = ctxs[release_pos]
                key = (c["sender_idx"], c["src_shard"])
                pend.setdefault(key, deque()).append(c)
                release_pos += 1
            backlog = rel_target - disp
            backlog_peak = max(backlog_peak, backlog)
            if backlog > params["max_backlog"]:
                aborted = f"backlog {backlog} > {params['max_backlog']}"
                for q in ctx_qs:
                    q.put_nowait(END)
                break
            ready = sorted((pend[k][0]["arrival_pos"], k)
                           for k in pend if infl_sent.get(k, 0) < window)
            for _, key in ready[:int(params["dispatch_per_tick"])]:
                dq = pend.get(key)
                if not dq:
                    continue
                c = dq.popleft()
                v, dst, src = c["amount_wei"], c["dst_shard"], c["src_shard"]
                elig = {b2: est[b2][dst] for b2 in range(nb) if est[b2][dst] >= v}
                if elig:
                    b_sel = select_broker(elig, v, rng)
                else:
                    nq_est += 1
                    b_sel = rng.randrange(nb)
                nonce = nonces.next(users.address(c["sender_idx"]), src)
                est[b_sel][src] += v
                est[b_sel][dst] -= v
                item = dict(c)
                item["nonce"] = nonce
                ctx_qs[b_sel].put_nowait(item)
                assigned[c["ctx_id"]] = (b_sel, key)
                infl_sent[key] = infl_sent.get(key, 0) + 1
                disp += 1
                if not dq:
                    pend.pop(key)
            if os.environ.get("EXP003_DEBUG"):
                print(f"[coord] h={h_cache} rel={rel_target} disp={disp} done={done} "
                      f"mintpend={len(pending_mint)} reqq={req_q.qsize()}", flush=True)
            if disp >= N and done >= N and not end_sent:
                for q in ctx_qs:
                    q.put_nowait(END)        # 引擎停止开新事件；coordinator 继续服务在途
                end_sent = True
            if now() - t0 > N / max(rate, 1) + params["drain_tail_s"]:
                aborted = f"deadline（派发 {disp}/{N}，落定 {done}/{N}，收口 {len(closes)}）"
                if not end_sent:
                    for q in ctx_qs:
                        q.put_nowait(END)
                    end_sent = True
                # 清账退出：给每一个未完成请求显式回执 status=0，
                # 让引擎把 in-flight 段判终态——coordinator 绝不带着未答请求离开。
                for _ in range(100000):
                    try:
                        rq = req_q.get_nowait()
                    except queue.Empty:
                        break
                    ack_qs[rq["engine"]].put((rq["ref"], 0, None))
                for h, pnd in list(pending_mint.items()):
                    ack_qs[pnd[1]].put((pnd[0], 0, None))
                deadline_ = now() + 15.0
                while len(closes) < params["num_brokers"] and now() < deadline_:
                    time.sleep(0.1)
                    try:
                        while True:
                            closes.add(close_q.get_nowait())
                    except queue.Empty:
                        pass
                aborted += f"，清账后收口 {len(closes)}"
                break
            time.sleep(params.get("loop_sleep_s", 0.02))
        doneq.put({"dispatched": disp, "settled": done, "served": served_ct,
                   "relay_settled": relay_ct, "no_qualified_est": nq_est,
                   "no_qualified_local": nq_local, "backlog_peak": int(backlog_peak),
                   "minted": minted, "minted_wei": minted_wei,
                   "budget_denied": budget_denied,
                   "budget_denied_wei": budget_denied_wei,
                   "closes": len(closes), "end_sent": end_sent,
                   "perf": {k: (round(v, 1) if isinstance(v, float) else v)
                            for k, v in perf.items()},
                   "perf_avg_send_ms": round(perf["send"] / max(perf["send_n"], 1), 2),
                   "perf_avg_probe_ms": round(perf["probe"] / max(perf["probe_n"], 1), 2),
                   "perf_avg_blk_ms": round(perf["blk"] / max(perf["blk_n"], 1), 2),
                   "declared_seq": [int(x) for x in declared_seq],
                   "refresh_ms_mean": round(statistics.fmean(refresh_ms), 1)
                   if refresh_ms else None,
                   "aborted": aborted, "error": None})
    except BaseException:
        doneq.put({"dispatched": 0, "settled": 0, "error": traceback.format_exc()})


def _engine_worker(payload, ctx_q, rep_q, req_q, ack_q, close_q, readyq, outq, gate):
    b = payload["broker_idx"]
    try:
        if payload.get("tdr_params"):
            eng = tdr_engine_from_payload(payload, ctx_q, rep_q, req_q, ack_q)
        else:
            eng = dynamic_engine_from_payload(payload, ctx_q, rep_q, req_q, ack_q)
        if payload.get("relay_probe"):       # 诊断：记录每次 relay 决策的账本状态
            eng._relay_probe = []
        readyq.put(("engine", b))
        gate.wait()
        env = eng.run_blocking()
        outq.put(env)
        close_q.put(b)                       # 收口：本引擎再无任何在途段
    except BaseException:
        readyq.put(("engine_error", b, traceback.format_exc()))
        outq.put({"broker_idx": b, "rows": [], "error": traceback.format_exc(),
                  "n_rcvd": 0, "relay_fallback": 0})
        close_q.put(b)


def _wait_ready(readyq, doneq, budget_s, label):
    """等 ready 信号；doneq 出现内容 = 对方进程提前退出，错误原样带出。"""
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        try:
            item = readyq.get(timeout=1.0)
            if isinstance(item, tuple) and item[0] == "engine_error":
                raise RuntimeError(f"{label} 构造失败 broker_{item[1]}：\n{item[2]}")
            return item
        except RuntimeError:
            raise
        except Exception:
            try:
                env = doneq.get_nowait()
            except Exception:
                continue
            raise RuntimeError(f"{label} 提前退出：{env.get('error') or env}")
    raise TimeoutError(f"{label} ready 超时 {budget_s}s")


# ---------------------------------------------------------------------------
# 一个方案：起链 → coordinator + 50 引擎（plain 或 tdr）→ 落定 → 三重对账
# ---------------------------------------------------------------------------

def run_arm(cfg, arm, sel, total_wei, out_root, watchdog,
            engine="plain", fund_eth=None, cap_mult=None, eps=None, chi=None,
            hl=None, qmin=None, frac=None):
    """跑一个臂。engine ∈ plain | proportional | valve | base | topup；
    fund_eth 覆盖 balances_eth；cap_mult 仅 valve 用；eps 覆盖 tdr_epsilon；
    chi 覆盖事件最小间隔；hl 覆盖 EWMA 半衰期；qmin 覆盖 τ 地板；
    frac ∈ (0,1]：只有该比例的 broker 装备 TDR（fig6c 消融用；None/1=全员）。
    B2E：cfg.b2e.enabled=true 时 broker 路径加 Θ1a 全额费用 + Θ1b 烧币腿
    （终态与 brokerchain 串行版逐元一致；relay burn = v+F、mint = v；
    TDR 转账按 charges_rebalancing=false 不收费）。"""
    nb = cfg.scale.num_brokers
    rate = exp_param(cfg, "rate", int)
    shards = list(range(cfg.chain.num_shards))
    N = len(sel)
    arm_dir = out_root / arm
    fund_eth = exp_param(cfg, "balances_eth", float) if fund_eth is None else fund_eth
    cluster, tx, users, broker_addrs, before = fund_chain(cfg, sel, total_wei,
                                                          arm_dir, watchdog,
                                                          fund_eth=fund_eth)
    ctxm = mp.get_context("spawn")
    procs, coord = [], None
    try:
        params = {k: exp_param(cfg, k, float) for k in
                  ("max_inflight", "poll_s", "probe_s", "probe_first_s",
                   "relay_timeout_s", "engine_timeout_s")}
        _Ftot, _fb, _fbrn = b2e_fees(cfg)
        params.update({"fee_broker_wei": _fb, "fee_burn_wei": _fbrn,
                       "num_shards": cfg.chain.num_shards,
                       "balances_eth": fund_eth,
                       "route_seed": exp_param(cfg, "route_seed", int),
                       "refresh_every_blocks": 1, "refresh_workers": 8,
                       "max_backlog": exp_param(cfg, "max_backlog", int),
                       "dispatch_per_tick": exp_param(cfg, "dispatch_per_tick", int),
                       "loop_sleep_s": exp_param(cfg, "loop_sleep_s", float),
                       "release_poll_s": exp_param(cfg, "release_poll_s", float),
                       "drain_tail_s": exp_param(cfg, "drain_tail_s", float)})
        tdr_params = None
        if engine != "plain":
            pol = {"proportional": "proportional", "valve": "valve",
                   "base": "base", "topup": "topup"}.get(engine, engine)
            tdr_params = {
                "num_shards": cfg.chain.num_shards,
                "window_blocks": exp_param(cfg, "tdr_window_blocks", int),
                "epsilon": (exp_param(cfg, "tdr_epsilon", float)
                            if eps is None else eps),
                "q_min": (exp_param(cfg, "tdr_q_min", float)
                          if qmin is None else qmin),
                "chi_blocks": (exp_param(cfg, "tdr_chi_blocks", int)
                               if chi is None else int(chi)),
                "timeout_blocks": exp_param(cfg, "tdr_timeout_blocks", int),
                "trigger_mode": exp_param(cfg, "tdr_trigger_mode", str),
                "offset_blocks": 0,          # 错峰在 per-engine 注入（见下）
                "block_poll_s": exp_param(cfg, "tdr_block_poll_s", float),
                "policy": pol,
                "valve_init_wei": int(fund_eth * ETH),
                "valve_cap_mult": (exp_param(cfg, "tdr_cap_mult", float)
                                   if cap_mult is None else cap_mult),
                "base_lead_blocks": exp_param(cfg, "tdr_base_lead_blocks", float),
                "base_safety": exp_param(cfg, "tdr_base_safety", float),
                "base_floor_frac": exp_param(cfg, "tdr_base_floor_frac", float),
                "demand_ewma_half_life": (exp_param(cfg, "tdr_ewma_half_life", float)
                                          if hl is None else hl),
            }
        cfg_plain = {"chain": asdict(cfg.chain), "scale": asdict(cfg.scale)}
        init_wei = int(fund_eth * ETH)
        # fig6c 消融：frac<1 时只有抽中的 broker 装 TDR，其余为动态 plain 引擎
        equipped = None
        if engine != "plain" and frac is not None and frac < 1.0:
            k = max(1, round(nb * float(frac)))
            equipped = set(random.Random(int(exp_param(cfg, "seed", int)) * 7919
                                         + int(round(frac * 1000))).sample(
                                         range(nb), k))
            print(f"  [frac={frac}] 装备 TDR 的 broker: {len(equipped)}/{nb}")
        ctx_qs = [ctxm.Queue() for _ in range(nb)]
        rep_q, req_q = ctxm.Queue(), ctxm.Queue()
        ack_qs = [ctxm.Queue() for _ in range(nb)]
        readyq, outq, doneq = ctxm.Queue(), ctxm.Queue(), ctxm.Queue()
        close_q = ctxm.Queue()
        gate = ctxm.Event()
        params["num_brokers"] = nb
        coord = ctxm.Process(target=_routing_coordinator_worker,
                             args=(cfg_plain, params, rate,
                                   exp_param(cfg, "sender_window", int),
                                   [dict(c) for c in sel], ctx_qs, rep_q, req_q,
                                   ack_qs, close_q, readyq, doneq, gate))
        coord.start()
        watchdog.track(coord.pid)
        _wait_ready(readyq, doneq, exp_param(cfg, "t_ready_budget_s", float),
                    "coord")
        t_launch = time.monotonic()
        relay_probe = bool(os.environ.get("EXP003_RELAY_PROBE"))
        for b in range(nb):
            my_tdr = tdr_params
            if equipped is not None and b not in equipped:
                my_tdr = None                 # 未装备 ⇒ plain 动态引擎
            payload = {"cfg": cfg_plain, "broker_idx": b,
                       "init_wei_by_shard": {s: init_wei for s in shards},
                       "params": params, "tdr_params": my_tdr,
                       "relay_probe": relay_probe}
            if my_tdr:                        # 冷启动错峰：按 broker 索引偏移
                payload["tdr_params"] = dict(my_tdr, offset_blocks=b % 5)
            p = ctxm.Process(target=_engine_worker,
                             args=(payload, ctx_qs[b], rep_q, req_q, ack_qs[b],
                                   close_q, readyq, outq, gate))
            p.start()
            procs.append(p)
            watchdog.track(p.pid)
        for _ in range(nb):
            _wait_ready(readyq, doneq, exp_param(cfg, "t_ready_budget_s", float),
                        "engine")
        t_ready = time.monotonic() - t_launch
        gate.set()
        t0 = time.monotonic()
        envs = {}
        deadline = t0 + N / rate + params["drain_tail_s"] + 120
        while len(envs) < nb:
            if watchdog.tripped:
                raise RuntimeError("RSS watchdog tripped")
            if time.monotonic() > deadline:
                errs = [e.get("error") for e in envs.values() if e.get("error")]
                for er in errs[:2]:
                    print("  -- engine error --\n", er)
                raise TimeoutError(f"{arm}: 引擎未在时限内全部落定（收集 {len(envs)}/{nb}）")
            try:
                env = outq.get(timeout=1.0)
                envs[env["broker_idx"]] = env
                continue
            except Exception:
                pass
            if not coord.is_alive():
                # coordinator 已退：先掏空 outq（其 feeder 已 flush），仍凑不齐才异常
                try:
                    while True:
                        env = outq.get_nowait()
                        envs[env["broker_idx"]] = env
                except Exception:
                    pass
                if len(envs) < nb:
                    errs = [e.get("error") for e in envs.values() if e.get("error")]
                    for er in errs[:2]:
                        print("  -- engine error --\n", er)
                    try:
                        cd = doneq.get_nowait()
                        print("  -- coordinator 终态 --", {k: cd.get(k) for k in
                              ("dispatched", "settled", "aborted", "error")})
                    except Exception:
                        print("  -- coordinator 死了且没留终态（硬崩溃）--")
                    raise RuntimeError(f"{arm}: coordinator 死亡时仍有 "
                                       f"{nb - len(envs)} 个引擎未落定")
        coord_env = doneq.get(timeout=120)
        coord.join(timeout=30)
        for p in procs:
            p.join(timeout=10)
        wall = time.monotonic() - t0
        last_settles = [e["t_last_settle_mono"] for e in envs.values()
                        if e.get("t_last_settle_mono")]
        wall_ctx = (max(last_settles) - t0) if last_settles else wall
        after = {(a, s): tx.get_balance(a, s) for (a, s) in before}
        probes = [p for e in envs.values() for p in (e.get("relay_probe") or [])]
        if probes:
            (arm_dir / "relay_probe.json").write_text(json.dumps(probes))

        # —— 对账（TDR 臂的期望值包含搬运项）——
        rows = [r for e in envs.values() for r in e.get("rows", [])]
        served = [r for r in rows if r["ok"] and r["route"] == "broker"]
        relay = [r for r in rows if r["ok"] and r["route"] == "relay"]
        failed = [r for r in rows if not r["ok"]]
        per = {b: dict.fromkeys(shards, init_wei) for b in range(nb)}
        for r in rows:
            if r["ok"] and r["route"] == "broker":
                # B2E：Θ1a 到账 v+F、Θ1b 随即烧 (1−β)F ⇒ src 净进账 v+βF
                per[r["broker_idx"]][r["src"]] += (r["amount_wei"]
                                                   + r.get("fee_broker_wei", 0))
                per[r["broker_idx"]][r["dst"]] -= r["amount_wei"]
        tdr_moves = []
        for b, e in envs.items():
            for (s, d, a) in (e.get("tdr") or {}).get("moves", []):
                per[b][s] -= a
                per[b][d] += a
                tdr_moves.append((b, s, d, a, "done"))
            # burn 已落而 mint 未成的损耗：src 侧真实减少，必须进期望值
            for (s, d, a) in (e.get("tdr") or {}).get("lost_moves", []):
                per[b][s] -= a
                tdr_moves.append((b, s, d, a, "lost"))
        ledger_bad = 0
        bad_cells = []
        for b, a in enumerate(broker_addrs):
            for s in shards:
                if after[(a, s)] != per[b][s]:
                    ledger_bad += 1
                    if len(bad_cells) < 8:
                        bad_cells.append((b, s, after[(a, s)] // 10**15,
                                          per[b][s] // 10**15))
        if bad_cells:
            print("  -- G3a diff cells (eth):", bad_cells)
        drift = sum(after[k] - before[k] for k in before)
        tdr_snap = {k: sum((e.get("tdr") or {}).get(k, 0) for e in envs.values())
                    for k in ("events_opened", "transfers_done", "moved_wei",
                              "transfers_lost", "lost_in_transit_wei")}
        # 注意：lost 的 burn 也在链上——但 tdr_moves 聚合里 "lost" 行只进 G3a 期望与
        # burn_ledger（sum m[3]），不重复计入 moved_wei。
        # burn≡mint 台账：ΔBURN == Σrelay v + Σtdr 已确认 burn(moves+lost)
        burn_delta = sum(after[(BURN_ADDRESS, s)] - before[(BURN_ADDRESS, s)]
                         for s in shards)
        # 台账：ctx-relay 的 burn（v） + TDR 已确认 burn（moves=done + lost_moves）
        burn_ledger = (sum(r["amount_wei"] for r in relay) +
                       sum(r.get("fee_burn_wei", 0) for r in rows
                           if r["ok"]) +              # relay 行=全额 F；broker 行=(1−β)F
                       sum(m[3] for m in tdr_moves))
        ids = {r["ctx_id"] for r in rows}
        # —— 链上段数口径（用户收紧版）——
        # 每笔落定的 CTX 恰好 2 段：broker 路径 = Θ1+Θ2；relay = burn+mint。
        # 所以 CTX 基线段数恒为 2N，与走哪条路无关。
        # TDR 每笔成功转移 = 2 段（burn 段 + coordinator mint 段）；
        # 判失的 lost 转移 burn 段在链上、mint 段不在 → 只算 1 段。
        # B2E 开启时，broker 路径每笔多一段 Θ1b 烧币（relay 无附加段、
        # TDR 按 charges_rebalancing=false 不收费）。
        _fbrn_legs = bool(served) and any(
            r.get("fee_burn_wei", 0) for r in served)
        ctx_legs = 2 * (len(served) + len(relay)) + (len(served) if _fbrn_legs else 0)
        tdr_legs = 2 * tdr_snap["transfers_done"] + tdr_snap["transfers_lost"]
        stats = {
            "arm": arm, "engine": engine, "fund_eth": round(fund_eth, 2),
            "rate": rate, "n": len(rows),
            "served": len(served), "relayed": len(relay), "failed": len(failed),
            "wall_s": round(wall, 2), "t_ready_s": round(t_ready, 2),
            # CTX 服务吞吐按"全部 CTX 落定"计；TDR 搬运是后台事务，其收尾单列
            "wall_ctx_s": round(wall_ctx, 2),
            "tdr_close_tail_s": round(wall - wall_ctx, 2),
            "throughput_ctx_per_s": round(len(rows) / wall_ctx, 2) if wall_ctx > 0 else None,
            "e2e_secs": dist([r["e2e_secs"] for r in rows if r["ok"]]),
            "hops_total": dist([r["hops_total"] for r in rows if r["ok"]]),
            "legs": {"ctx_legs": ctx_legs, "tdr_legs": tdr_legs,
                     "tdr_share_of_ctx_legs": round(tdr_legs / ctx_legs, 4)
                     if ctx_legs else None},
            "gates": {
                "G1_all_ok": len(failed) == 0,
                "G2_no_dup_no_loss": ids == {c["ctx_id"] for c in sel},
                "G3a_broker_ledger_eq_chain": ledger_bad == 0,
                "G3b_global_net_zero": drift == 0,
                "G4_reports_complete": (coord_env.get("dispatched") == N
                                        and coord_env.get("settled") == N),
            },
            "ledger_mismatch_cells": ledger_bad,
            "global_net_sum_wei": drift,
            "coord": {k: coord_env.get(k) for k in
                      ("dispatched", "settled", "served", "relay_settled",
                       "no_qualified_est", "no_qualified_local", "backlog_peak",
                       "minted", "minted_wei", "budget_denied",
                       "budget_denied_wei", "refresh_ms_mean", "aborted")},
            "tdr": tdr_snap,
            "b2e": {"enabled": bool(cfg.b2e.enabled),
                    "fee_broker_wei_served": sum(
                        r.get("fee_broker_wei", 0) for r in rows if r["ok"]),
                    "fee_burn_wei_total": sum(
                        r.get("fee_burn_wei", 0) for r in rows if r["ok"])},
            "burn_reconcile": {"chain_delta_wei": burn_delta,
                               "ledger_wei": burn_ledger,
                               "minted_wei": coord_env.get("minted_wei"),
                               "ok": burn_delta == burn_ledger},
        }
        if rows:
            with open(arm_dir / "ctx_rows.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader(); w.writerows(rows)
        with open(arm_dir / "tdr_moves.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["broker_idx", "src_shard", "dst_shard", "amount_wei", "state"])
            w.writerows(tdr_moves)
        (arm_dir / "coordinator.json").write_text(json.dumps(coord_env))
        return stats
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
        if coord is not None and coord.is_alive():
            coord.terminate()
        cluster.stop()


def make_figure(out_root, results):
    """多臂总览图：(a) 改判数 (b) relay/搬运量 (c) CTX e2e ECDF。
    results: {tag: stats}（tag 顺序即图的横轴顺序）。"""
    from brokerlab import plotting as P
    import matplotlib.pyplot as plt
    tags = list(results.keys())
    figs = out_root / "figs"
    figs.mkdir(exist_ok=True)
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(13.5, 4), dpi=140)
    cols = [P.COLOR_BROKER if results[t]["engine"] != "plain" else P.COLOR_GRAY
            for t in tags]
    a.bar(tags, [results[t]["coord"]["no_qualified_local"] for t in tags],
          color=cols)
    a.set_ylabel("final-review rejections (CTX)")
    a.set_title("(a) relay fallback by arm")
    b.bar(tags, [results[t]["legs"]["tdr_legs"] for t in tags], color=cols)
    b.set_ylabel("TDR on-chain legs")
    b.set_title("(b) TDR chain legs")
    for t in tags:
        s = results[t]
        rows = []
        with open(out_root / s["arm"] / "ctx_rows.csv") as f:
            for r in csv.DictReader(f):
                if r["e2e_secs"]:
                    rows.append(float(r["e2e_secs"]))
        rows.sort()
        ys = [(i + 1) / len(rows) for i in range(len(rows))]
        col = P.COLOR_BROKER if s["engine"] != "plain" else P.COLOR_GRAY
        c.step(rows, ys, where="post", color=col, lw=1.5, label=t)
    c.set_xlabel("e2e (s)"); c.set_ylabel("ECDF")
    c.set_title("(c) latency cost"); c.legend(fontsize=8)
    P.save(fig, figs / "exp003_tdr.png")
    print(f"  figure: {figs / 'exp003_tdr.png'}")


def _arm_spec(token):
    """把臂 token 解析成 (name, engine, fund_eth, cap_mult, eps, chi, hl)。
    token 形如 plain/tdr/valve/base/topup，后缀 @fund @cap @eps @chi @hl @qmin：
      cap 仅 valve；eps 仅 demand 类策略；chi 事件间隔；hl EWMA 半衰期；
      qmin 覆盖 τ 地板比例。fund=None ⇒ 用 cfg 的 balances_eth。"""
    parts = token.split("@")
    name = parts[0]
    engine = {"plain": "plain", "tdr": "proportional", "valve": "valve",
              "base": "base", "topup": "topup"}.get(name, "plain")
    fund = float(parts[1]) if len(parts) > 1 and parts[1] else None
    cap = float(parts[2]) if len(parts) > 2 and parts[2] else None
    eps = float(parts[3]) if len(parts) > 3 and parts[3] else None
    chi = float(parts[4]) if len(parts) > 4 and parts[4] else None
    hl = float(parts[5]) if len(parts) > 5 and parts[5] else None
    qmin = float(parts[6]) if len(parts) > 6 and parts[6] else None
    frac = float(parts[7]) if len(parts) > 7 and parts[7] else None
    return name, engine, fund, cap, eps, chi, hl, qmin, frac


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    ap.add_argument("--out-root", default=None,
                    help="产物根目录（exp008 复用本管线时指定；默认本包 out/）")
    args = ap.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)
    base = Path(args.out_root) if args.out_root else HERE / "out"
    out_root = base / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root.mkdir(parents=True)
    tokens = [a.strip() for a in exp_param(cfg, "arms", str).split(",") if a.strip()]
    print(f"  exp003 · out: {out_root} | rate={exp_param(cfg, 'rate', int)} | arms={tokens}")

    sel, total_wei = build_workload(cfg, out_root)
    watchdog = RSSWatchdog(exp_param(cfg, "rss_cap_mb", float))
    watchdog.start()
    results = {}
    try:
        for token in tokens:
            name, engine, fund, cap, eps, chi, hl, qmin, frac = _arm_spec(token)
            tag = f"{name}@{fund}" if fund else name
            for v in (cap, eps, chi, hl, qmin):
                if v is not None:
                    tag = f"{tag}@{v}"
            if frac is not None:
                tag = f"{tag}@p{int(round(frac * 100))}"
            print(f"\n  ==== 方案 {tag}（engine={engine}）====")
            s = run_arm(cfg, tag, sel, total_wei, out_root, watchdog,
                        engine=engine, fund_eth=fund, cap_mult=cap, eps=eps,
                        chi=chi, hl=hl, qmin=qmin, frac=frac)
            results[tag] = s
            co = s["coord"]
            extra = (f" | events {s['tdr']['events_opened']} "
                     f"| 搬运 {s['tdr']['moved_wei']/ETH:.1f} ETH "
                     f"| 段占比 {s['legs']['tdr_share_of_ctx_legs']}") if engine != "plain" else ""
            print(f"  {tag}: {s['throughput_ctx_per_s']} CTX/s | relay {s['relayed']} "
                  f"| 改判 {co['no_qualified_local']} | 拒答 {co['budget_denied']} "
                  f"| net {s['global_net_sum_wei']}{extra}")
    finally:
        watchdog.stop()

    gates = {}
    for tag, s in results.items():
        for gk, gv in s["gates"].items():
            gates[f"{tag}:{gk}"] = gv
        gates[f"{tag}:burn_reconcile"] = s["burn_reconcile"]["ok"]
    bad = [k for k, v in gates.items() if not v]
    print("\n" + "=" * 74)
    print(f"  gates: {'ALL OK' if not bad else bad}")

    # 多臂通用判据：凡挂了 TDR 引擎（engine≠plain）的臂，都要比同资金 plain 改判低。
    plain_default = next((t for t in results if t == "plain" or t.startswith("plain@")), None)
    if len(results) >= 2 and plain_default:
        base = results[plain_default]["coord"]["no_qualified_local"]
        ht1 = True
        ht1_detail = {}
        for tag, s in results.items():
            if s["engine"] != "plain":
                ok = s["coord"]["no_qualified_local"] < base
                ht1 = ht1 and ok
                ht1_detail[tag] = {"relay": s["coord"]["no_qualified_local"],
                                   "relay_baseline": base,
                                   "events": s["tdr"]["events_opened"],
                                   "moved_eth": round(s["tdr"]["moved_wei"] / ETH, 1),
                                   "tdr_legs": s["legs"]["tdr_legs"],
                                   "ctx_legs": s["legs"]["ctx_legs"],
                                   "tdr_share_of_ctx_legs": s["legs"]["tdr_share_of_ctx_legs"],
                                   "verdict": ok}
        print(f"  H-T1 TDR 有效（全部 TDR 臂改判 < plain@{results[plain_default]['fund_eth']}）: {ht1}")
    else:
        ht1, ht1_detail = True, {}

    ht2 = all(gates.values())
    try:
        if len(results) >= 2:
            make_figure(out_root, results)
    except Exception:
        print("  (matplotlib 不可用或绘图失败，跳过)")
    (out_root / "summary.json").write_text(json.dumps({
        "params": to_params_dict(cfg, extra={"total_ctx": len(sel),
                                             "rss_peak_mb": round(watchdog.peak_mb, 0)}),
        "arms": list(results.values()),
        "gates": gates,
        "hypotheses": {"H-T1_tdr_effective": ht1_detail,
                       "H-T2_conservation_under_tdr": ht2},
    }, indent=2, default=str))
    print("=" * 74)
    return 0 if (ht2 and ht1) else 1


if __name__ == "__main__":
    sys.exit(main())
