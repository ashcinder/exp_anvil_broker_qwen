"""配置系统：YAML → 只读 dataclass。设计规则（出处 PLAN_refactor_CN.md）：

- 禁止用环境变量改写模块属性（旧三代的 SWEEP_* 野路子，禁止复活）；
- 字段名必须带单位后缀（`*_eth`、`*_wei`、`*_s`、`*_blocks`）；
- 命令行覆盖走 `--set section.field=value` 点路径，按字段类型转换；
- 未知键直接报错（拼写错误快速暴露，不搞静默忽略）；
- 每次运行把解析后的完整配置写成 params.json 快照（to_params_dict）。
"""
import dataclasses
import json
import typing as t
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

ETH: int = 10**18


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChainCfg:
    anvil_bin: t.Optional[str] = None
    mnemonic: str = "myth like bonus scare over problem client lizard pioneer submit female collect"
    num_shards: int = 2
    base_port: int = 8600
    chain_id_base: int = 43000
    block_time_s: int = 1
    gas_limit: int = 30_000_000
    num_funded_accounts: int = 260
    accounts_balance_eth: int = 0


@dataclass(frozen=True)
class ScaleCfg:
    coordinator_index: int = 1
    broker_base_index: int = 100
    num_brokers: int = 1
    user_base_index: int = 200
    num_users: int = 50


@dataclass(frozen=True)
class BrokerCfg:
    initial_balance_eth: float = 100.0
    # Relay = burn-and-mint：这不是流动性，而是【铸造预算】。EVM 交易无法凭空
    # 造钱，mint 段由签发账户（coordinator，代表目的分片委员会按协议铸币）用
    # 真实转账执行；其支出恒等于各源分片 BURN 额（demo 不变量校验），耗尽属于
    # 配置错误而非协议结果。命名刻意避开 pool/fund 以免与 broker 流动性混淆。
    relay_mint_budget_eth: int = 1000


@dataclass(frozen=True)
class TrafficCfg:
    kind: str = "real_csv"          # real_csv | synthetic (M-later)
    real_csv_path: t.Optional[str] = None
    # True 时允许以合约地址作为流量端点；实验仍只重放地址、方向与 ETH value，
    # 不执行原始 calldata/合约逻辑。默认 False 保持旧版纯 EOA→EOA 口径。
    allow_contract_endpoints: bool = False
    value_floor_eth: float = 0.01
    value_cap_eth: float = 10.0
    max_extract: int = 50


@dataclass(frozen=True)
class TDRCfg:
    enabled: bool = False
    trigger_mode: str = "excess_only"   # excess_only (Go reference) | two_sided
    window_blocks: int = 10
    epsilon: float = 0.10
    q_min: float = 0.10
    chi_blocks: int = 5
    timeout_blocks: int = 5


@dataclass(frozen=True)
class B2ECfg:
    """B2E（Broker2Earn）手续费，见 PLAN_b2e_CN.md v2 定案。

    费用基数 F = gas_units_per_ctx × ref_gas_price_wei（名义单价，固定值；
    链上 gasPrice 仍为 0——真实 gas 经济不建模，费用全部用显式转账表达）。
    broker 服务一笔 CTX：βF 随 Θ1a 进 broker，(1−β)F 由 Θ1b 小额交易烧掉；
    relay 回退：v+F 全额进 BURN。enabled=false 时机制层逐字节走旧路径。
    命名 β 与 TDR 的余额记号 β_s 无关（那是 M4 的事，两节各解释各的）。"""
    enabled: bool = False
    fee_share: float = 0.10              # β ∈ [0,1]
    gas_units_per_ctx: int = 21000       # 一笔 CTX 的 gas 成本基数
    ref_gas_price_wei: int = 1_000_000_000   # 名义 gas 单价（1 gwei），无浮动机制
    charges_rebalancing: bool = False    # TDR 再平衡交易是否同样付费（M4 启用）


# 费用拆分用纯整数运算：β 先化成 9 位小数的定点数再乘除，
# 避开浮点误差把 floor 抖偏 1 wei 的经典坑；恒有 fee_broker + fee_burn == F。
BETA_SCALE = 10 ** 9


def b2e_fees(cfg: "RunCfg") -> t.Tuple[int, int, int]:
    """返回 (F, fee_broker, fee_burn)，单位 wei；未启用时全 0。"""
    b = cfg.b2e
    if not b.enabled:
        return 0, 0, 0
    f = int(b.gas_units_per_ctx) * int(b.ref_gas_price_wei)
    beta_f = int(round(b.fee_share * BETA_SCALE))
    fee_broker = f * beta_f // BETA_SCALE
    return f, fee_broker, f - fee_broker


