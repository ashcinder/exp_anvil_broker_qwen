"""exp033 出图/出表：SOTA 对照的 Pareto 图（relay × 搬运次数）+ 盈亏平衡射线簇。

口径与 SOTA_COMPARISON_DESIGN.md §1 一致：
  横轴 x = relay CTX 数（越低越好）
  纵轴 y = 再平衡搬运次数（越低越好）
  参考点 = plain，记 (x0, 0)；经济阈值 ρ* = y/(x0−x)（该方案所需的最小 κ/μ）
  盈亏平衡射线：y = (κ/μ)·(x0−x)，过 plain 点，线以上为亏损区

用法（runner 会自动带上这三个参数）：
  python plot_pareto.py --experiment-source <run/> --output <run/figs>
"""
import argparse
import csv
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from brokerlab.plotting import COLOR_BROKER, COLOR_GRAY, save   # noqa: E402

# 展示顺序 = 论文里的叙述顺序：先基线，再各 SOTA 臂，最后本方案。
ORDER = ["plain", "pnps", "spider", "flexichain", "autoloop", "ucrb",
         "overpass_u", "overpass_d", "loopmax", "topup"]
LABEL = {"plain": "plain (no rebalancing)", "topup": "TDR (ours)",
         "flexichain": "Flexichain (TOPS'24)", "loopmax": "Loopmax (ePrint'22)",
         "spider": "Spider-vol (NSDI'20)", "pnps": "PnP-static (INFOCOM'20)",
         "autoloop": "Autoloop (ePrint'22)", "overpass_u": "Overpass-QP θ=1/n",
         "overpass_d": "Overpass-QP θ∝λ", "ucrb": "UCRB (ePrint'25)"}
RAYS = (0.05, 0.10, 0.25, 0.50, 1.00)


def arm_of(row):
    """本实验每行的 sweep 恒为 "figures"，臂名在 value 列（token 去掉 @ 后缀）。
    exp025 那种参数扫描档的 sweep 才是维度名——不属本脚本口径。"""
    if row["sweep"] != "figures":
        raise SystemExit(f"非本实验口径的行（sweep={row['sweep']}），拒绝出图")
    return row["value"].split("@")[0]


