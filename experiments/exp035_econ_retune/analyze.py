"""exp035 出表/出图：把同一批网格点按四种目标分别排名。

四种目标（都以 plain 为基线，x0 = plain 的 relay 数）：

  1. relay        ：原始 relay CTX 数（= 老 exp008 的字面目标）
  2. C            ：relay + 搬运次数（= exp028/029 的等权主指标）
  3. rho          ：ρ* = y / (x0 − x)，该方案不亏所需的最低抽成（越低越好）
  4. net          ：净收益 = β·(x0 − x) − y，含 plain 这个「不搬运」选项（越高越好）

前三个都只在 TDR 家族内部比较；第四个才允许"什么都不做"胜出。
ρ* ≤ β 与 net ≥ 0 是同一件事（β 见 config 的 exp.fee_beta）。

用法（runner 收尾会自动带上这两个参数）：
  python analyze.py --source <run/> --output <run/figs>
"""
import argparse
import csv
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from brokerlab.plotting import COLOR_ALT, COLOR_BROKER, COLOR_GRAY, COLOR_RELAY, save  # noqa: E402

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REQUIRED = ("value", "round", "n", "served", "relayed", "transfers")
PALETTE = (COLOR_BROKER, COLOR_RELAY, COLOR_ALT, "#7B3294")


def load_rows(source):
    path = Path(source) / "raw_results.csv"
    if not path.is_file():
        raise FileNotFoundError(f"缺少 {path}；先跑 run.py")
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError("raw_results.csv 为空（还没有任何臂完成）")
    missing = [k for k in REQUIRED if k not in rows[0]]
    if missing:
        raise ValueError(f"raw_results.csv 缺列: {missing}")
    return rows


def beta_of(source, default=0.10):
    pj = Path(source) / "params.json"
    if pj.is_file():
        try:
            return float(json.loads(pj.read_text())["exp"].get("fee_beta", default))
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
    return default


def aggregate(rows):
    """按网格点聚合（多场取中位数；单场即该次观测）。"""
    by_point = {}
    for r in rows:
        by_point.setdefault(r["value"], []).append(r)
    agg = {}
    for label, group in by_point.items():
        agg[label] = dict(
            rounds=len(group),
            relayed=st.median(int(g["relayed"]) for g in group),
            transfers=st.median(int(g["transfers"]) for g in group),
            served=st.median(int(g["served"]) for g in group),
            moved_eth=st.median(float(g["moved_eth"]) for g in group),
            throughput=st.median(float(g["throughput"]) for g in group),
        )
    return agg


def score(agg, beta):
    if "plain" not in agg:
        raise ValueError("网格里没有 plain 基线臂，无法算 Δn_b（经济目标必须有它）")
    x0 = agg["plain"]["relayed"]
    out = []
    for label in sorted(agg):
        a = agg[label]
        if label == "plain":
            out.append(dict(label=label, is_baseline=True, rounds=a["rounds"], relay=x0,
                            transfers=0, dnb=0, rho=None, net=0.0, C=x0,
                            eth_per_move=None, throughput=a["throughput"]))
            continue
        delta = x0 - a["relayed"]
        y = a["transfers"]
        out.append(dict(label=label, is_baseline=False, rounds=a["rounds"], relay=a["relayed"],
                        transfers=y, dnb=delta,
                        rho=(y / delta) if delta > 0 else None,
                        net=beta * delta - y, C=a["relayed"] + y,
                        eth_per_move=(a["moved_eth"] / y) if y else None,
                        throughput=a["throughput"]))
    return out, x0


def rank_map(scored, key, reverse=False, only_tdr=False):
    pool = [s for s in scored if s[key] is not None and not (only_tdr and s["is_baseline"])]
    pool.sort(key=lambda s: s[key], reverse=reverse)
    return {s["label"]: i + 1 for i, s in enumerate(pool)}


def best(scored, key, reverse=False):
    pool = [s for s in scored if s[key] is not None]
    if not pool:
        return "—"
    return (max if reverse else min)(pool, key=lambda s: s[key])["label"]


def fmt(v, nd=3, dash="—"):
    return dash if v is None else f"{v:.{nd}f}"


def write_csv(path, scored, ranks):
    fields = ["label", "is_baseline", "rounds", "relay", "transfers", "dnb", "rho", "net",
              "C", "eth_per_move", "throughput", "rank_relay", "rank_C", "rank_rho", "rank_net"]
    with Path(path).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in scored:
            row = {k: s.get(k) for k in fields[:11]}
            for rk in ("rank_relay", "rank_C", "rank_rho", "rank_net"):
                row[rk] = ranks[rk].get(s["label"], "")
            w.writerow(row)


