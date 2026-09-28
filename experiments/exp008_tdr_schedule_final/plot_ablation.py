#!/usr/bin/env python3
"""exp008 · 调度策略消融图 —— 比原稿多出的一张图。

阶梯式消融：每个阶段在前一阶段上只加一个组件，全部读自真实产物。
  plain(不搬) → valve(搬运但目标均匀) → proportional(需求目标+全量重排)
  → topup(只补缺口) → topup+深死区 → topup+深死区+EWMA(定稿)
面板：(a) relay 数 (b) TDR 搬运次数 (c) 搬量 ETH (d) 死区 ε 灵敏度与悬崖。
数据源：本包 out/*/report.json（正式档各方案跨场中位）
        + exp003 out/*/summary.json 的矩阵档 topup 臂（面板 d 的 ε 曲线）。
用法：python plot_ablation.py          # 产物 figs/exp008_ablation.png
"""
import csv
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from brokerlab import plotting as P          # noqa: E402
import matplotlib.pyplot as plt              # noqa: E402

ETH = 10 ** 18

# 消融阶梯的固定顺序与每级新增组件（图例文字用英文，避免字体缺字）
LADDER = [
    ("plain@3.0",            "TDR_OFF"),
    ("valve@3.0@1.3",        "UNIFORM"),
    ("tdr@3.0@0.1",          "PROPORTIONAL"),
    ("topup@3.0@0.1",        "TOP_UP"),
    ("topup@3.0@0.95",       "TOP_UP + BAND"),
    ("topup@3.0@0.95@20.0",  "TDR_FINAL"),
]


def select_report(required_tags):
    """只选一份通过验收的 40k/120 报告，避免把不同场数或旧参数混成样本。"""
    candidates = []
    for rf in HERE.glob("out/*/report.json"):
        d = json.loads(rf.read_text())
        exp = d.get("params", {}).get("exp", {})
        arms = d.get("arms", {})
        scale = d.get("params", {}).get("scale", {})
        chain = d.get("params", {}).get("chain", {})
        if (d.get("ctx_total") == 40000 and exp.get("rate") == 120
                and exp.get("seed") == 42 and exp.get("balances_eth") == 3
                and chain.get("num_shards") == 16 and scale.get("num_brokers") == 50
                and set(required_tags) <= set(arms)
                and all(arms[t]["gates_all_true"] and arms[t]["burn_all_ok"]
                        for t in required_tags)):
            candidates.append((len(d.get("sessions", [])), rf.name, rf, d))
    if not candidates:
        return None, None
    _, _, path, report = max(candidates, key=lambda item: (item[0], str(item[2])))
    return path, report


def arm_medians(report, tag):
    a = report["arms"][tag]
    return {"relay": a["relay"]["median"],
            "transfers": a["transfers"]["median"],
            "moved": a["moved_eth"]["median"]}


def load_matrix_eps():
    """面板 d：exp003 矩阵档 topup(硬窗) 的 ε 曲线，含 ε≥1.0 悬崖样本。"""
    pts = {}
    for sf in sorted((HERE.parent / "exp003_tdr_on_off" / "out").glob("*/summary.json")):
        try:
            d = json.loads(sf.read_text())
        except Exception:
            continue
        if d["params"]["chain"]["num_shards"] != 16:
            continue
        if float(d["params"]["exp"].get("ctx_per_broker", 0)) != 200:
            continue
        if float(d["params"].get("_runtime", {}).get("total_ctx", 0)) != 10000:
            continue
        if float(d["params"]["exp"].get("tdr_ewma_half_life", 0)) != 20:
            continue   # 面板 d 的 ε 曲线统一取 EWMA(hl20) 矩阵档口径
        for a in d["arms"]:
            if a.get("engine") != "topup" or a.get("fund_eth") != 37.5:
                continue
            parts = a["arm"].split("@")
            if len(parts) < 3:
                continue
            try:
                eps = float(parts[2])
            except ValueError:
                continue
            pts[eps] = {"relay": a["relayed"],
                        "moved": a["tdr"]["moved_wei"] / ETH}
    return sorted(pts.items())




