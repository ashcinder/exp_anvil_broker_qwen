import importlib.util
import csv
import json
import sys
from pathlib import Path

import yaml

from brokerlab.config import load_config


ROOT = Path(__file__).resolve().parents[1]
RUN_PATH = ROOT / "experiments" / "exp010_tdr_parameter_search" / "run.py"
SPEC = importlib.util.spec_from_file_location("exp010_run", RUN_PATH)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)


def _inputs():
    base = ROOT / "experiments" / "exp010_tdr_parameter_search"
    cfg = load_config(base / "config.yaml")
    space = yaml.safe_load((base / "search_space.yaml").read_text(encoding="utf-8"))
    return cfg, space


def test_arm_tokens_are_unique_and_normalized():
    cfg, _ = _inputs()
    tokens = MOD.arm_tokens(MOD.base_state(cfg))
    tags = [MOD._tag(token) for token in tokens.values()]
    assert len(tokens) == 5
    assert len(set(tags)) == 5
    assert "tdr@1.0@0.1" in tags
    assert "topup@1.0@0.5@2.0@4.0@0.2" in tags


def test_smoke_oat_plan_deduplicates_shared_baselines():
    cfg, space = _inputs()
    trials = MOD.build_trials(cfg, space, "smoke", "oat",
                              {"epsilon", "hard_epsilon"})
    assert trials
    assert len({t.trial_id for t in trials}) == len(trials)
    assert any(len(t.views) > 1 for t in trials)


def test_search_space_contains_only_requested_dimensions():
    cfg, space = _inputs()
    targets = {spec["target"] for spec in space["dimensions"].values()}
    assert targets == {
        "arm.ewma.epsilon", "arm.ewma.half_life", "arm.all.balance_eth",
        "arm.ewma.q_min",
        "exp.tdr_window_blocks", "arm.valve.cap_mult",
        "arm.proportional.epsilon", "arm.hard.epsilon",
        "traffic.value_floor_eth", "traffic.value_cap_eth",
    }


def test_cartesian_ewma_policy_count():
    cfg, space = _inputs()
    trials = MOD.build_trials(cfg, space, "search", "cartesian", {"ewma_policy"})
    # 5 epsilon × 5 half-life × 4 q_min; baseline is already one grid point.
    assert len(trials) == 100


def test_smoke_grid_has_three_by_three_points():
    cfg, space = _inputs()
    trials = MOD.build_trials(cfg, space, "smoke", "grid",
                              {"epsilon_x_half_life"})
    grid_views = [v for t in trials for v in t.views if v.get("kind") == "grid"]
    assert len(grid_views) == 9
    assert len({v["x_value"] for v in grid_views}) == 3
    assert len({v["y_value"] for v in grid_views}) == 3


def test_injection_rate_is_fixed_not_searched():
    cfg, space = _inputs()
    assert cfg.exp["rate"] == 120
    assert "injection_rate" not in space["dimensions"]
    targets = {spec["target"] for spec in space["dimensions"].values()}
    assert "exp.rate" not in targets


def test_default_search_profile_runs_exactly_1000_ctx_per_arm():
    cfg, space = _inputs()
    profile = space["profiles"][cfg.exp["exp010_profile"]]
    assert cfg.exp["exp010_profile"] == "search"
    assert profile["ctx_per_broker"] * cfg.scale.num_brokers == 1000
    assert profile["use_smoke_values"] is False


def test_baseline_stage_contains_one_five_arm_trial():
    cfg, space = _inputs()
    trials = MOD.build_trials(cfg, space, "search", "baseline")
    assert len(trials) == 1
    assert len(MOD.arm_tokens(trials[0].state)) == 5


def test_arm_cache_key_reuses_unchanged_baselines_but_not_ewma():
    cfg, space = _inputs()
    profile = space["profiles"]["search"]
    trials = MOD.build_trials(cfg, space, "search", "oat", {"epsilon"})
    low = next(t for t in trials if t.state["ewma_epsilon"] == 0.05)
    high = next(t for t in trials if t.state["ewma_epsilon"] == 0.95)
    low_overrides = MOD.common_overrides(low, profile, 7)
    high_overrides = MOD.common_overrides(high, profile, 7)
    low_tokens, high_tokens = MOD.arm_tokens(low.state), MOD.arm_tokens(high.state)
    assert MOD.arm_cache_key(low_tokens["plain"], low_overrides, "fp") == \
        MOD.arm_cache_key(high_tokens["plain"], high_overrides, "fp")
    assert MOD.arm_cache_key(low_tokens["ewma_topup"], low_overrides, "fp") != \
        MOD.arm_cache_key(high_tokens["ewma_topup"], high_overrides, "fp")


def test_common_overrides_force_rate_and_accept_base_port():
    cfg, space = _inputs()
    trial = MOD.build_trials(cfg, space, "search", "oat", {"epsilon"})[0]
    values = MOD.common_overrides(
        trial, space["profiles"]["search"], 17, {"chain.base_port": "9800"})
    assert values["exp.rate"] == 120
    assert values["exp.ctx_per_broker"] == 20
    assert values["chain.base_port"] == "9800"


def test_merge_cached_arm_summaries(tmp_path):
    paths, tags = [], ["plain@1.0", "valve@1.0@1.3"]
    for index, tag in enumerate(tags):
        path = tmp_path / f"source_{index}.json"
        path.write_text(json.dumps({
            "params": {},
            "arms": [{
                "arm": tag,
                "gates": {"G1_all_ok": True},
                "burn_reconcile": {"ok": True},
            }],
            "status": {"complete": True},
        }), encoding="utf-8")
        paths.append(path)
    destination = tmp_path / "merged.json"
    assert MOD.merge_arm_summaries(paths, tags, destination)
    merged = json.loads(destination.read_text(encoding="utf-8"))
    assert [a["arm"] for a in merged["arms"]] == tags
    assert merged["status"]["complete"] is True
    assert merged["status"]["gates_ok"] is True