def plot(scored, beta, outdir):
    pts = [s for s in scored if not s["is_baseline"]]
    if not pts:
        return []
    by_q = {}
    for s in pts:
        eps_part, q_part = s["label"].split("_")
        by_q.setdefault(float(q_part[4:]), []).append((float(eps_part[3:]), s))
    # 图注一律用 ASCII：本机 matplotlib 只有 DejaVu Sans，中文会渲染成方框。
    # 中文说明在 SUMMARY_CN.md 里。
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, ykey, ylabel, hline, hlabel in (
            (axes[0], "rho", "rho* = transfers / delta_n_b", beta,
             f"beta = {beta:g} (break-even)"),
            (axes[1], "net", "net profit (in F)", 0.0, "plain = no rebalancing")):
        for i, qmin in enumerate(sorted(by_q)):
            series = sorted(by_q[qmin])
            ax.plot([e for e, _ in series], [s[ykey] for _, s in series], "o-",
                    color=PALETTE[i % len(PALETTE)], label=f"q_min = {qmin:g}")
        ax.axhline(hline, color=COLOR_GRAY, ls="--", lw=1, label=hlabel)
        ax.set_xlabel("epsilon (larger = lazier trigger)")
        ax.set_ylabel(ylabel)
        ax.legend(fontsize=8)
        ax.grid(alpha=0.25)
    out = Path(outdir) / "econ_grid.png"
    save(fig, out)
    return [out]


def write_summary(path, scored, x0, beta, ranks):
    best_net = best(scored, "net", reverse=True)
    best_C = best(scored, "C")
    lines = ["# exp035 · 经济目标下的参数重调", "",
             f"plain 基线 relay = {x0:g}；β = {beta:g}；ρ* ≤ β（等价净收益 ≥ 0）才值得做 TDR。", "",
             "| 网格点 | relay | 搬运 y | Δn_b | C=relay+y | ρ* | 净收益 | ETH/笔 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in sorted(scored, key=lambda s: s["net"], reverse=True):
        tag = "**plain（不搬运）**" if s["is_baseline"] else s["label"]
        lines.append(f"| {tag} | {s['relay']:g} | {s['transfers']:g} | {s['dnb']:g} | "
                     f"{s['C']:g} | {fmt(s['rho'])} | {s['net']:+.0f} | "
                     f"{fmt(s['eth_per_move'], 0)} |")
    lines += ["", "## 四种目标各自的最优解", "", "| 目标 | 最优网格点 |", "|---|---|",
              f"| relay 最小 | {best(scored, 'relay')} |",
              f"| C 最小（exp028/029 等权口径） | {best_C} |",
              f"| ρ* 最小 | {best(scored, 'rho')} |",
              f"| 净收益最大（含 plain） | {best_net} |", ""]
    baseline_wins = next(s for s in scored if s["label"] == best_net)["is_baseline"]
    if baseline_wins:
        lines += [f"**结论：最优解是 plain（不搬运）。** 网格中没有任何 TDR 参数点能超过「什么都不做」"
                  f"的 0 收益 —— 即全部 ρ* > β = {beta:g}。", ""]
    else:
        lines += [f"**结论：最优参数点是 `{best_net}`**（净收益 "
                  f"{next(s for s in scored if s['label'] == best_net)['net']:+.0f} F，ρ* = "
                  f"{fmt(next(s for s in scored if s['label'] == best_net)['rho'])}）。", ""]
        if best_net != best_C:
            lines += [f"注意：等权 C 口径选出的点是 `{best_C}`，与经济目标**不一致**。", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="exp035 经济目标评分")
    ap.add_argument("--source", required=True, help="运行目录（含 raw_results.csv）")
    ap.add_argument("--output", default=None, help="出图目录，默认 <source>/figs")
    args = ap.parse_args()
    source = Path(args.source)
    outdir = Path(args.output) if args.output else source / "figs"
    outdir.mkdir(parents=True, exist_ok=True)

    beta = beta_of(source)
    scored, x0 = score(aggregate(load_rows(source)), beta)
    ranks = dict(rank_relay=rank_map(scored, "relay"),
                 rank_C=rank_map(scored, "C"),
                 rank_rho=rank_map(scored, "rho"),
                 rank_net=rank_map(scored, "net", reverse=True))

    print(f"plain relay = {x0:g} | β = {beta:g} | 网格点 {len(scored) - 1} 个")
    print(f"  {'网格点':18s} {'relay':>7s} {'y':>6s} {'Δn_b':>7s} {'C':>8s} {'ρ*':>7s} {'净收益':>9s}")
    for s in sorted(scored, key=lambda s: s["net"], reverse=True):
        tag = "plain" if s["is_baseline"] else s["label"]
        print(f"  {tag:18s} {s['relay']:>7g} {s['transfers']:>6g} {s['dnb']:>7g} "
              f"{s['C']:>8g} {fmt(s['rho']):>7s} {s['net']:>+9.0f}")
    print("\n  四种目标的最优解：")
    print(f"    relay 最小 → {best(scored, 'relay')}")
    print(f"    C 最小     → {best(scored, 'C')}")
    print(f"    ρ* 最小    → {best(scored, 'rho')}")
    print(f"    净收益最大 → {best(scored, 'net', reverse=True)}")

    write_csv(source / "econ_grid.csv", scored, ranks)
    write_summary(source / "SUMMARY_CN.md", scored, x0, beta, ranks)
    for path in plot(scored, beta, outdir):
        print(f"  figure: {path}")
    print(f"  table : {source / 'SUMMARY_CN.md'}")
    print(f"  csv   : {source / 'econ_grid.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
