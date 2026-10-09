#!/usr/bin/env python3
"""exp032 · 完整对比搜索的汇总出表。

数据源：
  arm_Y_*  闪烁负载（阶跃 + 高频轮换）—— 主数据，两条 U 曲线都在这条负载上
  arm_G_*  纯阶跃负载（对照：没有闪烁时 hl 没有左支）
产物：out/final_config_table.md
"""
import json
import re
import statistics as st
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"


def rows(pat, glob_pat="arm_Y_*/*/summary.json"):
    p = re.compile(pat)
    got = {}
    for f in OUT.glob(glob_pat):
        # 用 search 不用 fullmatch：pattern 只写参数段（如 hl(\d+)_r\d），
        # 目录名前面还有 arm_Y_ / arm_G_ 前缀。
        m = p.search(f.parts[-3])
        if not m:
            continue
        got.setdefault(m.group(1), []).append(_read(f))
    return got


def _read(f):
    a = json.loads(f.read_text())["arms"][0]
    return {"relay": a["relayed"], "moved": a["tdr"]["moved_wei"] / 1e18,
            "events": a["tdr"]["events_opened"],
            "transfers": a["tdr"]["transfers_done"],
            "legs": a["legs"]["tdr_share_of_ctx_legs"],
            "tp": a["throughput_ctx_per_s"],
            "ok": all(a["gates"].values()) and a["burn_reconcile"]["ok"]}


def med(v):
    return st.median(v) if v else float("nan")


def table(doc, print_lines, title, got, sort_key, xlabel):
    doc += [f"\n### {title}\n",
            f"| {xlabel} | n | relay 中位 [区间] | 搬运次数中位 [区间] | 搬量中位 [区间] ETH |",
            "|---|---|---|---|---|"]
    print_lines.append(f"\n--- {title} ---")
    for k in sorted(got, key=sort_key):
        raw = got[k]
        v = [x for x in raw if x["ok"]]        # 未过守恒门的整跑剔除
        if not v:
            continue
        flags = f"  （剔除 {len(raw)-len(v)} 次未过门）" if len(v) < len(raw) else ""
        def cell(key, fmt="{:.0f}"):
            xs = [x[key] for x in v]
            rng = f"[{fmt.format(min(xs))}..{fmt.format(max(xs))}]" \
                  if len(xs) > 1 else "—"
            return f"{fmt.format(med(xs))} {rng}"
        rl = cell("relay")
        tr = cell("transfers")
        mv = cell("moved", "{:,.0f}")
        doc.append(f"| {k} | {len(v)} | {rl} | {tr} | {mv} |{flags}")
        print_lines.append(f"  {k:<8} n={len(v)} relay {rl:<16} "
                           f"搬运 {tr:<16} 搬量 {mv}{flags}")