def test_parameter_analysis_reports_value_impacts_and_ranking(tmp_path):
    rows = []
    for value, ewma_relay, ewma_transfers in ((0.1, 180, 20), (0.2, 80, 35)):
        for role, relay, transfers in (
                ("plain", 240, 0), ("valve", 130, 25),
                ("proportional", 150, 22), ("hard_topup", 120, 28),
                ("ewma_topup", ewma_relay, ewma_transfers)):
            rows.append({
                "trial_id": f"epsilon-{value}", "kind": "oat",
                "name": "epsilon", "label": "EWMA deficit epsilon",
                "x_param": "arm.ewma.epsilon", "x_value": value,
                "role": role, "all_gates_ok": True,
                "n_median": 1000, "relayed_median": relay,
                "events_median": 0 if role == "plain" else 5,
                "relay_plus_events_median": relay + (0 if role == "plain" else 5),
                "relay_plus_transfers_median": relay + transfers,
                "relay_rate_median": relay / 1000,
                "transfers_median": transfers,
                "moved_eth_median": transfers * 0.2,
                "tdr_legs_share_median": transfers / 1000,
                "achieved_injection_ctx_per_s_median": 115,
                "throughput_ctx_per_s_median": 92,
                "e2e_p50_s_median": 1.8, "e2e_p95_s_median": 2.1,
            })
    MOD.write_parameter_analysis(tmp_path, rows)
    impacts = list(__import__("csv").DictReader(
        (tmp_path / "parameter_value_impacts.csv").open(
            newline="", encoding="utf-8-sig")))
    effects = list(__import__("csv").DictReader(
        (tmp_path / "parameter_effects.csv").open(
            newline="", encoding="utf-8-sig")))
    recommended = list(__import__("csv").DictReader(
        (tmp_path / "recommended_policy_values.csv").open(
            newline="", encoding="utf-8-sig")))
    assert len(impacts) == 2
    assert impacts[1]["ewma_relay_rate_improvement_vs_valve"] == "0.05"
    assert impacts[1]["ewma_relay_plus_transfers"] == "115"
    assert effects[0]["parameter"] == "epsilon"
    assert effects[0]["impact_level"] == "large"
    assert effects[0]["best_value_lexicographic"] == "0.2"
    assert recommended[0]["best_value_lexicographic"] == "0.2"


def test_five_way_comparison_counts_relay_and_tdr_separately(tmp_path):
    cfg, _ = _inputs()
    trial = MOD.Trial("counts", MOD.base_state(cfg), {},
                      [{"kind": "baseline", "name": "baseline"}])
    metrics = {
        "plain": (160, 0, 0),
        "valve": (180, 40, 60),
        "proportional": (220, 30, 200),
        "hard_topup": (155, 25, 80),
        "ewma_topup": (130, 20, 90),
    }
    arms = []
    for role, token in MOD.arm_tokens(trial.state).items():
        relay, events, transfers = metrics[role]
        arms.append({
            "arm": MOD._tag(token), "engine": role, "fund_eth": 1,
            "n": 1000, "served": 1000, "relayed": relay, "failed": 0,
            "wall_s": 10, "throughput_ctx_per_s": 100,
            "tdr_close_tail_s": 0,
            "tdr": {"events_opened": events, "transfers_done": transfers,
                    "moved_wei": 0},
            "legs": {"tdr_share_of_ctx_legs": transfers / 1000},
            "coord": {"achieved_injection_ctx_per_s": 120},
            "e2e_secs": {"p50": 1, "p95": 2},
            "gates": {"G1_all_ok": True},
            "burn_reconcile": {"ok": True},
        })
    rep = tmp_path / "trials" / trial.trial_id / "replicate_1"
    rep.mkdir(parents=True)
    summary = rep / "summary.json"
    summary.write_text(json.dumps({"arms": arms}), encoding="utf-8")
    (rep.parent / "status.json").write_text(json.dumps({
        "replicates": [{"replicate": 1, "route_seed": 7,
                        "summary_path": str(summary)}]}), encoding="utf-8")
    MOD.aggregate_records(tmp_path, [trial])
    with (tmp_path / "five_way_comparisons.csv").open(
            newline="", encoding="utf-8-sig") as fh:
        row = next(csv.DictReader(fh))
    assert row["plain_relay"] == "160.0"
    assert row["ewma_topup_events"] == "20.0"
    assert row["ewma_topup_relay_plus_transfers"] == "220.0"
    assert row["relay_winners"] == "ewma_topup"
    assert row["informative_load"] == "True"
    assert row["ewma_unique_relay_winner"] == "True"
    assert row["ewma_unique_combined_winner"] == "False"


def test_no_pressure_cannot_recommend_a_disabled_policy(tmp_path):
    rows = []
    for role in ("plain", "valve", "proportional", "hard_topup", "ewma_topup"):
        rows.append({
            "trial_id": "no-pressure", "kind": "oat", "name": "epsilon",
            "label": "EWMA deficit epsilon", "x_param": "arm.ewma.epsilon",
            "x_value": 0.95, "role": role, "all_gates_ok": True,
            "n_median": 1000, "relayed_median": 0,
            "relay_rate_median": 0, "events_median": 0,
            "transfers_median": 0, "relay_plus_transfers_median": 0,
        })
    MOD.write_parameter_analysis(tmp_path, rows)
    with (tmp_path / "recommended_policy_values.csv").open(
            newline="", encoding="utf-8-sig") as fh:
        assert list(csv.DictReader(fh)) == []
