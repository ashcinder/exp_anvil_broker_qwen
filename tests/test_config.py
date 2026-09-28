"""配置系统的单测：加载、校验、--set 覆盖、类型转换、快照。"""
import json

import pytest

from brokerlab import config as C

MINIMAL = """
label: t
chain:
  num_shards: 3
  base_port: 9000
tdr:
  epsilon: 0.2
"""


def _write(tmp_path, text):
    p = tmp_path / "cfg.yaml"
    p.write_text(text)
    return p


def test_load_defaults_and_sections(tmp_path):
    cfg = C.load_config(_write(tmp_path, MINIMAL))
    assert cfg.label == "t"
    assert cfg.chain.num_shards == 3 and cfg.chain.base_port == 9000
    assert cfg.tdr.epsilon == pytest.approx(0.2)
    # untouched fields keep defaults
    assert cfg.tdr.trigger_mode == "excess_only"
    assert cfg.scale.num_brokers == 1


def test_unknown_key_rejected(tmp_path):
    with pytest.raises(C.ConfigError, match="unknown key"):
        C.load_config(_write(tmp_path, "chain:\n  num_shards: 2\n  foo: 1\n"))


def test_index_validation():
    with pytest.raises(C.ConfigError):
        C.validate(C.RunCfg(scale=C.ScaleCfg(coordinator_index=1,
                                             broker_base_index=0)))


def test_overrides_typed():
    cfg = C.RunCfg()
    cfg = C.apply_overrides(cfg, ["tdr.window_blocks=7", "label=x",
                                  "tdr.enabled=true", "tdr.epsilon=0.35"])
    assert cfg.tdr.window_blocks == 7 and isinstance(cfg.tdr.window_blocks, int)
    assert cfg.tdr.enabled is True
    assert cfg.tdr.epsilon == pytest.approx(0.35)
    assert cfg.label == "x"


def test_override_bad_value_or_range():
    with pytest.raises(C.ConfigError):
        C.apply_overrides(C.RunCfg(), ["tdr.window_blocks=abc"])
    with pytest.raises(C.ConfigError):
        C.apply_overrides(C.RunCfg(), ["tdr.epsilon=5"])


def test_exp_section_passthrough_and_override(tmp_path):
    p = _write(tmp_path, MINIMAL + "exp:\n  count: 12\n  balances: \"20,60\"\n")
    cfg = C.load_config(p)
    assert cfg.exp == {"count": 12, "balances": "20,60"}
    cfg2 = C.apply_overrides(cfg, ["exp.count=200", "exp.flag=true", "exp.tag=abc"])
    assert cfg2.exp["count"] == 200          # int 猜测
    assert cfg2.exp["flag"] is True          # bool 猜测
    assert cfg2.exp["tag"] == "abc"          # 回退字符串
    assert cfg2.exp["balances"] == "20,60"   # 未被覆盖的保留原类型


def test_project_root_discovery(tmp_path):
    # experiments/expNNN/config.yaml → 向上找到含 brokerlab/ 或 pyproject.toml 的项目根
    proj = tmp_path / "proj"
    (proj / "brokerlab").mkdir(parents=True)
    cfgdir = proj / "experiments" / "exp007_x"
    cfgdir.mkdir(parents=True)
    p = cfgdir / "config.yaml"
    p.write_text("traffic:\n  kind: real_csv\n  real_csv_path: null\n")
    cfg = C.load_config(p)
    assert cfg.traffic.real_csv_path == str(proj / "trace" / "ETH_cleaned.csv")
    # 相对路径也锚定项目根
    p.write_text("traffic:\n  kind: real_csv\n  real_csv_path: mydata/eth.csv\n")
    cfg = C.load_config(p)
    assert cfg.traffic.real_csv_path == str(proj / "mydata" / "eth.csv")


def test_params_snapshot_roundtrip(tmp_path):
    cfg = C.RunCfg(label="snap")
    out = tmp_path / "params.json"
    C.write_params_json(cfg, out, extra={"anvil_bin": "/usr/bin/anvil"})
    d = json.loads(out.read_text())
    assert d["label"] == "snap"
    assert d["chain"]["block_time_s"] == cfg.chain.block_time_s
    assert d["_runtime"]["anvil_bin"] == "/usr/bin/anvil"


def test_exp005_package_config_loads_and_validates():
    """exp005 真实配置必须能过加载与校验（索引区间、账户覆盖是 50-broker 档的地基）。"""
    from pathlib import Path
    p = (Path(__file__).resolve().parents[1]
         / "experiments/exp005_broker_substrate_benchmark/config.yaml")
    cfg = C.load_config(p)
    assert cfg.scale.num_brokers == 50
    assert cfg.scale.broker_base_index + cfg.scale.num_brokers <= cfg.scale.user_base_index
    assert cfg.scale.user_base_index + cfg.scale.num_users <= cfg.chain.num_funded_accounts
    for k in ("modes", "ctx_per_broker", "pool_scan", "group_cap_mult", "seed",
              "max_inflight", "poll_s", "probe_s", "probe_first_s", "timeout_s",
              "fund_buffer_eth", "broker_buffer_eth", "serial_speedup_min",
              "t_ready_budget_s", "rss_cap_mb", "anchor_dir"):
        assert k in cfg.exp, f"exp: 缺键 {k}"