def dst8_trajectory(full_report_path):
    """第二张图：dst8 余额轨迹——对应原稿 fig5a 的概念（本平台规模版）。

    重建口径（近似，图上注明）：
      CTX 部分按各行到达序分 100 个 bin 累计（broker 路径 src=8 进 / dst=8 出；
      relay 不动 broker 账）。初值 150/分片。TDR 净流入按完成序号均摊进各 bin——
      tdr_moves.csv 无时间戳，均摊是诚实近似：展示量级与方向，非逐事件锯齿。
    """
    import csv
    import statistics
    N = 100
    INIT = 150.0

    def traj(armdir_glob, with_tdr):
        curves = []
        for files in sorted(glob.glob(armdir_glob)):
            if len(open(files).readlines()) < 2000:
                continue                      # 跳过冒烟小档（分片编号口径不同）
            arm_dir = files.rsplit("/", 1)[0]
            rows = list(csv.DictReader(open(files)))
            delta = defaultdict(lambda: [0.0] * N)
            for r in rows:
                if r["ok"] != "True" or r["route"] != "broker":
                    continue
                b = int(r["broker_idx"])
                bn = min(N - 1, int(float(r["arrival_pos"]) / 40000 * N))
                v = float(r["amount_wei"]) / ETH
                if int(r["src"]) == 8: delta[b][bn] += v
                if int(r["dst"]) == 8: delta[b][bn] -= v
            tdr_net = defaultdict(float)
            if with_tdr:
                try:
                    for m in csv.DictReader(open(arm_dir + "/tdr_moves.csv")):
                        if m["state"] != "done":
                            continue
                        a = float(m["amount_wei"]) / ETH
                        if int(m["dst_shard"]) == 8: tdr_net[int(m["broker_idx"])] += a
                        if int(m["src_shard"]) == 8: tdr_net[int(m["broker_idx"])] -= a
                except FileNotFoundError:
                    pass
            for b, dsteps in delta.items():
                c = [INIT]
                acc = INIT
                inflow = tdr_net.get(b, 0.0) / N
                for i in range(N):
                    acc += dsteps[i] + inflow
                    c.append(acc)
                curves.append(c[1:])
        if not curves:
            return None
        return [statistics.median([cv[i] for cv in curves]) for i in range(N)]

    (HERE / "figs").mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=140)
    xp = [(i + 1) / N for i in range(N)]
    t_plain = traj(str(full_report_path.parent / "session_*/*/plain@3.0/ctx_rows.csv"), False)
    fin_glob = str(full_report_path.parent / "session_*/*/topup@3.0@0.95@20.0/ctx_rows.csv")
    t_f_ctx = traj(fin_glob, False)
    t_f_all = traj(fin_glob, True)
    if not (t_plain and t_f_ctx and t_f_all):
        print("  !! 轨迹图缺输入，跳过"); return
    ax.plot(xp, t_plain, color=P.COLOR_GRAY, lw=1.6, label="TDR_OFF (CTX accounting)")
    ax.plot(xp, t_f_ctx, color=P.COLOR_RELAY, lw=1.2, ls="--",
            label="TDR_FINAL (CTX-only hypothetical)")
    ax.plot(xp, t_f_all, color=P.COLOR_BROKER, lw=1.8,
            label="TDR_FINAL (CTX + spread TDR inflow)")
    ax.axhline(0, color="k", lw=0.7, ls=":")
    ax.set_xlabel("Share of 40,000 Transactions Released")
    ax.set_ylabel("Reconstructed Net Funding Position on Shard 8 (ETH)")
    ax.set_title("Shard 8 Funding Illustration (not a balance trace)")
    ax.legend(fontsize=7, loc="upper left")
    ax.text(0.98, 0.03, "Below zero = hypothetical funding shortfall",
            transform=ax.transAxes, ha="right", fontsize=7)
    P.save(fig, HERE / "figs" / "exp008_dst8_trajectory.png")
    print(f"  figure: {HERE / 'figs' / 'exp008_dst8_trajectory.png'}")