@dataclass(frozen=True)
class OutputCfg:
    results_root: str = "results"


@dataclass(frozen=True)
class RunCfg:
    label: str = "smoke"
    # 实验自定义参数区（per-experiment knobs: count/balances/pairs/…）。
    # schema-free 直通 dict，YAML 里写 `exp:` 段；覆盖用 --set exp.<key>=<value>。
    # 结构化参数仍归各 section——exp: 只放实验层自由量。
    exp: t.Dict[str, t.Any] = field(default_factory=dict)
    chain: ChainCfg = field(default_factory=ChainCfg)
    scale: ScaleCfg = field(default_factory=ScaleCfg)
    broker: BrokerCfg = field(default_factory=BrokerCfg)
    traffic: TrafficCfg = field(default_factory=TrafficCfg)
    tdr: TDRCfg = field(default_factory=TDRCfg)
    b2e: B2ECfg = field(default_factory=B2ECfg)
    output: OutputCfg = field(default_factory=OutputCfg)

    # convenience ---------------------------------------------------------
    @property
    def broker_initial_balance_wei(self) -> int:
        return int(self.broker.initial_balance_eth * ETH)

    @property
    def relay_mint_budget_wei(self) -> int:
        return int(self.broker.relay_mint_budget_eth * ETH)


_SECTIONS: t.Dict[str, t.Any] = {
    "chain": ChainCfg,
    "scale": ScaleCfg,
    "broker": BrokerCfg,
    "traffic": TrafficCfg,
    "tdr": TDRCfg,
    "b2e": B2ECfg,
    "output": OutputCfg,
}


def _coerce_guess(raw: str) -> t.Any:
    """--set exp.<k>=v 的类型猜测（exp 区无 schema 可依）。"""
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _project_root(cfg_file: Path) -> Path:
    """向上找含 brokerlab/ 或 pyproject.toml 的目录 = 项目根。
    兼容 configs/*.yaml 与 experiments/*/config.yaml 两类位置，
    trace/ 等相对路径永远锚定项目根 → 实验目录可整体搬家。"""
    for anc in (cfg_file.parent, *cfg_file.parents):
        if (anc / "brokerlab").is_dir() or (anc / "pyproject.toml").exists():
            return anc
    return cfg_file.parent.parent


# ---------------------------------------------------------------------------
# Loading / coercion
# ---------------------------------------------------------------------------

def _base_type(tp: t.Any) -> t.Any:
    """把 Optional[X] 拆成 X；其他类型原样返回（供 _coerce 判断用）。"""
    args = t.get_args(tp)
    if args and type(None) in args:
        remaining = [a for a in args if a is not type(None)]
        if len(remaining) == 1:
            return remaining[0]
    return tp


def _coerce(tp: t.Any, raw: t.Any, where: str):
    if raw is None:
        if type(None) in t.get_args(tp):
            return None
        raise ConfigError(f"{where}: null not allowed for {tp}")
    base = _base_type(tp)
    if base is bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.lower() in ("true", "false"):
            return raw.lower() == "true"
        raise ConfigError(f"{where}: expected bool, got {raw!r}")
    if base is int:
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ConfigError(f"{where}: expected int, got {raw!r}") from None
    if base is float:
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ConfigError(f"{where}: expected float, got {raw!r}") from None
    if base is str:
        return str(raw)
    raise ConfigError(f"{where}: unsupported type {tp}")


def _build_section(cls, data: t.Dict[str, t.Any], path: str):
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected mapping, got {type(data).__name__}")
    known = {f.name: f for f in fields(cls)}
    for k in data:
        if k not in known:
            raise ConfigError(f"{path}.{k}: unknown key (allowed: {sorted(known)})")
    kwargs = {
        name: _coerce(f.type, data[name], f"{path}.{name}")
        for name, f in known.items() if name in data
    }
    return cls(**kwargs)


def load_config(path: t.Union[str, Path]) -> RunCfg:
    p = Path(path).resolve()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{p}: top level must be a mapping")
    for k in raw:
        if k not in ("label", "exp") and k not in _SECTIONS:
            raise ConfigError(f"unknown section {k!r} (allowed: label, exp, {sorted(_SECTIONS)})")
    exp = raw.get("exp") or {}
    if not isinstance(exp, dict):
        raise ConfigError("exp: must be a mapping of experiment-local params")
    cfg = RunCfg(
        label=str(raw.get("label", "smoke")),
        exp=dict(exp),
        **{sec: _build_section(cls, raw.get(sec, {}), sec)
           for sec, cls in _SECTIONS.items()},
    )
    # trace 数据默认在 <project root>/trace/：real_csv_path 为 null 取默认值，
    # 相对路径一律按项目根解析（实验目录搬家、机器迁移都不改配置）。
    if cfg.traffic.kind == "real_csv":
        project_root = _project_root(p)
        default = project_root / "trace" / "ETH_cleaned.csv"
        rp = cfg.traffic.real_csv_path
        resolved = str(default if not rp else
                       (Path(rp) if Path(rp).is_absolute()
                        else project_root / rp))
        cfg = dataclasses.replace(
            cfg, traffic=dataclasses.replace(cfg.traffic, real_csv_path=resolved))
    validate(cfg)
    return cfg


