"""Independent lower threshold: decisions, event discipline and worker propagation."""
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
from brokerlab.tdr_engine import TdrAgent
from brokerlab.tdr_policy import topup_plans
from brokerlab.config import load_config, apply_overrides
from experiments.exp025_regime_shift_sensitivity import runner, pipeline
ROOT = Path(__file__).resolve().parents[1]

def test_low_water_is_independent_of_q_floor_and_epsilon_lower_band():
    # Under q-floor=.3: floor=15, actual20 is above it. New threshold is .3*80=24.
    assert topup_plans([80,20],[20,80],.95,recipient_floor_wei=15) == []
    assert topup_plans([80,20],[20,80],.95,recipient_ratio=.3) == [(0,1,60)]
    # Change alpha alone; identical target and donor rules.
    assert topup_plans([80,20],[20,80],.95,recipient_ratio=.2) == []
    assert topup_plans([80,20],[20,80],.05,recipient_ratio=.2) == []
    assert topup_plans([80,20],[20,80],.05,recipient_ratio=.3) == [(0,1,60)]

@pytest.mark.parametrize('alpha',[0,.05,.1,.15,.2,.3,.4,.5,.6,.7,.8,.9,1])
def test_agent_uses_alpha_tau_and_preserves_balance(alpha):
    a=TdrAgent(2,policy='topup_lowwater',q_min=.3,epsilon=.95,
               low_water_ratio=alpha,window_blocks=2,chi_blocks=10)
    a.observe(0,0,1);a.observe(0,1,4)
    a.tick(0,[80,20])
    plans=a.tick(1,[80,20])
    assert a.last_target == [20,80]
    assert bool(plans) == (20 < int(80*alpha))
    after=[80,20]
    for src,dst,amount in plans:
        after[src]-=amount;after[dst]+=amount
    assert sum(after)==100 and min(after)>=0
    if plans: assert a.tick(2,after)==[]

def test_boundary_strictness_and_partial_fill():
    assert topup_plans([76,24],[20,80],.95,recipient_ratio=.3)==[]
    plans=topup_plans([50,5,5],[20,50,50],.1,recipient_ratio=.3)
    assert sum(p[2] for p in plans)==30
    assert plans==[(0,1,30)]  # Only available donor excess, no manufactured funds.

@pytest.mark.parametrize('epsilon',[0,.05,.1,.2,.3,.5,.7,1,1.5,2,4,8,16])
def test_lowwater_accepts_epsilon_grid(epsilon):
    a=TdrAgent(2,policy='topup_lowwater',epsilon=epsilon,low_water_ratio=.3)
    assert a.low_water_ratio==.3

@pytest.mark.parametrize('alpha',[-.01,1.01,float('nan'),float('inf')])
def test_invalid_alpha_rejected(alpha):
    with pytest.raises(ValueError,match='low_water_ratio'):
        TdrAgent(2,policy='topup_lowwater',low_water_ratio=alpha)

def test_separate_alpha_grid_and_config_override():
    cfg=load_config(ROOT/'experiments/exp034_qmin_floor/config.yaml')
    space=yaml.safe_load((ROOT/'experiments/exp034_qmin_floor/low_water_space.yaml').read_text())
    tasks=runner.tasks_for(cfg,'exp034',space)
    assert len(tasks)==120
    assert {t['low_water_ratio'] for t in tasks} == set(space['low_water_ratio']['values'])
    assert len({t['tag'] for t in tasks})==12
    task=next(t for t in tasks if t['value']==.7)
    assert runner.task_cfg(cfg,task).exp['low_water_ratio']==.7
    default_space=yaml.safe_load((ROOT/'experiments/exp034_qmin_floor/search_space.yaml').read_text())
    overridden=runner.tasks_for(apply_overrides(cfg,['exp.low_water_ratio=0.5']),'exp034',default_space)
    assert all(t['low_water_ratio']==.5 for t in overridden if t['sweep']!='low_water_ratio')
    assert pipeline._POLICY_TABLE['topup_lowwater'][0]=='topup_lowwater'

def test_worker_receives_lower_parameter(monkeypatch):
    import brokerlab.broker_engine as be
    import brokerlab.tdr_hook as hook
    monkeypatch.setattr(be,'build_services',lambda cfg:(object(),object(),SimpleNamespace(broker_base_index=100)))
    monkeypatch.setattr(hook,'TdrDynamicEngine',lambda *args,**kwargs:kwargs['tdr_agent'])
    payload=dict(cfg={},broker_idx=0,init_wei_by_shard={0:45,1:45},
      params=dict(max_inflight=24,poll_s=.05,probe_s=.2,probe_first_s=.9,engine_timeout_s=150),
      tdr_params=dict(num_shards=2,window_blocks=20,epsilon=16,q_min=.3,
                      chi_blocks=10,timeout_blocks=20,policy='topup_lowwater',low_water_ratio=.7))
    agent=hook.tdr_engine_from_payload(payload,None,None,None,None)
    assert agent.policy=='topup_lowwater' and agent.low_water_ratio==.7 and agent.epsilon==16
