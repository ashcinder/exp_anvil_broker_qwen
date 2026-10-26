#!/usr/bin/env python3
"""exp009 · exp001-008 全功能开关矩阵（编排器）。

它不实现任何机制，只做四件事：
  ① 读 config.yaml 的开关表（stage = 一个实验包的开关+参数覆盖）。
  ② 按固定依赖序，把启用的 stage 逐个子进程调用各包自己的 run.py。
     --set 覆盖原样转发；profile=smoke 追加各包的缩规模覆盖。
  ③ 收每场退出码与产物目录（解析 "out:" 打印，失败则取包内最新目录），
     提取 summary/report 的判据字段。
  ④ 聚合 out/<ts>/matrix_report.json + 控制台总表。退出码：全部 stage
     的 rc 都在 expected_rc 内 ⇒ 0，否则 1。

诚实边界（写进 README）：统一的是入口/开关/报告，不是管线本身。
各包的机制与判据代码不合并，也不被本包修改。

用法：
  python run.py --dry-run                    # 只看执行计划
  python run.py --only exp001,exp008         # 临时只跑指定 stage
  python run.py                              # 按 config 的开关表执行
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from brokerlab.config import apply_overrides, load_config, to_params_dict  # noqa: E402

OUT_RE = re.compile(r"out:\s*(\S+?/out/20\d+_\d+)")


def discover_out(pkg_dir: Path, stdout: str, before: set) -> Path:
    """产物目录：优先解析打印的 out 路径，退化到"运行后新增的最新目录"。"""
    for m in reversed(OUT_RE.findall(stdout)):
        p = Path(m)
        if p.exists():
            return p
    base = pkg_dir / "out"
    if not base.is_dir():
        return None
    dirs = [d for d in base.iterdir() if d.is_dir()]
    new = [d for d in dirs if d not in before] or dirs
    return max(new, key=lambda d: d.stat().st_mtime)


def extract(out_dir: Path) -> dict:
    """从 summary.json / report.json 提取判据字段（只读，不改结构）。"""
    got = {}
    for name in ("summary.json", "report.json"):
        f = out_dir / name
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        sub = {k: d[k] for k in ("gates", "hypotheses", "verdicts",
                                 "verdict_all") if k in d}
        if isinstance(d.get("arms"), list):
            sub["arms"] = [{"arm": a.get("arm"), "relay": a.get("relayed")}
                           for a in d["arms"]]
        elif isinstance(d.get("arms"), dict):
            sub["arms"] = {k: {"relay_median": (v.get("relay") or {}).get("median")}
                           for k, v in d["arms"].items()}
        got[name] = sub
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--set", dest="overrides", action="append", default=[])
    ap.add_argument("--only", default=None, help="逗号分隔的 stage id 白名单")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = apply_overrides(load_config(args.config), args.overrides)
    stages = cfg.exp["stages"]
    profile = str(cfg.exp.get("profile", "smoke"))
    only = set(args.only.split(",")) if args.only else None
    out_root = HERE / "out" / datetime.now().strftime("%Y%m%d_%H%M%S")
    if not args.dry_run:
        out_root.mkdir(parents=True)
    print(f"  exp009 · profile={profile} out={out_root if not args.dry_run else '(dry)'}")

    report = {"profile": profile, "stages": [], "params": to_params_dict(cfg)}
    all_ok = True
    stage_items = stages.items() if isinstance(stages, dict) else \
        [(st["pkg"].split("_")[0], st) for st in stages]
    for sid, sc in stage_items:
        sc = dict(sc)
        if only and sid not in only:
            continue
        pkg_dir = HERE.parent / sc["pkg"]
        script = pkg_dir / "run.py"
        sets = list(sc.get("overrides", []))
        if profile == "smoke":
            sets += list(sc.get("smoke_overrides", []))
        cmd = [sys.executable, str(script)]
        for x in sets:
            cmd += ["--set", x]
        if not sc.get("enabled", True):
            print(f"  [{sid}] 跳过（开关关闭）")
            report["stages"].append({"stage": sid, "skipped": True})
            continue
        if args.dry_run:
            print(f"  [{sid}] 计划: cd {pkg_dir.name} && " + " ".join(cmd[1:]))
            report["stages"].append({"stage": sid, "cmd": cmd[1:], "dry": True})
            continue
        before = {d for d in (pkg_dir / "out").glob("20*") if d.is_dir()} \
            if (pkg_dir / "out").is_dir() else set()
        print(f"\n  [{sid}] 启动（{len(sets)} 个覆盖）…", flush=True)
        t0 = time.monotonic()
        p = subprocess.run(cmd, cwd=pkg_dir, capture_output=True, text=True)
        (pkg_log := out_root / f"{sid}.log").write_text(p.stdout + p.stderr)
        dur = time.monotonic() - t0
        exp_rc = [int(r) for r in sc.get("expected_rc", [0])]
        ok = p.returncode in exp_rc
        all_ok = all_ok and ok
        od = discover_out(pkg_dir, p.stdout, before)
        rec = {"stage": sid, "rc": p.returncode, "expected_rc": exp_rc,
               "ok": ok, "wall_s": round(dur, 1),
               "out": str(od) if od else None}
        if od:
            rec["criteria"] = extract(od)
        report["stages"].append(rec)
        print(f"  [{sid}] rc={p.returncode} 期望{exp_rc} "
              f"{'OK' if ok else 'FAIL'} | {dur:.0f}s | out={rec['out']}")
    if not args.dry_run:
        report["all_ok"] = all_ok
        (out_root / "matrix_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str))
        print("\n" + "=" * 66)
        for r in report["stages"]:
            if r.get("skipped"):
                print(f"  {r['stage']:<7} 跳过")
                continue
            crit = r.get("criteria", {})
            keys = ",".join(crit.keys())
            print(f"  {r['stage']:<7} rc={r.get('rc')} "
                  f"{'OK ' if r.get('ok') else 'FAIL'} {r.get('wall_s', 0):>7.1f}s "
                  f"判据文件[{keys}]")
        print(f"  matrix_report: {out_root / 'matrix_report.json'}")
        print("=" * 66)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