def load(sources):
    """可合并多次运行的 raw_results.csv：同一负载/种子/配置下分批跑出的臂
    必须能画在同一张图上（每次运行自己起链，臂之间本来就相互独立）。"""
    rows = []
    for source in sources:
        with (Path(source) / "raw_results.csv").open(encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                name = arm_of(row)
                rows.append({"arm": name, "relay": int(row["relayed"]),
                             "transfers": int(row["transfers"]),
                             "moved_eth": float(row["moved_eth"]),
                             "served": int(row["served"]), "n": int(row["n"]),
                             "round": int(row["round"]), "source": str(source)})
    if not rows:
        raise SystemExit("raw_results.csv 为空：先跑正式档")
    # 同一 (臂, 场次) 出现两次 ⇒ 两次运行的种子/负载不同，合并会污染中位数。
    keys = [(r["arm"], r["round"]) for r in rows]
    duplicated = {k for k in keys if keys.count(k) > 1}
    if duplicated:
        raise SystemExit(f"重复的臂/场次（不可合并）：{sorted(duplicated)}")
    return rows


def aggregate(rows):
    out = {}
    for row in rows:
        out.setdefault(row["arm"], []).append(row)
    summary = {}
    for arm, group in out.items():
        summary[arm] = {
            "relay": st.median(r["relay"] for r in group),
            "relay_lo": min(r["relay"] for r in group),
            "relay_hi": max(r["relay"] for r in group),
            "transfers": st.median(r["transfers"] for r in group),
            "moved_eth": st.median(r["moved_eth"] for r in group),
            "served": st.median(r["served"] for r in group),
            "rounds": len(group)}
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment-source", required=True, nargs="+",
                    help="一个或多个 run 目录（分批跑时依次给出，会合并）")
    ap.add_argument("--output", required=True)
    ap.add_argument("--heatmap-end-s", type=float, default=684.0,
                    help="兼容 exp025/exp026 runner 的调用签名（本图不用）")
    args = ap.parse_args()
    figs = Path(args.output)
    figs.mkdir(parents=True, exist_ok=True)
    summary = aggregate(load(args.experiment_source))
    if "plain" not in summary:
        raise SystemExit("缺 plain 臂：盈亏平衡射线必须以 plain 点为参考")
    x0 = summary["plain"]["relay"]
    ordered = [a for a in ORDER if a in summary]
    ordered += [a for a in summary if a not in ordered]

    def rho_(arm):
        if arm == "plain":
            return None
        dx = x0 - summary[arm]["relay"]
        return summary[arm]["transfers"] / dx if dx > 0 else float("inf")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8.4, 5.6), dpi=160)
    x_lo = max(1.0, min(s["relay"] for s in summary.values()) * 0.5)
    y_top = max(s["transfers"] for s in summary.values()) * 3.0
    # 盈亏平衡射线簇：y = (κ/μ)(x0 − x)，过 plain 点 (x0, 0)，线上方为亏损区。
    # 射线在【线性】坐标里是直线；x 轴取对数后按采样点画，形状自然弯曲。
    for ratio in RAYS:
        xs = [x0 - (x0 - x_lo) * i / 200 for i in range(201)]
        ys = [max(ratio * (x0 - x), 1e-9) for x in xs]
        keep = [i for i, y in enumerate(ys) if y <= y_top]
        if len(keep) < 2:
            continue
        ax.plot([xs[i] for i in keep], [ys[i] for i in keep], ls="--", lw=0.9,
                color="0.62", zorder=1)
        # 标签放在射线左端稍内缩处，避免压到坐标轴外
        head = next(i for i in keep if xs[i] >= x_lo * 1.25)
        ax.annotate(rf"$\kappa/\mu={ratio:g}$", (xs[head], ys[head]), fontsize=7.5,
                    color="0.45", ha="left", va="bottom")
    for index, arm in enumerate(ordered):
        s = summary[arm]
        color = (COLOR_BROKER if arm == "topup"
                 else COLOR_GRAY if arm == "plain" else "tab:blue")
        y = max(s["transfers"], 0.5)
        ax.scatter(s["relay"], y, s=54, color=color, zorder=3,
                   marker="*" if arm == "topup" else "o",
                   edgecolor="black", linewidth=0.4)
        value = rho_(arm)
        tag = LABEL.get(arm, arm)
        if value is not None and value != float("inf"):
            tag += f"  $\\rho^*$={value:.3f}"
        # 右侧的点把标签甩到左边，左侧的点往右写；再按序交错上下偏移防压字
        left_of_point = s["relay"] > x0 * 0.55
        ax.annotate(tag, (s["relay"], y), fontsize=7.5,
                    ha="right" if left_of_point else "left",
                    xytext=(-8 if left_of_point else 6,
                            6 if index % 2 == 0 else -12),
                    textcoords="offset points")
    ax.axvline(x0, color="0.75", lw=0.8, zorder=0)
    ax.annotate("plain reference $x_0$", (x0, y_top), fontsize=7,
                color="0.45", ha="right", va="top")
    ax.set_xlabel("relay CTX (lower is better) →")
    ax.set_ylabel("rebalancing transfers (lower is better)")
    ax.set_xlim(x_lo, x0 * 2.6)      # 右侧留白，右侧各点的标注才不出画框
    ax.set_ylim(0.4, y_top)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.grid(alpha=0.25, lw=0.5, which="both")
    sessions = summary["plain"]["rounds"]
    suffix = "" if sessions >= 3 else f"  [analysis pass: {sessions} session]"
    ax.set_title("exp033 · SOTA rebalancing: relay vs transfers "
                 f"(median of {sessions} session{'s' if sessions > 1 else ''})"
                 f"{suffix}", fontsize=9)
    save(fig, figs / "exp033_pareto.png")

    lines = ["# exp033 · SOTA 对照汇总（中位数）", "",
             f"负载与 exp025 同参数；场次 = {summary['plain']['rounds']}；"
             f"plain 参考点 relay = {x0}。", "",
             "| 方案 | relay CTX [区间] | 搬运次数 | 搬量 ETH | 服务 CTX | "
             "ρ* = y/(x0−x) |", "|---|---:|---:|---:|---:|---:|"]
    for arm in ordered:
        s = summary[arm]
        value = rho_(arm)
        cell = "—" if value is None else (
            "∞（未降低 relay）" if value == float("inf") else f"{value:.3f}")
        lines.append(f"| {LABEL.get(arm, arm)} | {s['relay']} "
                     f"[{s['relay_lo']}..{s['relay_hi']}] | {s['transfers']} | "
                     f"{s['moved_eth']:.0f} | {s['served']} | {cell} |")
    lines += ["", "ρ* 越小 = 对经济环境越宽容 = 越稳健；ρ* 与任何 gas/费率假设无关。",
              "盈亏平衡射线 y=(κ/μ)(x0−x) 过 plain 点，线上方为亏损区。"]
    (figs.parent / "SUMMARY_CN.md").write_text("\n".join(lines) + "\n",
                                               encoding="utf-8")
    print(f"  figure: {figs / 'exp033_pareto.png'}")
    print(f"  table : {figs.parent / 'SUMMARY_CN.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
