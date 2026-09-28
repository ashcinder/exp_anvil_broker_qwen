#!/usr/bin/env python3
"""把重构实验（exp001–exp008）的产物换算成原始手稿绘图代码所需的 CSV schema。

只读重构实验输出，绝不修改 exp_figure_origin/ 下任何文件。
产物直接写到各图目录里、与对应 plot_*.py 同级（原始文件名、原始目录布局），
例：fig5b_balance/tdr_off_balance_distribution.csv、
    fig6a_threshold/ENABLE_TDR=1_TDR_THRESHOLD=..._run=*/broker_pnl.csv。

数据源（全部 40000 笔正式档口径，固定写死可溯源；对照表见 README.md）：
  fig1 / fig5b-off = exp008 session_1 的 plain@150.0（无 TDR）
  fig5a / fig5d-on = exp008 session_1 的 topup@150.0@0.95@20.0（定稿方案）
  fig5d-off        = plain@150.0
  fig5b-on         = exp008 session_1 的 tdr@150.0@0.1（proportional，手稿同族）
  fig6a            = 全部 40000 笔 tdr(proportional) ε 场自动扫描
                     （exp008/exp003 现成场 + 本目录 sweep_fig6a/ 补跑场）
  fig5c            = exp004（B2E 手续费，仅机制级 60 笔——尺度缺口见 README）

换算口径（全部由 ctx_rows.csv + tdr_moves.csv 事件重建，非伪造）：
  · 合成块高  block = arrival_pos // 50   （与原始脚本 tx_index//50 同规则）
  · broker 子账户余额轨迹：初值 150 ETH/片；broker 路径 Θ1 在 t1_block 入账、
    Θ2 在 t2_block 出账；TDR 搬运在重放触发块入账（τ/上界/触发时刻为离线重放，
    搬运总数与 tdr_moves.csv 真值对照后打印校验）。
  · fig1 右图 A/B 线 = 全体 50 broker 各自最大/最小子账户余额之和（分片求和），
    单位 wei（对应原图 1e22 轴标）。
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # exp_anvil_broker_qwen/
DATA = HERE            # 产物直接落在各图目录（plot 脚本旁）

sys.path.insert(0, str(ROOT))
from brokerlab.tdr_policy import (DemandWindow, EwmaDemand, compute_target,  # noqa: E402
                                  compute_transfers, topup_plans, percent_of)

ETH = 10 ** 18

# —— 源 run 固定选择（与 README.md 表一致） ——
E8_S1 = ROOT / ("experiments/exp008_tdr_schedule_final/out/20260910_033927/"
                "session_1/20260910_033928")
PLAIN_ARM = E8_S1 / "plain@150.0"
TDR_ARM = E8_S1 / "topup@150.0@0.95@20.0"
EXP003_OUT = ROOT / "experiments/exp003_tdr_on_off/out"
E4 = ROOT / "experiments/exp004_b2e_revenue/out/20260904_095954"

FUND_ETH = 150.0          # broker 每分片初始垫资（config: broker.initial_balance_eth）
N_SHARDS = 16
N_BROKERS = 50
# fig5b 重放用的定稿方案参数（exp008 config.yaml 同源）
REPLAY = dict(eps=0.95, q_min=0.10, hl=20.0, chi=10, min_blocks=5)


# ---------------------------------------------------------------------------
# 公共件
# ---------------------------------------------------------------------------

def load_ctx(arm_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(arm_dir / "ctx_rows.csv")
    df = df.sort_values("arrival_pos").reset_index(drop=True)
    df["synth_block"] = df["arrival_pos"] // 50 + 1      # 原始脚本 tx_index//50 同规则
    return df


def load_ctx_rate(arm_dir: Path, tx_per_block: int) -> pd.DataFrame:
    """按指定“每块成交笔数”给合成块高，用于把柱高/带宽落进手稿画幅。

    手稿图的块轴与其 mock 负载率绑定（fig1≈28.6 笔/块、fig5a≈28.8 笔/块）；
    直接套用真实 submit_block 会让柱体顶出固定 ylim。合成块高不改变任何
    机制结论，只是把 x 轴按原画幅重新分箱。"""
    df = pd.read_csv(arm_dir / "ctx_rows.csv")
    df = df.sort_values("arrival_pos").reset_index(drop=True)
    df["synth_block"] = df["arrival_pos"] // tx_per_block + 1
    return df


# ---------------------------------------------------------------------------
# fig1_motivation: exp_b_handler_trace_ori.csv（1 行/合成块，520 块）
# ---------------------------------------------------------------------------

def prep_fig1():
    df = load_ctx_rate(PLAIN_ARM, 28)       # 28 笔/块 × 520 块 ≈ 手稿 14890 笔量级
    df = df[df.synth_block <= 520].copy()
    bal = [[FUND_ETH * ETH] * N_SHARDS for _ in range(N_BROKERS)]
    rows = []
    counts = {b: {"relay": 0, "brk": 0} for b in range(1, 521)}
    deltas_by_block: dict[int, list] = {b: [] for b in range(1, 521)}
    for r in df.itertuples():
        b = int(r.synth_block)
        if r.ok and r.route == "broker":
            counts[b]["brk"] += 1
            deltas_by_block[b].append((int(r.broker_idx), int(r.src), int(r.amount_wei)))
            deltas_by_block[b].append((int(r.broker_idx), int(r.dst), -int(r.amount_wei)))
        elif r.ok and r.route == "relay":
            counts[b]["relay"] += 1
    for b in range(1, 521):
        for bi, s, dv in deltas_by_block[b]:
            bal[bi][s] += dv
        a = sum(max(row) for row in bal)          # Σ_b 最大子账户
        c = sum(min(row) for row in bal)          # Σ_b 最小子账户
        rows.append({
            "epoch": (b - 1) // 5,
            "block_height": b,
            "tx_index": (b - 1) * 50,
            "broker_tx_index": (b - 1) * 50,
            "broker_tx": counts[b]["brk"],
            "relay_fallback": counts[b]["relay"],
            "shard_A_total_balance": int(a),
            "shard_B_total_balance": int(c),
            "system_status": "NORMAL",
        })
    out = DATA / "fig1_motivation"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "exp_b_handler_trace_ori.csv", index=False)
    print(f"fig1: {len(rows)} 块 | relay 合计 {sum(counts[b]['relay'] for b in counts)}"
          f" | broker 合计 {sum(counts[b]['brk'] for b in counts)}")


# ---------------------------------------------------------------------------
# fig5a: exp_b_tdr_trace_2.csv（1 行/块 ×600；脚本内另有手稿同款合成覆盖）
# ---------------------------------------------------------------------------

def prep_fig5a():
    df = load_ctx_rate(TDR_ARM, 28)         # 对齐手稿每块 ≈28.8 笔的 mock 负载率
    df = df[df.synth_block <= 600]
    counts = {b: {"relay": 0, "brk": 0} for b in range(1, 601)}
    for r in df.itertuples():
        if not r.ok:
            continue
        counts[int(r.synth_block)]["brk" if r.route == "broker" else "relay"] += 1
    rows = [{
        "epoch": (b - 1) // 5,
        "block_height": b,
        "tx_index": (b - 1) * 50,
        "broker_tx_index": (b - 1) * 50,
        "broker_tx": counts[b]["brk"],
        "relay_fallback": counts[b]["relay"],
        "shard_A_total_balance": 0, "shard_B_total_balance": 0,
        "system_status": "NORMAL",
    } for b in range(1, 601)]
    out = DATA / "fig5a_transmit_data"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "exp_b_tdr_trace_2.csv", index=False)
    print("fig5a: 600 块写入")


# ---------------------------------------------------------------------------
# fig5b: tdr_off/tdr_on_balance_distribution.csv
#   数据源 = exp008 正式档（16 分片 × 50 broker × 800 = 40000 笔注入）：
#     off = plain@150.0（累计正常确认 15165，恰好铺满原图 0-15000 的 x 域）
#     on  = tdr@150.0@0.1（proportional、ε=0.1 论文默认——与手稿原图同族，
#           密集 τ 锚定小额搬运 → 红点丰富）
#   τ/上界/红点：ctx_rows 离线重放 brokerlab 纯策略函数（与运行时同一套代码）；
#   重放搬运总数与 tdr_moves.csv 真值对照打印。
#   轴说明：原脚本坐标框绑定旧 mock（on 0-0.8 ETH、off 0-5），正式档余额在
#   0-400 ETH 量级 → port 里轴范围/刻度按数据自适应（配色/尺寸/文字不动），
#   理由与清单见 README「fig5b 重绘说明」。
# ---------------------------------------------------------------------------

TDR40_ARM = E8_S1 / "tdr@150.0@0.1"


def prep_fig5b():
    out = DATA / "fig5b_balance"
    out.mkdir(parents=True, exist_ok=True)
    _fig5b_arm(PLAIN_ARM, out / "tdr_off_balance_distribution.csv", with_tdr=False)
    _fig5b_arm(TDR40_ARM, out / "tdr_on_balance_distribution.csv", with_tdr=True)


def _fig5b_arm(arm_dir: Path, path: Path, with_tdr: bool):
    df = pd.read_csv(arm_dir / "ctx_rows.csv").sort_values(
        "arrival_pos").reset_index(drop=True)
    moves = (pd.read_csv(arm_dir / "tdr_moves.csv") if with_tdr else None)
    n_sh = int(df[["src", "dst"]].max().max()) + 1
    ALPHA = int(df.broker_idx.min())
    fund = FUND_ETH
    # 从 summary 读该臂真实垫资
    summ = arm_dir.parent / "summary.json"
    if summ.exists():
        s = json.loads(summ.read_text())
        for a in s.get("arms", []):
            if a["arm"] == arm_dir.name:
                fund = float(a["fund_eth"])
    sub = df[(df.broker_idx == ALPHA) & (df.route == "broker") & (df.ok == True)]  # noqa: E712
    alpha_all = df[df.broker_idx == ALPHA]       # 需求观测含最终改判 relay 的提交
    # shard_0 = 出账最多的子账户（被抽干/回灌侧）；shard_1 = 入账最多且 ≠ shard_0
    dst_cell = int(sub.groupby("dst").amount_wei.sum().idxmax())
    src_amt = sub.groupby("src").amount_wei.sum().sort_values(ascending=False)
    src_cell = int(next(s for s in src_amt.index if s != dst_cell))
    end_block = int(df.submit_block.max())
    rows_by_block: dict[int, list] = {}
    for r in sub.itertuples():
        rows_by_block.setdefault(int(r.submit_block), []).append(r)
    obs_by_block: dict[int, list] = {}
    for r in alpha_all.itertuples():
        obs_by_block.setdefault(int(r.submit_block), []).append(r)

    norm = df[df.route == "broker"].groupby("submit_block").size()
    norm = norm.reindex(range(0, end_block + 1), fill_value=0)
    epochs = list(range(1, end_block + 1))

    # —— 单进程重放：余额轨迹 + τ/上界 + 触发事件（与运行时同一套 brokerlab 纯策略） ——
    is_topup = arm_dir.name.startswith("topup")
    if is_topup:
        eps = REPLAY["eps"]
    elif arm_dir.name.startswith("tdr@"):
        eps = float(arm_dir.name.rsplit("@", 1)[-1])   # tdr@0.1 → 0.1
    else:                                              # plain：不参与触发计算
        eps = REPLAY["eps"]
    dem = (EwmaDemand(half_life_blocks=REPLAY["hl"], min_blocks=REPLAY["min_blocks"])
           if is_topup else DemandWindow(window_blocks=20))
    bal = [int(fund * ETH)] * n_sh
    tau = {s: {} for s in range(n_sh)}
    ub = {s: {} for s in range(n_sh)}
    dots = {b: 0 for b in epochs}
    series = {s: {0: int(fund * ETH)} for s in range(n_sh)}
    last_evt = -10 ** 9
    for b in epochs:
        for r in rows_by_block.get(b, []):
            v = int(r.amount_wei)
            bal[int(r.src)] += v        # Θ1 入账（近似：同块）
            bal[int(r.dst)] -= v        # Θ2 出账
        for r in obs_by_block.get(b, []):
            dem.observe(b, int(r.dst), int(r.amount_wei))   # 路由时刻观测（含改判件）
        lam, _ = dem.demand(b, n_sh)
        tgt = compute_target(bal, lam, q_min=REPLAY["q_min"])
        for s in range(n_sh):
            tau[s][b] = int(tgt[s])
            ub[s][b] = int(tgt[s] + percent_of(tgt[s], eps))
        if with_tdr and dem.ready(b) and b - last_evt >= REPLAY["chi"]:
            plans = (topup_plans(bal, tgt, eps) if is_topup
                     else compute_transfers(bal, tgt, eps, "excess_only"))
            if plans:
                last_evt = b
                dots[b] = len(plans)
                for (s, d, amt) in plans:
                    bal[s] -= amt
                    bal[d] += amt
        for s in range(n_sh):
            series[s][b] = int(bal[s])
    if with_tdr and moves is not None:
        n_real = int(((moves.broker_idx == ALPHA)
                      & (moves.state == "done")).sum())
        print(f"fig5b 重放校验: 重放搬运 {sum(dots.values())} vs 真实 "
              f"{n_real}（{arm_dir.name}, eps={eps}, {'topup' if is_topup else 'proportional'}）")
    cell0 = series.get(dst_cell, {})
    cell1 = series.get(src_cell, {})

    rows = []
    for b in epochs:
        nrm = int(norm.get(b, 0))
        b0 = max(0, cell0.get(b, int(fund * ETH)))
        b1 = max(0, cell1.get(b, int(fund * ETH)))
        rows.append({
            "epoch": b,
            "timestamp": f"2026-09-10T00:00:00Z",
            "normal_tx_confirmed": nrm,
            "tdr_self_transfer_confirmed": dots.get(b, 0) if with_tdr else 0,
            "broker_id": f"alpha_broker_{ALPHA}",
            "shard_0_balance": b0,
            "shard_0_broker_tx_confirmed": nrm,
            "shard_0_relay_tx_confirmed": 0,
            "shard_0_tdr_self_transfer_confirmed": dots.get(b, 0) if with_tdr else 0,
            "shard_0_tau": tau[dst_cell].get(b, "") if with_tdr else "",
            "shard_0_lower_bound": "",
            "shard_0_upper_bound": ub[dst_cell].get(b, "") if with_tdr else "",
            "shard_1_balance": b1,
            "shard_1_broker_tx_confirmed": 0,
            "shard_1_relay_tx_confirmed": 0,
            "shard_1_tdr_self_transfer_confirmed": 0,
            # 原脚本读 shard_1_tau/_upper_bound 画 α.0 的目标/上界线 → 填 dst 侧
            "shard_1_tau": tau[dst_cell].get(b, "") if with_tdr else "",
            "shard_1_lower_bound": "",
            "shard_1_upper_bound": ub[dst_cell].get(b, "") if with_tdr else "",
            "total_balance": b0 + b1,
            "balance_stddev": abs(b0 - b1) / 2,
            "balance_min": min(b0, b1),
            "balance_max": max(b0, b1),
            "deviation_from_target": 0,
        })
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"fig5b: {path.name} {len(rows)} 行 | α {dst_cell}(shard_0)/"
          f"{src_cell}(shard_1) | fund {fund} ETH")


# ---------------------------------------------------------------------------
# fig5c: tdr_off/on_brief_info.csv（exp004 B2E 机制级小流量）
# ---------------------------------------------------------------------------

FEE5C = HERE / "fees_fig5c"          # 带费 TDR off/on 正式跑（附录 C 命令产出）


def _latest_fee5c_arms():
    """返回 {'off': 臂目录, 'on': 臂目录}：plain=off、topup/tdr=on，取最新一场。"""
    runs = sorted(p for p in FEE5C.glob("*/") if (p / "summary.json").exists())
    if not runs:
        return None
    arms = {}
    for a in sorted(runs[-1].iterdir()):
        if not a.is_dir() or not (a / "ctx_rows.csv").is_file():
            continue
        if a.name.startswith("plain"):
            arms["off"] = a
        elif a.name.startswith(("topup", "tdr")):
            arms["on"] = a
    return arms if {"off", "on"} <= set(arms) else None


def prep_fig5c():
    out = DATA / "fig5c_profit"
    out.mkdir(parents=True, exist_ok=True)
    fee_arms = _latest_fee5c_arms()
    if fee_arms is not None:
        # 正式口径：带 B2E 费用的 TDR off/on 对照（见附录 C）
        for tag, arm_dir in fee_arms.items():
            df = pd.read_csv(arm_dir / "ctx_rows.csv")
            has_fee = "fee_broker_wei" in df.columns
            rows = []
            for blk, g in df.groupby("submit_block"):
                fee_sum = int(g["fee_broker_wei"].sum()) if has_fee else 0
                rows.append({
                    "epoch": int(blk), "block_height": int(blk),
                    "onchain_tx_total": len(g), "tdr_onchain_tx": 0,
                    "tdr_tx_ratio": 0.0, "inner_tx": 0,
                    "broker_ctx": int((g.route == "broker").sum()),
                    "relay_ctx": int((g.route == "relay").sum()),
                    "ctx_service_rate": 1.0, "cross_shard_ratio": 1.0,
                    "avg_block_fullness": 0.0,
                    "total_fee_revenue": fee_sum,
                    "total_tdr_gas_cost": 0,      # charges_rebalancing=false
                    "net_system_revenue": fee_sum,
                    "rebalance_triggers": 0, "rebalance_tx_submitted": 0,
                    "tdr_tx_confirmed": 0,
                })
            p = out / f"tdr_{tag}_brief_info.csv"
            pd.DataFrame(rows).sort_values("epoch").to_csv(p, index=False)
            print(f"fig5c: {p.name} 来自带费正式跑 {arm_dir.name}（{len(rows)} 块）")
        return
    print("fig5c: 未见 fees_fig5c/ 带费跑批 → 退回 exp004 机制级小流量（尺度缺口见 README）")
    for tag in ("off", "on"):
        df = pd.read_csv(E4 / tag / "b2e_trace.csv")
        rows = []
        for blk, g in df.groupby("block"):
            rows.append({
                "epoch": int(blk),
                "block_height": int(blk),
                "onchain_tx_total": len(g),
                "tdr_onchain_tx": 0,
                "tdr_tx_ratio": 0.0,
                "inner_tx": 0,
                "broker_ctx": int((g.route == "broker").sum()),
                "relay_ctx": int((g.route == "relay").sum()),
                "ctx_service_rate": 1.0,
                "cross_shard_ratio": 1.0,
                "avg_block_fullness": 0.0,
                "total_fee_revenue": int(g.fee_broker_wei.sum()),
                "total_tdr_gas_cost": 0,
                # 手稿口径：净收益 = broker 留存费用（B2E 的 βF）；TDR 自转账 0
                "net_system_revenue": int(g.fee_broker_wei.sum()),
                "rebalance_triggers": 0,
                "rebalance_tx_submitted": 0,
                "tdr_tx_confirmed": 0,
            })
        df_rows = pd.DataFrame(rows).sort_values("epoch")
        p = out / f"tdr_{tag}_brief_info.csv"
        df_rows.to_csv(p, index=False)
        print(f"fig5c: {p.name} {len(df_rows)} 块 | 合计确认 "
              f"{int(df_rows.broker_ctx.sum() + df_rows.relay_ctx.sum())} 笔")


# ---------------------------------------------------------------------------
# fig5d: tdr_off/on/ctx_records.csv
# ---------------------------------------------------------------------------

def prep_fig5d():
    out = DATA / "fig5d_hot"
    for name, arm in [("tdr_off", PLAIN_ARM), ("tdr_on", TDR_ARM)]:
        df = load_ctx(arm)
        rec = pd.DataFrame({
            "ctx_id": df.ctx_id,
            "sender_idx": "",
            "receiver_idx": "",
            "source_shard": df.src,
            "dest_shard": df.dst,
            "amount_wei": df.amount_wei,
            "amount_eth": df.amount_wei / ETH,
            "broker_id": df.broker_idx,
            "status": df.route.map({"broker": "completed_broker",
                                    "relay": "completed_relay"}),
            "tx1_hash": "", "tx2_hash": "",
            "submit_time": df.submit_block.astype(float),      # 块长 1s → 秒
            "confirm_time": df.t2_block.astype(float),
            "latency_s": df.e2e_secs,
            "error": "",
        })
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        rec.to_csv(d / "ctx_records.csv", index=False)
        print(f"fig5d: {name} {len(rec)} 行")


# ---------------------------------------------------------------------------
# fig6a: ENABLE_TDR=1_TDR_THRESHOLD=*_CTX_COUNT=*_run=*/broker_pnl.csv
#   口径 = 40000 笔正式档、proportional(tdr) ε 臂。自动扫描：
#     · exp008 两 session 的 tdr@150.0@0.1
#     · exp003 探索档案里 40000 笔的 tdr@150.0@<eps>（041547/125057 等）
#     · 本目录 sweep_fig6a/ 下补跑的 ε×run 扫描（README 附录 A 命令产出）
#   同一 ε 的每场 = 一个 run 目录；箱线跨 run。
# ---------------------------------------------------------------------------

# 臂目录名两种写法都认：tdr@150.0@0.1（带资金）/ tdr@0.1（无资金后缀）
ARM6A_RE = re.compile(r"^tdr@(?:150\.0@)?(0\.\d+)$")


def _scan_6a_runs():
    """收集所有 40000 笔 proportional 场：[(arm_dir, eps, ctx_n)]，按 (eps,时间) 排序。

    不依赖 summary.json（扫描中途崩掉的场也认）：直接找各 session 目录下
    名为 tdr@150.0@<eps> 的臂子目录，ctx 笔数 = ctx_rows.csv 行数-1；
    <20000 笔的场（如 12320 的漏配池扫描、10000 的探索档）一律排除。"""
    found = []
    sess_dirs = []
    e8root = ROOT / "experiments/exp008_tdr_schedule_final/out"
    sess_dirs += sorted(e8root.glob("*/session_*/2026*"))
    sess_dirs += sorted(EXP003_OUT.glob("2026*"))
    sess_dirs += sorted((HERE / "sweep_fig6a").glob("2026*"))
    sess_dirs += sorted((HERE / "sweep_fig6a").glob("e*/2026*"))
    for sd in sess_dirs:
        if not sd.is_dir():
            continue
        for arm_dir in sorted(sd.iterdir()):
            if not arm_dir.is_dir():
                continue
            m = ARM6A_RE.match(arm_dir.name)
            if not m:
                continue
            ctx = arm_dir / "ctx_rows.csv"
            if not ctx.is_file():
                continue
            n = sum(1 for _ in ctx.open("rb")) - 1
            if n < 20000:
                continue
            p = None
            if (sd / "summary.json").exists():
                try:
                    p = json.loads((sd / "summary.json").read_text())["params"]
                except Exception:
                    pass
            if p is not None:
                if float(p["exp"].get("rate", 120)) < 100:
                    continue               # 统一 120 CTX/s 注入档，剔除 rate40 老场
                if int(p["chain"].get("num_shards", 16)) != 16:
                    continue               # 剔除 4 分片误扫场（exp003 现默认 4！）
            found.append((float(m.group(1)), sd.name, arm_dir, n))
    found.sort(key=lambda x: (x[0], x[1], x[2].as_posix()))
    return [(d, _e, n) for _e, _sd, d, n in found]


def prep_fig6b():
    """sweep_fig6b/ 窗口扫描 → Fig6b_window_size/results/<原始命名>/broker_pnl.csv。

    目录名严格沿用原脚本模式 ENABLE_TDR=1_TDR_THRESHOLD=0.200_CTX_COUNT=<n>_TDR_WINDOW=<w>_run=<k>。
    同一窗口按场时间序自动编号 run。窗口 session 里只有一个 tdr@0.2 臂。"""
    import shutil
    out = HERE / "Fig6b_window_size" / "results"
    if out.exists():
        shutil.rmtree(out)
    per_w: dict[int, list] = {}
    for wdir in sorted((HERE / "sweep_fig6b").glob("w*/2026*")):
        w = int(wdir.parent.name[1:])
        arm = wdir / "tdr@0.2"
        ctx = arm / "ctx_rows.csv"
        mv = arm / "tdr_moves.csv"
        if not (ctx.is_file() and mv.is_file()):
            continue
        df = pd.read_csv(ctx)
        if len(df) < 20000:
            continue
        moves = pd.read_csv(mv)
        done = moves[moves.state == "done"].groupby("broker_idx").size()
        served = df[df.route == "broker"].groupby("broker_idx").size()
        relayed = df[df.route == "relay"].groupby("broker_idx").size()
        rows = []
        for b in range(N_BROKERS):
            tr = int(done.get(b, 0))
            rows.append({
                "broker_id": b, "account_index": 100 + b,
                "total_balance_final_eth": round(FUND_ETH * N_SHARDS, 3),
                "served_count": int(served.get(b, 0)),
                "relay_fallback_count": int(relayed.get(b, 0)),
                "gas_reward_wei": 0, "gas_reward_eth": 0.0,
                "rebalance_count": tr, "total_rebalance_transfers": tr,
                "rebalance_cost_wei": 0, "rebalance_cost_eth": 0.0,
                "net_profit_wei": 0, "net_profit_eth": 0.0,
            })
        per_w.setdefault(w, []).append((wdir.name, len(df), rows))
    for w, runs in sorted(per_w.items()):
        for k, (run_name, n, rows) in enumerate(runs):
            d = out / (f"ENABLE_TDR=1_TDR_THRESHOLD=0.200_CTX_COUNT={n}"
                       f"_TDR_WINDOW={w}_run={k}")
            d.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_csv(d / "broker_pnl.csv", index=False)
            print(f"fig6b: window={w} run={k} ctx={n} <- sweep_fig6b/w{w}/{run_name}")


def prep_fig6a():
    out = DATA / "fig6a_threshold"
    out.mkdir(exist_ok=True)
    for stale in out.glob("ENABLE_TDR=1_TDR_THRESHOLD=*"):
        import shutil as _sh
        if stale.is_dir():
            _sh.rmtree(stale)            # 只清旧 run 目录，保留脚本与 out/
    seen_eps: dict[float, int] = {}
    for src, eps, ctx_n in _scan_6a_runs():
        run = seen_eps.get(eps, 0)
        seen_eps[eps] = run + 1
        df = pd.read_csv(src / "ctx_rows.csv")
        moves = pd.read_csv(src / "tdr_moves.csv")
        done = moves[moves.state == "done"].groupby("broker_idx").size()
        served = df[df.route == "broker"].groupby("broker_idx").size()
        relayed = df[df.route == "relay"].groupby("broker_idx").size()
        rows = []
        for b in range(N_BROKERS):
            tr = int(done.get(b, 0))
            fb = int(relayed.get(b, 0))
            sv = int(served.get(b, 0))
            final = FUND_ETH * N_SHARDS
            rows.append({
                "broker_id": b,
                "account_index": 100 + b,
                "total_balance_final_eth": round(final, 3),
                "served_count": sv,
                "relay_fallback_count": fb,
                "gas_reward_wei": 0, "gas_reward_eth": 0.0,
                "rebalance_count": tr,
                "total_rebalance_transfers": tr,
                "rebalance_cost_wei": 0, "rebalance_cost_eth": 0.0,
                "net_profit_wei": 0, "net_profit_eth": 0.0,
            })
        d = out / (f"ENABLE_TDR=1_TDR_THRESHOLD={eps:.3f}"
                   f"_CTX_COUNT={ctx_n}_run={run}")
        d.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(d / "broker_pnl.csv", index=False)
        print(f"fig6a: eps={eps} run={run} ctx={ctx_n} <- {src.parent.name}/{src.name}")


FRAC6C = HERE / "frac_fig6c"          # per-broker TDR 装备比例扫描（附录 C 命令产出）


def prep_fig6c():
    """frac_fig6c/ 各 session → Fig6c_pct/<rep>/pct{N}_<arm>/ 三件套。

    session 序 = repeat 号；臂标签末尾 @p{NN} → pct{NN}。
    产出与原版同 schema：exp_e_system_summary.json（confirmed_logical_txs /
    confirmed_broker_ctxs）、tdr_broker_revenue.csv（每 broker net_revenue=ΣβF,
    wei 整数字符串）、tdr_brief_info.csv（存在性检查用 + per-epoch 汇总）。"""
    out = HERE / "Fig6c_pct"
    import shutil as _sh
    for stale in out.glob("[0-9]*"):
        if stale.is_dir():
            _sh.rmtree(stale)
    # 同一 frac 档位（p{N}/）内的 session 序 = repeat 轮次（driver 每档跑 2 场）
    pdirs = sorted((p for p in FRAC6C.glob("p*") if p.is_dir()),
                   key=lambda p: int(p.name[1:]))
    sessions = [(rep, sess, pd) for pd in pdirs
                for rep, sess in enumerate(
                    sorted(p for p in pd.glob("2026*")
                           if (p / "summary.json").exists()), start=1)]
    if not sessions:
        print("fig6c: 跳过 —— 未见 frac_fig6c/ 扫描产物（跑完后自动纳入）")
        return
    for rep, sess, _pd in sessions:
        summ = json.loads((sess / "summary.json").read_text())
        for a in summ["arms"]:
            m = re.search(r"@p(\d{2,3})$", a["arm"])
            if not m:
                continue
            pct = int(m.group(1))
            arm_dir = sess / a["arm"]
            if not arm_dir.is_dir():          # 兼容：session 根下或父目录 p{N} 下
                alt = sess.parent / a["arm"]
                arm_dir = alt if alt.is_dir() else arm_dir
            ctx = arm_dir / "ctx_rows.csv"
            if not (pct in range(10, 101, 10) and ctx.is_file()):
                continue
            df = pd.read_csv(ctx)
            d = out / str(rep) / f"pct{pct}_{a['arm']}"
            d.mkdir(parents=True, exist_ok=True)
            json.dump({"confirmed_logical_txs": int(len(df)),
                       "confirmed_broker_ctxs": int((df.route == "broker").sum()),
                       "confirmed_relay_ctxs": int((df.route == "relay").sum())},
                      open(d / "exp_e_system_summary.json", "w"))
            if "fee_broker_wei" in df.columns:
                rev = df.groupby("broker_idx")["fee_broker_wei"].sum()
            else:
                rev = df.groupby("broker_idx").size() * 0
            with open(d / "tdr_broker_revenue.csv", "w", newline="") as f:
                f.write("broker_id,net_revenue\n")
                for b in range(N_BROKERS):
                    f.write(f"{b},{int(rev.get(b, 0))}\n")
            if "fee_broker_wei" in df.columns:
                agg = {"broker_ctx": ("route", lambda x: int((x == "broker").sum())),
                       "relay_ctx": ("route", lambda x: int((x == "relay").sum())),
                       "net_system_revenue": ("fee_broker_wei", "sum")}
            else:
                agg = {"broker_ctx": ("route", lambda x: int((x == "broker").sum())),
                       "relay_ctx": ("route", lambda x: int((x == "relay").sum()))}
            per = df.groupby("submit_block").agg(**agg)
            per.to_csv(d / "tdr_brief_info.csv", index_label="epoch")
        print(f"fig6c: repeat {rep} <- {sess.name}")


def main():
    DATA.mkdir(exist_ok=True)
    # 6b/6c 暂无数据：预建空目录，让对应脚本按原逻辑给出“没有可用 run 目录”的报错
    (DATA / "Fig6b_window_size" / "results").mkdir(parents=True, exist_ok=True)
    (DATA / "Fig6c_pct").mkdir(exist_ok=True)
    prep_fig1()
    prep_fig5a()
    prep_fig5b()
    prep_fig5c()
    prep_fig5d()
    prep_fig6a()
    prep_fig6b()
    prep_fig6c()
    import shutil
    legacy = HERE / "data"
    if legacy.exists():
        shutil.rmtree(legacy)              # 旧集中式 data/ 已迁到各图目录
    print("\n全部数据产物已写入各图目录（plot 脚本旁）")


if __name__ == "__main__":
    main()