def main():
    P = []
    doc = ["# exp032 · 完整对比搜索汇总（闪烁负载）", "",
           "**负载**：阶跃 + 高频轮换。前 30% 目的分布原样；后 70% 被接管，"
           "热点在分片 11 与 3 之间每 ≈13 块换一次。",
           "",
           "**基底**：垫资 45 ETH/分片、rate=120、4 万笔、16 分片 × 50 broker、"
           "刷新节流 10/16、ε=0.95。扫某参数时另一个固定在近最优"
           "（hl=10、q_min=0.3）。",
           "",
           "**方案**：topup + EWMA（§7.7 定稿方案），此处只为它选参数与负载。",
           "轮次在外层交错，抵消机器状态的批次漂移。"]

    # 一、TDR 有效性
    # 未过守恒门的整跑一律剔除（实测 96 组里有 1 组）
    allruns = [x for pat in (r"(plain|valve|proportional)", r"hl(\d+)_r\d",
                             r"q([0-9.]+)_r\d")
               for xs in rows(pat).values() for x in xs]
    dropped = [x for x in allruns if not x["ok"]]
    if dropped:
        print(f"  剔除 {len(dropped)} 次未过守恒门的运行"
              f"（其中 relay 值：{[d['relay'] for d in dropped]}）")
    base = rows(r"(plain|valve|proportional)")
    doc += ["\n## 一、TDR 有效性（同一闪烁负载）\n",
            "| 方案 | relay 中位 [区间] | n | 搬量中位 ETH | 段占比 |",
            "|---|---|---|---|---|"]
    P.append("\n--- 一、TDR 有效性 ---")
    for k in ("plain", "valve", "proportional"):
        if k not in base:
            continue
        v = base[k]
        rl = [x["relay"] for x in v]
        rng = f"[{min(rl)}..{max(rl)}]"
        P.append(f"  {k:<14} relay {med(rl):6.0f} {rng:<12} n={len(v)} "
                 f"搬量 {med([x['moved'] for x in v]):10,.0f}")
        doc.append(f"| {k} | {med(rl):,.0f} {rng} | {len(v)} | "
                   f"{med([x['moved'] for x in v]):,.0f} | "
                   f"{med([x['legs'] for x in v]):.3f} |")

    # 代表臂：hl=10、q_min=0.3 —— 两条曲线的最低点交汇处
    rep = rows(r"hl(20)_r\d")
    if "20" in rep:
        v = rep["20"]
        rl = [x["relay"] for x in v]
        rng = f"[{min(rl)}..{max(rl)}]"
        doc.append(f"| **topup+EWMA (hl=20, q_min=0.3)** | **{med(rl):,.0f} {rng}** "
                   f"| {len(v)} | **{med([x['moved'] for x in v]):,.0f}** | "
                   f"{med([x['legs'] for x in v]):.3f} |")
        P.append(f"  topup+EWMA(hl20,q0.3) relay {med(rl):6.0f} {rng} n={len(v)}")

    table(doc, P, "二、hl 曲线（q_min=0.3 固定）",
          rows(r"hl(\d+)_r\d"), lambda s: int(s), "hl")
    table(doc, P, "三、q_min 曲线（hl=10 固定）",
          rows(r"q([0-9.]+)_r\d"), float, "q_min")

    # 四、对照：闪烁 vs 纯阶跃
    gy = rows(r"hl(\d+)_r\d")
    gg = rows(r"hl(\d+)_r\d", "arm_G_*/*/summary.json")
    if gy and gg:
        doc += ["\n## 四、对照：闪烁负载 vs 纯阶跃负载（同一组 hl）\n",
                "纯阶跃下 hl 单调上升（无左支）；叠上闪烁后左支出现。",
                "这正是「短半衰期要有代价，负载必须含有不值得追的波动」的直接证据。\n",
                "| hl | 闪烁负载 relay 中位 | 纯阶跃 relay 中位 |",
                "|---|---|---|"]
        P.append("\n--- 四、闪烁 vs 纯阶跃 ---")
        for hl in sorted(set(gy) | set(gg), key=int):
            a = [x["relay"] for x in gy.get(hl, [])]
            b = [x["relay"] for x in gg.get(hl, [])]
            sa = f"{med(a):.0f}" if a else "—"
            sb = f"{med(b):.0f}" if b else "—"
            P.append(f"  hl={hl:<4} 闪烁 {sa:<8} 纯阶跃 {sb}")
            doc.append(f"| {hl} | {sa} | {sb} |")

    # 五、ε（沿用纯阶跃口径，已结案）
    ep = rows(r"eps([0-9.]+)_r\d", "arm_G_*/*/summary.json")
    if ep:
        table(doc, P, "五、ε 曲线（纯阶跃口径，已结案）", ep, float, "ε")
    # 六、χ
    chi = rows(r"chi(\d+)", "arm_G_*/*/summary.json")
    if chi:
        doc += ["\n## 六、χ 选值（不进论文，只为定一个够用的数）\n",
                "| χ | relay | 事件 |", "|---|---|---|"]
        P.append("\n--- 六、χ ---")
        for k in sorted(chi, key=int):
            v = chi[k][0]
            P.append(f"  χ={k:<4} relay {v['relay']}")
            doc.append(f"| {k} | {v['relay']} | {v['events']} |")

    print("\n".join(P))
    p = OUT / "final_config_table.md"
    p.write_text("\n".join(doc) + "\n")
    print(f"\n  表：{p}")


if __name__ == "__main__":
    sys.exit(main())