# ---------------------------------------------------------------------------
# --set overrides
# ---------------------------------------------------------------------------

def apply_overrides(cfg: RunCfg, overrides: t.List[str]) -> RunCfg:
    """应用 `section.field=value`（或顶层 `label=...`）形式的命令行覆盖项。"""
    for item in overrides:
        if "=" not in item:
            raise ConfigError(f"--set expects key=value, got {item!r}")
        dotted, raw = item.split("=", 1)
        parts = dotted.split(".")
        if parts[0] == "label" and len(parts) == 1:
            cfg = dataclasses.replace(cfg, label=raw)
            continue
        if parts[0] == "exp" and len(parts) == 2:
            cfg = dataclasses.replace(cfg, exp={**cfg.exp, parts[1]: _coerce_guess(raw)})
            continue
        if len(parts) != 2 or parts[0] not in _SECTIONS:
            raise ConfigError(f"--set {dotted}: expected section.field")
        sec, key = parts
        cls = _SECTIONS[sec]
        known = {f.name: f for f in fields(cls)}
        if key not in known:
            raise ConfigError(f"--set {dotted}: unknown key (allowed: {sorted(known)})")
        section = getattr(cfg, sec)
        value = _coerce(known[key].type, raw, dotted)
        cfg = dataclasses.replace(cfg, **{sec: dataclasses.replace(section, **{key: value})})
    validate(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Validation (fail fast, before any anvil process exists)
# ---------------------------------------------------------------------------

def validate(cfg: RunCfg) -> None:
    c, s, td = cfg.chain, cfg.scale, cfg.tdr
    if c.num_shards < 1:
        raise ConfigError("chain.num_shards must be >= 1")
    if c.base_port + c.num_shards > 65535:
        raise ConfigError("chain.base_port + num_shards exceeds port space")
    if c.block_time_s < 1:
        raise ConfigError("chain.block_time_s must be >= 1 (anvil minimum)")
    if s.user_base_index + s.num_users > c.num_funded_accounts:
        raise ConfigError(
            "user range exceeds chain.num_funded_accounts "
            f"({s.user_base_index + s.num_users} > {c.num_funded_accounts})")
    if not (s.coordinator_index < s.broker_base_index < s.user_base_index):
        raise ConfigError("index ranges must satisfy coordinator < broker_base < user_base")
    if s.broker_base_index + s.num_brokers > s.user_base_index:
        raise ConfigError("broker range overlaps user range")
    if td.trigger_mode not in ("excess_only", "two_sided"):
        raise ConfigError(f"tdr.trigger_mode invalid: {td.trigger_mode!r}")
    be = cfg.b2e
    if not 0 <= be.fee_share <= 1:
        raise ConfigError("b2e.fee_share must be in [0, 1]")
    if be.gas_units_per_ctx <= 0:
        raise ConfigError("b2e.gas_units_per_ctx must be > 0")
    if be.ref_gas_price_wei < 0:
        raise ConfigError("b2e.ref_gas_price_wei must be >= 0")
    if not 0 <= td.epsilon < 1:
        raise ConfigError("tdr.epsilon must be in [0, 1)")
    if not 0 <= td.q_min <= 1:
        raise ConfigError("tdr.q_min must be in [0, 1]")
    if td.window_blocks < 1 or td.chi_blocks < 0 or td.timeout_blocks < 1:
        raise ConfigError("need tdr.window_blocks >= 1, chi_blocks >= 0, timeout_blocks >= 1")
    # NOTE: traffic.real_csv_path presence is a RUNTIME requirement
    # (checked by the demo/run entry), not a structural one.


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def to_params_dict(cfg: RunCfg, extra: t.Optional[t.Dict[str, t.Any]] = None) -> t.Dict[str, t.Any]:
    d = dataclasses.asdict(cfg)
    if extra:
        d["_runtime"] = extra
    return d


def write_params_json(cfg: RunCfg, path: t.Union[str, Path],
                      extra: t.Optional[t.Dict[str, t.Any]] = None) -> None:
    Path(path).write_text(json.dumps(to_params_dict(cfg, extra), indent=2, ensure_ascii=False))
