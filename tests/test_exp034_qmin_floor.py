"""exp034 decisions, integration, and matched workload design."""
import pytest
from brokerlab.tdr_engine import TdrAgent
from brokerlab.tdr_policy import topup_plans, compute_target, BPS_SCALE
from brokerlab.config import load_config, apply_overrides
from experiments.exp025_regime_shift_sensitivity import pipeline, runner
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_epsilon_no_longer_controls_recipient_threshold():
    # 10 < q-floor=15, but 10 > old epsilon shortage threshold=4.
    assert topup_plans([90, 10], [20, 80], .95) == []
    assert topup_plans([90, 10], [20, 80], .95, recipient_floor_wei=15) == [(0, 1, 70)]
    assert topup_plans([90, 10], [20, 80], 0, recipient_floor_wei=15) == [(0, 1, 70)]

def test_recipient_boundary_and_donor_upper_threshold():
    assert topup_plans([85, 15], [20, 80], .95, recipient_floor_wei=15) == []
    # Receiver below floor, donor fails upper threshold.
    assert topup_plans([55, 5], [30, 30], .95, recipient_floor_wei=9) == []
    assert topup_plans([55, 5], [30, 30], 0, recipient_floor_wei=9) == [(0, 1, 25)]

@pytest.mark.parametrize('q', [0, .1, .3, .5, 1])
def test_agent_uses_qmin_floor_and_preserves_capital(q):
    a = TdrAgent(2, policy='topup_qfloor', epsilon=.95, q_min=q,
                 window_blocks=2, chi_blocks=10)
    a.observe(0, 0, 1)
    a.observe(0, 1, 4)
    a.tick(0, [90, 10])
    plans = a.tick(1, [90, 10])
    tau = compute_target([90, 10], [1, 4], q_min=q)
    assert a.last_target == tau
    assert sum(tau) == 100
    floor = 100 * int(q * BPS_SCALE) // (2 * BPS_SCALE)
    if q <= .1 or q == 1:  # at q=1 donor 90 is below the 97 upper threshold
        assert plans == []
    else:
        assert plans
    post = [90, 10]
    for src, dst, amount in plans:
        assert post[dst] < floor
        assert amount > 0
        post[src] -= amount
        post[dst] += amount
    assert sum(post) == 100
    assert min(post) >= 0
    if plans:
        assert a.tick(2, post) == []  # mutual exclusion retained

@pytest.mark.parametrize('kw', [dict(topup_two_sided=True), dict(min_reserve_wei=1), dict(target_cap=.5)])
def test_qfloor_rejects_other_lower_thresholds(kw):
    with pytest.raises(ValueError, match='q_min alone'):
        TdrAgent(2, policy='topup_qfloor', **kw)

def test_new_arm_reaches_agent_policy_and_tasks():
    cfg = load_config(ROOT/'experiments/exp034_qmin_floor/config.yaml')
    import yaml
    space = yaml.safe_load((ROOT/'experiments/exp034_qmin_floor/search_space.yaml').read_text())
    tasks = runner.tasks_for(cfg, 'exp034', space)
    assert len(tasks) == 540
    assert {t['route_seed'] for t in tasks} == set(range(7, 98, 10))
    arm = pipeline._arm_spec('topup_qfloor@45@@0.95@@20@0.3')
    assert arm[1] == 'topup_qfloor'
    assert pipeline._POLICY_TABLE[arm[1]][0] == TdrAgent.POLICY_TOPUP_QFLOOR
    assert Path(cfg.traffic.real_csv_path).name == 'ETH_cleaned.csv'
    assert cfg.exp['shift_intensity'] == 1
    assert cfg.exp['shift_cycle'] == '11,3'

def test_direction_knob_changes_destinations_reproducibly():
    cfg = load_config(ROOT/'experiments/exp034_qmin_floor/config.yaml')
    rows = [dict(ctx_id=i, src_shard=0, dst_shard=1) for i in range(100)]
    def shift(x):
        return pipeline.apply_regime_shift([dict(r) for r in rows],
            apply_overrides(cfg, [f'exp.shift_intensity={x}']))
    assert shift(0) == rows
    assert shift(1) != rows
    assert shift(.5) == shift(.5)
    assert all(r['src_shard'] != r['dst_shard'] for r in shift(1))

@pytest.mark.parametrize('epsilon', [0,.05,.1,.2,.3,.5,.7,1,1.5,2,4,8,16])
def test_qfloor_accepts_entire_epsilon_grid(epsilon):
    agent = TdrAgent(2, policy='topup_qfloor', epsilon=epsilon, q_min=.3,
                     window_blocks=2)
    agent.observe(0, 0, 1)
    agent.observe(0, 1, 4)
    agent.tick(0, [90,10])
    plans = agent.tick(1, [90,10])
    assert bool(plans) == (90 > 20 + int(20*epsilon))

@pytest.mark.parametrize('epsilon', [-.1, float('nan'), float('inf')])
def test_qfloor_rejects_invalid_epsilon(epsilon):
    with pytest.raises(ValueError, match='finite'):
        TdrAgent(2, policy='topup_qfloor', epsilon=epsilon)

@pytest.mark.parametrize('epsilon', [1.5,2,4,8,16])
def test_old_topup_does_not_accept_expanded_epsilon(epsilon):
    with pytest.raises(ValueError, match='epsilon'):
        TdrAgent(2, policy='topup', epsilon=epsilon)

def test_sensitivity_matches_previous_grids_and_fixed_settings():
    import yaml
    space = yaml.safe_load((ROOT/'experiments/exp034_qmin_floor/search_space.yaml').read_text())
    old = yaml.safe_load((ROOT/'experiments/exp025_regime_shift_sensitivity/search_space.yaml').read_text())
    assert space['q_min'] == old['q_min']
    assert space['half_life'] == old['half_life']
    assert space['epsilon']['fixed'] == old['epsilon']['fixed']
    cfg = load_config(ROOT/'experiments/exp034_qmin_floor/config.yaml')
    tasks = runner.tasks_for(cfg, 'exp034', space)
    assert sum(t['sweep']=='epsilon' for t in tasks) == 120
    assert all(t['low_water_ratio'] == .3 for t in tasks if t['sweep'] != 'low_water_ratio')
    assert all(t['token'].startswith('topup_lowwater@') for t in tasks)
    assert len({t['id'] for t in tasks}) == 540
    assert cfg.scale.num_brokers * cfg.exp['ctx_per_broker'] == 40000
    assert cfg.exp.get('sota_training_ctx',0) == 0