def main() -> int:
    full_tags = [tag for tag, _ in LADDER if tag != "topup@3.0@0.95"]
    full_path, full_report = select_report(full_tags)
    extra_path, extra_report = select_report(["topup@3.0@0.95"])
    if not full_report or not extra_report:
        print("  !! 缺少通过验收的 40k/120 正式报告或深死区补充报告")
        return 1
    rows = [(label, arm_medians(extra_report if tag == "topup@3.0@0.95"
                                else full_report, tag)) for tag, label in LADDER]
    print(f"  data: main={full_path} | supplementary={extra_path}")
    eps_pts = load_matrix_eps()

    (HERE / "figs").mkdir(exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), dpi=140)
    ax_a, ax_b, ax_c, ax_d = axes.flat
    labels = [r[0] for r in rows]
    x = range(len(rows))
    hl = len(rows) - 1
    colors = [P.COLOR_GRAY if i == 0 else
              (P.COLOR_BROKER if i == hl else P.COLOR_ALT)
              for i in range(len(rows))]

    ax_a.bar(x, [r[1]["relay"] for r in rows], color=colors)
    ax_a.set_yscale("log")
    ax_a.set_xticks(list(x)); ax_a.set_xticklabels(labels, fontsize=8, rotation=18, ha="right")
    ax_a.set_ylabel("Transactions Using Relay (log scale)")
    ax_a.set_title("(a) Relay Fallbacks — Lower Is Better")
    for i, r in enumerate(rows):
        ax_a.text(i, r[1]["relay"] * 1.15, f"{r[1]['relay']:.0f}",
                  ha="center", fontsize=7)

    ax_b.bar(x, [r[1]["transfers"] for r in rows], color=colors)
    ax_b.set_yscale("symlog", linthresh=1)
    ax_b.set_xticks(list(x)); ax_b.set_xticklabels(labels, fontsize=8, rotation=18, ha="right")
    ax_b.set_ylabel("TDR Rebalancing Transfers (symlog scale)")
    ax_b.set_title("(b) Rebalancing Transactions — Lower Is Better")
    for i, r in enumerate(rows):
        ax_b.text(i, max(r[1]["transfers"], 1) * 1.3,
                  f"{r[1]['transfers']:.0f}", ha="center", fontsize=7)

    ax_c.bar(x, [r[1]["moved"] for r in rows], color=colors)
    ax_c.set_yscale("symlog", linthresh=1000)
    ax_c.set_xticks(list(x)); ax_c.set_xticklabels(labels, fontsize=8, rotation=18, ha="right")
    ax_c.set_ylabel("Funds Rebalanced (ETH; symlog scale)")
    ax_c.set_title("(c) Funds Moved — Lower Is Better")
    for i, r in enumerate(rows):
        ax_c.text(i, max(r[1]["moved"], 1000) * 1.3,
                  f"{r[1]['moved']:,.0f}", ha="center", fontsize=7)

    if eps_pts:
        ex = [e for e, _ in eps_pts]; ey = [p["relay"] for _, p in eps_pts]
        em = [max(p["moved"], 0.5) for _, p in eps_pts]
        line_relay, = ax_d.plot(ex, ey, "o-", color=P.COLOR_RELAY,
                                label="Relay Fallbacks")
        ax_d.set_yscale("log")
        ax_d.set_ylabel("Transactions Using Relay (log scale)")
        ax_d.set_xlabel("Deadband ε (10,000-CTX exploratory run)")
        ax2 = ax_d.twinx()
        line_moved, = ax2.plot(ex, em, "s--", color=P.COLOR_BROKER,
                               alpha=0.8, label="Funds Rebalanced")
        ax2.set_yscale("log"); ax2.set_ylabel("Funds Rebalanced (ETH; log scale)")
        ax_d.axvline(1.0, color="k", lw=0.8, ls=":")
        ax_d.text(0.995, ax_d.get_ylim()[1] * 0.5, " cliff: ε→1",
                  fontsize=7, ha="right")
        ax_d.set_title("(d) Deadband Sensitivity (Separate 10,000-CTX Data)")
        ax_d.legend([line_relay, line_moved], ["Relay Fallbacks", "Funds Rebalanced"],
                    fontsize=8, loc="lower left")
        ax_d.tick_params(labelsize=7)

    P.save(fig, HERE / "figs" / "exp008_ablation.png")
    print(f"  figure: {HERE / 'figs' / 'exp008_ablation.png'}")
    # 新 trace 没有固定 dst8 热点；余额图应直接读取 balance_snapshots.csv，
    # 不再生成旧版的 shard-8 假想资金缺口重构图。
    return 0


if __name__ == "__main__":
    sys.exit(main())
