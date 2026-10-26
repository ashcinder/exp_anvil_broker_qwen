#!/usr/bin/env python3
"""exp011 · 突变 A/B 对比：读两条腿的 summary.json，出表与曲线。

用法：
  python compare_shift.py                       # 默认读 out/formal_A_noshift 与 out/formal_B_shift8to11
  python compare_shift.py --a <dir> --b <dir>
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 臂 token 里 ε 的位置：name@fund@cap@eps@chi@hl@qmin
RE_EPS = re.compile(r"^[^@]+@[^@]*@[^@]*@([0-9.]+)")


def eps_of(tag):
    m = RE_EPS.match(tag)
    return float(m.group(1)) if m else float("nan")


def load(d):
    """把一个 out-root 下所有 <ts>/summary.json 的臂合并成 {eps: [读数…]}。"""
    rows = []
    for sf in sorted(Path(d).glob("*/summary.json")):
        data = json.loads(sf.read_text())
        for a in data["arms"]:
            rows.append({
                "arm": a["arm"],
                "eps": eps_of(a["arm"]),
                "relay": a["relayed"],
                "events": a["tdr"]["events_opened"],
                "transfers": a["tdr"]["transfers_done"],
                "moved_eth": a["tdr"]["moved_wei"] / 1e18,
                "legs_share": a["legs"]["tdr_share_of_ctx_legs"],
                "throughput": a["throughput_ctx_per_s"],
                "gates": all(a["gates"].values()) and a["burn_reconcile"]["ok"],
                "src": sf.parent.name,
            })
    return rows


def table(label, rows):
    print(f"\n{'='*82}\n{label}\n{'='*82}")
    if not rows:
        print("  （无数据）")
        return {}
    print(f"  {'ε':>5} {'relay':>7} {'事件':>7} {'搬运次数':>9} {'搬量 ETH':>12} "
          f"{'段占比':>7} {'吞吐':>7} gates")
    by = {}
    for r in sorted(rows, key=lambda x: x["eps"]):
        by.setdefault(r["eps"], []).append(r)
        g = "OK" if r["gates"] else "FAIL"
        print(f"  {r['eps']:>5.2f} {r['relay']:>7} {r['events']:>7} "
              f"{r['transfers']:>9} {r['moved_eth']:>12,.0f} "
              f"{r['legs_share']:>7.3f} {r['throughput']:>7.1f} {g}")
    print(f"  {'—'*70}")
    print(f"  {'ε':>5} {'relay 中位':>10} {'n':>3} {'搬量中位':>12}")
    med = {}
    for e, rs in sorted(by.items()):
        rl = sorted(x["relay"] for x in rs)
        mv = sorted(x["moved_eth"] for x in rs)
        m_rl = rl[len(rl) // 2]
        m_mv = mv[len(mv) // 2]
        med[e] = (m_rl, m_mv, len(rs))
        print(f"  {e:>5.2f} {m_rl:>10} {len(rs):>3} {m_mv:>12,.0f}")
    return med


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default=str(HERE / "out" / "formal_A_noshift"))
    ap.add_argument("--b", default=str(HERE / "out" / "formal_B_shift8to11"))
    ap.add_argument("--plot", action="store_true")
    a = ap.parse_args()

    ra, rb = load(a.a), load(a.b)
    ma = table(f"A · 无突变   [{a.a}]", ra)
    mb = table(f"B · 突变 8→11 [{a.b}]", rb)

    if ma and mb:
        print(f"\n{'='*82}\n对比：relay 随 ε 的走向\n{'='*82}")
        print(f"  {'ε':>5} {'A relay':>9} {'B relay':>9} {'B−A':>8} "
              f"{'A 搬量':>12} {'B 搬量':>12}")
        for e in sorted(set(ma) | set(mb)):
            av = ma.get(e, (None, None, 0))[0]
            bv = mb.get(e, (None, None, 0))[0]
            am = ma.get(e, (None, None, 0))[1]
            bm = mb.get(e, (None, None, 0))[1]
            d = f"{bv-av:+d}" if (av is not None and bv is not None) else "—"
            print(f"  {e:>5.2f} {str(av):>9} {str(bv):>9} {d:>8} "
                  f"{am:>12,.0f} {bm:>12,.0f}")
        abest = min(ma, key=lambda e: ma[e][0]) if ma else None
        bbest = min(mb, key=lambda e: mb[e][0]) if mb else None
        print(f"\n  A 组 relay 最优 ε = {abest}")
        print(f"  B 组 relay 最优 ε = {bbest}")
        print("  → 两者不同即出现右支（参数不再'越大越好'）"
              if abest != bbest else
              "  → 最优点未移动：突变没罚到参数，需加大强度或换 shift_start")

    if a.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
            for ax_, rows, lab in ((ax[0], ra, "A 无突变"), (ax[1], rb, "B 突变")):
                xs = sorted({r["eps"] for r in rows})
                for y, name in (("relay", "relay 笔数"), ("moved_eth", "搬量 ETH")):
                    ys = []
                    for x in xs:
                        v = [r[y] for r in rows if r["eps"] == x]
                        ys.append(sum(v) / len(v) if v else float("nan"))
                    ax_.plot(xs, ys, marker="o", label=name)
                ax_.set_xlabel("ε（死区深度）")
                ax_.set_title(lab)
                ax_.legend()
            ax[1].set_yscale("log")
            fig.tight_layout()
            out = HERE / "out" / "shift_ab_curve.png"
            fig.savefig(out, dpi=140)
            print(f"\n  图：{out}")
        except Exception as e:                                   # noqa: BLE001
            print(f"  (绘图跳过：{e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
