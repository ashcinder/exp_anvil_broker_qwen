"""brokerlab 命令行入口 —— 两个子命令：

  doctor   本机环境体检（实验室迁移的第一道门）
  demo     M1 可行骨架：一笔【真实】跨分片转账走 BrokerChain broker 路径
           + 一笔走 relay 兜底，数据来自真实 ETH 主网 trace，
           最后用链上守恒校验判定成败。
"""
import argparse
import subprocess
import sys
import time
import typing as t
from datetime import datetime
from importlib import metadata
from pathlib import Path

from . import __version__
from .config import (ETH, RunCfg, apply_overrides, load_config,
                     to_params_dict, validate, write_params_json)

def _fmt_eth(wei: int) -> str:
    """wei → 保留 4 位的 ETH 字符串（仅控制台展示用）。"""
    return f"{wei / ETH:.4f}"


# ===========================================================================
# doctor
# ===========================================================================

def cmd_doctor(config_path: t.Optional[str]) -> int:
    """环境体检：Python/web3/eth-account 版本、anvil 可用性、配置合法性、
    端口空闲、trace CSV 在位。任何 FAIL 返回 1。"""
    rows: t.List[t.Tuple[str, bool, str]] = []

    v = sys.version_info
    rows.append(("python >= 3.11", v >= (3, 11), f"found {v.major}.{v.minor}.{v.micro}"))

    for pkg, want in (("web3", "7"), ("eth-account", "0.13")):
        try:
            ver = metadata.version(pkg)
            rows.append((f"{pkg} == {want}.x", ver.startswith(want), ver))
        except metadata.PackageNotFoundError:
            rows.append((f"{pkg} installed", False, "missing — pip install -r requirements.txt"))

    try:
        import yaml  # noqa: F401
        rows.append(("PyYAML", True, "ok"))
    except ImportError:
        rows.append(("PyYAML", False, "missing"))

    from .chain import resolve_anvil_bin
    anvil_bin = None
    try:
        anvil_bin = resolve_anvil_bin()
        out = subprocess.run([anvil_bin, "--version"], capture_output=True,
                             text=True, timeout=10)
        first = (out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else "?"
        rows.append(("anvil", True, f"{anvil_bin} | {first}"))
    except Exception as e:  # noqa: BLE001
        rows.append(("anvil", False, str(e)))

    cfg = None
    if config_path:
        try:
            cfg = load_config(config_path)
            rows.append((f"config {config_path}", True, f"label={cfg.label}, shards={cfg.chain.num_shards}"))
        except Exception as e:  # noqa: BLE001
            rows.append((f"config {config_path}", False, str(e)))
    if cfg is not None:
        from .chain import _port_free
        busy = [cfg.chain.base_port + i for i in range(cfg.chain.num_shards)
                if not _port_free(cfg.chain.base_port + i)]
        rows.append(("ports free", not busy,
                     "ok" if not busy else f"busy: {busy} (another run?)"))
        if cfg.traffic.kind == "real_csv" and cfg.traffic.real_csv_path:
            p = Path(cfg.traffic.real_csv_path)
            rows.append(("real CSV present", p.exists(), str(p)))

    width = max(len(name) for name, _, _ in rows)
    failed = False
    print("\n  brokerlab doctor")
    print("  " + "-" * (width + 12))
    for name, ok, detail in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}")
        failed = failed or not ok
    return 1 if failed else 0


# ===========================================================================
# demo (M1 walking skeleton)
# ===========================================================================

def _snapshot(tx, addresses: t.List[str], shards: t.Iterable[int]):
    """对 (地址,分片) 全集拍一张余额快照，供前后差分对账。"""
    return {(a, s): tx.get_balance(a, s) for a in addresses for s in shards}


