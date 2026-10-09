#!/usr/bin/env python3
"""exp032 · A/B 进度与中间读数（随时可看，不干扰正在跑的实验）。

summary.json 要整轮跑完才写，所以中途读 driver 日志。
用法：python progress.py [--log /tmp/shift_ab_v2.log]
"""
import argparse
import glob
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
EPS = re.compile(r"^[^@]+@[^@]*@[^@]*@([0-9.]+)")
LINE = re.compile(
    r"^  (\S+): ([\d.]+) CTX/s \| relay (\d+) \| 改判 \d+ \| 拒答 \d+ \| "
    r"net \S+(?: \| events (\d+) \| 搬运 ([\d.]+) ETH \| 段占比 ([\d.]+))?")


def _eps(token):
    """原始臂 token：name@fund@cap@eps@chi@hl@qmin，ε 在下标 3。

    注意不能用日志/产物里的 arm 标签——那是重建过的（空的 cap/chi 被跳过），
    `topup@150.0@0.1@20.0` 里的 0.1 是 ε 还是 cap 看不出来。
    原始 token 才可靠。"""
    parts = token.split("@")
    try:
        return float(parts[3])
    except (IndexError, ValueError):
        return None


ARMS_RE = re.compile(r"arms=\[(.*?)\]")
TOKEN_RE = re.compile(r"'([^']+)'")


def _arm_tokens(seg):
    m = ARMS_RE.search(seg)
    return TOKEN_RE.findall(m.group(1)) if m else []


def from_driver_log(path):
    """按 ############ <tag> 切段，返回 {tag: {eps: {…}}}。"""
    txt = Path(path).read_text()
    segs = {}
    for chunk in re.split(r"#{5,}\s*", txt)[1:]:
        tag = chunk.split(" ")[0].split("·")[0].strip()
        eps_seq = [_eps(t) for t in _arm_tokens(chunk)]   # 按 arms 顺序
        n = 0
        for ln in chunk.splitlines():
            m = LINE.match(ln)
            if not m:
                continue
            _arm, tp, relay, ev, moved, legs = m.groups()
            eps = eps_seq[n] if n < len(eps_seq) else None
            n += 1
            segs.setdefault(tag, {})[eps] = {
                "relay": int(relay), "tp": float(tp),
                "events": int(ev) if ev else None,
                "moved": float(moved) if moved else None,
                "legs": float(legs) if legs else None,
            }
    return segs


def from_summary(pattern):
    got = {}
    for f in sorted(glob.glob(str(HERE / "out" / pattern / "*" / "summary.json"))):
        d = json.loads(Path(f).read_text())
        seq = [_eps(t) for t in
               str(d["params"].get("exp", {}).get("arms", "")).split(",")]
        for i, a in enumerate(d["arms"]):
            got[seq[i] if i < len(seq) else None] = {
                "relay": a["relayed"], "tp": a["throughput_ctx_per_s"],
                "events": a["tdr"]["events_opened"],
                "moved": a["tdr"]["moved_wei"] / 1e18,
                "legs": a["legs"]["tdr_share_of_ctx_legs"],
                "backlog": a["coord"].get("backlog_peak"),
                "gates": all(a["gates"].values()) and a["burn_reconcile"]["ok"],
            }
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="/tmp/ab_per_arm.log")
    a = ap.parse_args()

    live = from_driver_log(a.log) if Path(a.log).exists() else {}
    print(f"  === 进度 · {a.log} ===")
    for tag, label in (("A_noshift", "A 无突变 "), ("B_shift8to11", "B 突变 8→11")):
        d = live.get(tag, {})
        print(f"\n  【{label}】已完成 {len(d)}/3 臂")
        for e in sorted(d):
            r = d[e]
            print(f"    ε={e:<5} relay {r['relay']:>6} | 事件 {str(r['events']):>5} "
                  f"| 搬量 {r['moved']:>12,.0f} | 段占比 {r['legs']:.3f} "
                  f"| {r['tp']:.1f} CTX/s")

    A, B = live.get("A_noshift", {}), live.get("B_shift8to11", {})
    if A or B:
        print(f"\n  ---- ε → relay 对照 ----")
        print(f"    {'ε':>5} {'A':>7} {'B':>7} {'B−A':>7}")
        for e in sorted(set(A) | set(B)):
            av = A.get(e, {}).get("relay")
            bv = B.get(e, {}).get("relay")
            d = f"{bv-av:+d}" if (av is not None and bv is not None) else "—"
            print(f"    {e:>5} {str(av):>7} {str(bv):>7} {d:>7}")
        if A and B:
            ab, bb = min(A, key=lambda k: A[k]["relay"]), min(B, key=lambda k: B[k]["relay"])
            print(f"\n    A 最优 ε = {ab}（relay {A[ab]['relay']}）"
                  f"   B 最优 ε = {bb}（relay {B[bb]['relay']}）")
            print("    → 最优点移动 ⇒ 右支出现"
                  if ab != bb else "    → 最优点未动：突变还没罚到参数")

    for tag, dn in (("A", "arm_A_eps*"), ("B", "arm_B_eps*")):
        s = from_summary(dn)
        if s:
            print(f"\n  【{tag} 收尾读数（summary.json）】")
            for e in sorted(s):
                r = s[e]
                print(f"    ε={e:<5} relay {r['relay']:>6} | 搬量 {r['moved']:>12,.0f} "
                      f"| backlog {r['backlog']} | gates "
                      f"{'OK' if r.get('gates') else 'FAIL'}")


if __name__ == "__main__":
    main()