def cmd_demo(cfg: RunCfg) -> int:
    """M1 演示主流程：起链→选 2 笔真实 CTX→setup 充值→双路径执行→
    9 项守恒核对→写 report.json→收摊。退出码 0 ⇔ 全部核对通过。"""
    from .brokerchain import (BURN_ADDRESS, BrokerChain, CTX, ROUTE_BROKER,
                              ROUTE_RELAY)
    from .chain import AnvilCluster, Connections, resolve_anvil_bin
    from .identity import UserManager
    from .real_data import extract
    from .tx import TxService

    run_dir = Path(cfg.output.results_root) / f"{cfg.label}_demo_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)

    if cfg.traffic.kind != "real_csv" or not cfg.traffic.real_csv_path:
        print("  demo requires traffic.kind=real_csv with a real_csv_path set")
        return 2
    users = UserManager(cfg.chain.mnemonic)
    cluster = AnvilCluster(cfg.chain, run_dir / "logs")
    failed = True
    try:
        # ---- 1. chain up -------------------------------------------------
        print(f"  anvil: {resolve_anvil_bin(cfg.chain.anvil_bin)}")
        print(f"  starting {cfg.chain.num_shards} shards on ports "
              f"{cfg.chain.base_port}+ (block_time={cfg.chain.block_time_s}s) ...")
        cluster.start()
        cluster.write_pids(run_dir / "pids.json")
        conns = Connections(cfg.chain)
        conns.wait_all_ready()
        tx = TxService(conns, users, cfg.chain)
        for s in range(cfg.chain.num_shards):
            print(f"    shard_{s}: chainId={conns.web3(s).eth.chain_id} "
                  f"block={tx.block_number(s)}")

        # ---- 2. pick real-data CTXs --------------------------------------
        print("  loading real ETH trace rows (streaming) ...")
        pool = extract(
            cfg.traffic.real_csv_path,
            num_shards=cfg.chain.num_shards, num_users=cfg.scale.num_users,
            user_base_index=cfg.scale.user_base_index,
            value_floor_wei=int(cfg.traffic.value_floor_eth * ETH),
            value_cap_wei=int(cfg.traffic.value_cap_eth * ETH),
            limit=cfg.traffic.max_extract,
        )
        if len(pool) < 2:
            raise RuntimeError(f"not enough real cross-shard CTXs extracted: {len(pool)}")
        # pick two CTXs with fully disjoint accounts so funding and the
        # expected-delta checks stay independent per actor
        first = pool[0]
        used = {first.sender_idx, first.receiver_idx}
        second = next(
            (c for c in pool[1:] if not {c.sender_idx, c.receiver_idx} & used),
            None)
        if second is None:
            raise RuntimeError("no disjoint second CTX found in extracted pool")
        ctx_broker = CTX(first.ctx_id, first.sender_idx, first.receiver_idx,
                         first.src_shard, first.dst_shard, first.amount_wei,
                         origin=f"eth_mainnet_trace:{first.orig_from}->{first.orig_to}")
        ctx_relay = CTX(second.ctx_id, second.sender_idx, second.receiver_idx,
                        second.src_shard, second.dst_shard, second.amount_wei,
                        origin=f"eth_mainnet_trace:{second.orig_from}->{second.orig_to}")
        for c in (ctx_broker, ctx_relay):
            print(f"    {c.ctx_id}: S{c.src_shard}->S{c.dst_shard} "
                  f"{_fmt_eth(c.amount_wei)} ETH  [{c.origin}]")

        # ---- 3. setup funding (the ONLY sanctioned setBalance block) -----
        broker_addr = users.address(cfg.scale.broker_base_index)
        coord_addr = users.address(cfg.scale.coordinator_index)
        senders = [(ctx_broker.sender_idx, ctx_broker.src_shard, ctx_broker.amount_wei),
                   (ctx_relay.sender_idx, ctx_relay.src_shard, ctx_relay.amount_wei)]
        actor_idx = [cfg.scale.coordinator_index, cfg.scale.broker_base_index,
                     ctx_broker.sender_idx, ctx_broker.receiver_idx,
                     ctx_relay.sender_idx, ctx_relay.receiver_idx]
        shards = range(cfg.chain.num_shards)
        tx.sync_nonces(actor_idx, shards)
        # —— 初始资金布局（全部 setup 期 setBalance，见 tx.py 红线注释）——
        # broker：每分片 100 ETH（两个子账户，真·流动性）；
        # coordinator：relay 签发账户 =【铸造预算】，非流动性（见 brokerchain 模块注释）；
        # sender：金额+1ETH 缓冲（余额恰好等于转账额时任何隐性扣费都会导致 revert，
        #   留缓冲把"余额不足"从本步验证里排除掉）；receiver：刻意保持 0，
        #   收到的每一 wei 都只能来自 Θ2——0→v 是最干净的接收证明。
        for s in shards:
            tx.set_balance_setup(broker_addr, s, cfg.broker_initial_balance_wei)
            tx.set_balance_setup(coord_addr, s, cfg.relay_mint_budget_wei)
        for idx, shard, amt in senders:
            tx.set_balance_setup(users.address(idx), shard, amt + ETH)
        tx.sync_nonces(actor_idx, shards)   # setup 只动余额不动 nonce；再同步一次是幂等防御

        # ---- 4. execute ---------------------------------------------------
        bc = BrokerChain(tx, users, coordinator_index=cfg.scale.coordinator_index,
                         broker_base_index=cfg.scale.broker_base_index)
        actor_addrs = sorted({users.address(i) for i in actor_idx})
        watch = actor_addrs + [broker_addr, coord_addr, BURN_ADDRESS]
        watch = sorted(set(watch))
        # before 快照在 setup 完成之后、执行之前采集 → setup 动作不进差分；
        # gasPrice=0 ⇒ 每个 (地址,分片) 的 after-before 必须精确等于业务量，
        # 任何漂移都是协议 bug，不允许被 gas 噪声掩埋。
        before = _snapshot(tx, watch, shards)

        print("  executing broker-route CTX (Θ1 src->broker, Θ2 broker->receiver) ...")
        r1 = bc.execute(ctx_broker, ROUTE_BROKER, broker_idx=0)
        print("  executing relay-fallback CTX (Θ1 src->BURN, Θ2 coordinator->receiver) ...")
        r2 = bc.execute(ctx_relay, ROUTE_RELAY)
        after = _snapshot(tx, watch, shards)

        # ---- 5. verification ----------------------------------------------
        checks: t.List[t.Tuple[str, int, int]] = []

        def expect(label: str, addr: str, shard: int, delta: int):
            actual = after[(addr, shard)] - before[(addr, shard)]
            checks.append((label, delta, actual))

        a, b = ctx_broker.amount_wei, ctx_relay.amount_wei
        s1, d1, s2, d2 = (ctx_broker.sender_idx, ctx_broker.dst_shard,
                          ctx_relay.sender_idx, ctx_relay.dst_shard)
        s1_sh, s2_sh = ctx_broker.src_shard, ctx_relay.src_shard
        expect("broker Θ1: sender -a",      users.address(s1), s1_sh, -a)
        expect("broker Θ1: broker src +a",  broker_addr, s1_sh, +a)
        expect("broker Θ2: broker dst -a",  broker_addr, d1, -a)
        expect("broker Θ2: receiver +a",    users.address(ctx_broker.receiver_idx), d1, +a)
        expect("relay  Θ1: sender -b",      users.address(s2), s2_sh, -b)
        expect("relay  Θ1: BURN +b",        BURN_ADDRESS, s2_sh, +b)
        expect("relay  Θ2: coordinator -b", coord_addr, d2, -b)
        expect("relay  Θ2: receiver +b",    users.address(ctx_relay.receiver_idx), d2, +b)
        # 全局守恒（burn-and-mint 语义的总证明）：coordinator 累计铸出必须恰等于
        # 各分片 BURN 累计销毁——成立即说明 mint 预算只是销毁额的记账镜像，
        # 而不是像 broker 那样独立消耗、可枯竭的流动性来源。
        total_burned = sum(after[(BURN_ADDRESS, s)] - before[(BURN_ADDRESS, s)] for s in shards)
        total_minted = -sum(after[(coord_addr, s)] - before[(coord_addr, s)] for s in shards)
        checks.append(("global: Σminted == Σburned", total_burned, total_minted))

        print()
        hdr = f"  {'check':<32}{'expected (ETH)':>16}{'actual (ETH)':>15}  result"
        print(hdr); print("  " + "-" * (len(hdr) - 2))
        all_ok = r1.ok and r2.ok
        for label, exp, act in checks:
            ok = exp == act
            all_ok = all_ok and ok
            print(f"  {label:<32}{exp / ETH:>16.4f}{act / ETH:>15.4f}  {'OK' if ok else 'MISMATCH'}")

        def hres(name, res):
            t1, t2 = res.theta1, res.theta2
            print(f"  {name}: {res.route:<6} ok={res.ok}  "
                  f"Θ1 shard{t1.shard} blk{t1.block} {t1.tx_hash[:18]}…  "
                  + (f"Θ2 shard{t2.shard} blk{t2.block} {t2.tx_hash[:18]}…" if t2 else "Θ2 MISSING"))
        print()
        hres(ctx_broker.ctx_id, r1)
        hres(ctx_relay.ctx_id, r2)
        print(f"  {'ALL CHECKS PASSED' if all_ok else 'DEMO FAILED'}")

        # ---- 6. report ----------------------------------------------------
        import dataclasses
        import json
        report = {
            "params": to_params_dict(cfg, extra={
                "anvil_bin": resolve_anvil_bin(cfg.chain.anvil_bin),
                "brokerlab_version": __version__,
                "web3_version": metadata.version("web3"),
            }),
            "ctxs": [
                {"ctx": dataclasses.asdict(ctx_broker), "result": dataclasses.asdict(r1)},
                {"ctx": dataclasses.asdict(ctx_relay), "result": dataclasses.asdict(r2)},
            ],
            "checks": [{"label": l, "expected_wei": e, "actual_wei": a_} for l, e, a_ in checks],
            "balances_wei": {
                "before": {f"{addr}@s{sh}": v for (addr, sh), v in before.items()},
                "after": {f"{addr}@s{sh}": v for (addr, sh), v in after.items()},
            },
            "passed": all_ok,
        }
        (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
        print(f"  report: {run_dir / 'report.json'}")
        failed = not all_ok
    finally:
        # 无论中途抛什么异常都必须收摊；只终止自家 PID（审计 F8：实验室共享
        # 机器上全局 pkill 是事故源），terminate→超时→kill 升级在 AnvilCluster.stop 内。
        cluster.stop()
        print("  anvil cluster stopped (own PIDs only).")
    return 1 if failed else 0


# ===========================================================================
# entry
# ===========================================================================

def main(argv: t.Optional[t.List[str]] = None) -> int:
    """CLI 总入口：解析子命令；demo 分支负责加载配置并应用 --set 覆盖。"""
    ap = argparse.ArgumentParser(prog="brokerlab", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_doc = sub.add_parser("doctor", help="environment check")
    p_doc.add_argument("--config", default=None)

    p_demo = sub.add_parser("demo", help="M1: one real cross-shard transfer")
    p_demo.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "configs" / "smoke.yaml"))
    p_demo.add_argument("--set", action="append", default=[],
                        metavar="section.field=value", dest="overrides")

    args = ap.parse_args(argv)
    if args.cmd == "doctor":
        return cmd_doctor(args.config)
    if args.cmd == "demo":
        cfg = apply_overrides(load_config(args.config), args.overrides)
        return cmd_demo(cfg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
