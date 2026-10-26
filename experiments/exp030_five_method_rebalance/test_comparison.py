"""Meaningful invariants: verified LAI recurrence, binding capacity, causal audit."""
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from brokerlab.config import load_config
from experiments.exp030_five_method_rebalance.policies import ComparisonAgent,lai_coefficients,lai_step,integer_allocation,allocation_plan
from experiments.exp030_five_method_rebalance.run import tasks_for
from experiments.exp030_five_method_rebalance.reporting import aggregate
from experiments.exp030_five_method_rebalance.design import evaluation_metrics,review_design
from experiments.exp030_five_method_rebalance.reporting import build_report,read_csv


def test_lai_paper_recurrence_terminal_and_stationary_gamma_one():
    from decimal import Decimal
    for a in ('0.01','1','10'):
        c=lai_coefficients(a,1,8)
        assert all(abs(v-c[0])<Decimal('1e-45') for v in c)
        c=lai_coefficients(a,0,8)
        assert abs(c[-1]-1/(1+Decimal(a)))<Decimal('1e-25')
        assert all(abs(1/c[t]-(2+Decimal(a)-c[t+1]))<Decimal('1e-24') for t in range(7))


def test_lai_continuous_action_and_conservative_integer_transport():
    from decimal import Decimal
    c=lai_coefficients(1,1,1)[0]
    previous=[Decimal('0.25')]*4
    demand=[Decimal(0),Decimal(1),Decimal(0),Decimal(0)]
    x=lai_step(previous,demand,c)
    assert abs(sum(x)-1)<Decimal('1e-26')
    assert abs(x[1]-(1-Decimal('0.75')*c))<Decimal('1e-26')
    for av in ([0,3,6,7],[2,3,11,20],[10**22,0,1,9]):
        target=integer_allocation(x,sum(av));plans=allocation_plan(av,target)
        result=list(av)
        for src,dst,amt in plans:result[src]-=amt;result[dst]+=amt
        assert result==target and min(result)>=0 and len(plans)<=len(av)-1
        for i in range(4):assert sum(v for src,_,v in plans if src==i)<=av[i]


def agent(method,**kw):
    return ComparisonAgent(4,method=method,initial_wei=100,chi_blocks=3,**kw)


def close(a):
    for i in range(len(a.active)):
        a.mark_first_confirmed(i)
        a.mark_second_confirmed(i)


def test_horcrux_only_compensates_confirmed_service_and_excludes_overlap():
    a=agent("horcrux")
    a.note_ctx("x",1,0,1,40)
    assert a.tick(1,[100]*4)==[]
    a.note_settled(0,1,40)
    assert a.tick(2,[120,60,100,100])==[(0,1,40)]
    assert a.tick(3,[120,60,100,100])==[]
    close(a)
    assert a.tick(4,[100]*4)==[]
    assert a.compensation[(0,1)]==0
    assert a.event_ctx_links==[dict(event_id=1,ctx_id="x",observed_block=1,decision_block=2)]


def test_shaduf_binding_limits_not_reset_and_reverse_capacity_increases():
    a=agent("shaduf")
    a.note_ctx("x",1,0,1,80)
    plans=a.tick(1,[100,0,100,100])
    assert sum(v for _,dst,v in plans if dst==1)==80
    assert all(v<=33 for _,_,v in plans)
    before=dict(a.bindings)
    close(a)
    for src,dst,v in plans:
        assert a.bindings[(src,dst)]==before[(src,dst)]-v
        assert a.bindings[(dst,src)]==before[(dst,src)]+v


def test_event_reference_never_uses_future_observation():
    a=agent("lai")
    a.note_ctx("future",9,0,1,100)
    a.note_ctx("known",1,0,1,10)
    assert a.tick(2,[100,0,100,100])
    assert [r["ctx_id"] for r in a.event_ctx_links]==["known"]


def test_shaduf_planned_incoming_cannot_finance_another_burn():
    a=agent("shaduf")
    a.note_ctx("a",1,0,1,10)
    a.note_ctx("b",1,0,2,10)
    av=[5,0,1,8]
    plans=a.tick(1,av)
    for shard in range(4):
        assert sum(v for src,_,v in plans if src==shard)<=av[shard]


def test_shaduf_ignores_expired_out_of_order_observations():
    a=agent("shaduf",window_blocks=3)
    a.note_ctx("new",9,0,1,1)
    a.note_ctx("old",1,0,2,99)
    assert a.tick(9,[10,10,10,10])==[]


def test_paired_whole_runs_and_no_fake_single_run_ci():
    cfg=load_config(Path(__file__).with_name("config.yaml"))
    tasks=tasks_for(cfg)
    assert len(tasks)==15
    for rep in range(1,4):
        group=[t for t in tasks if t["round"]==rep]
        assert len(group)==5 and len({t["route_seed"] for t in group})==1
    out=aggregate([dict(method="tdr",round=1,relay_ctx=12)])
    assert out[0]["ci95_low"] is None


def test_primary_metric_counts_one_transfer_once_and_profit_for_all_brokers():
    # 10 adjustment CTXs have 20 chain legs. Only 10 belongs in the score.
    m=evaluation_metrics(100,10,120_000_000,21_000_000,4)
    assert m['relay_plus_adjustment_ctx']==110
    assert m['broker_total_profit_wei']==-90_000_000
    assert m['broker_mean_profit_wei']=='-22500000'
    assert evaluation_metrics(200,0,80,21,4)['relay_plus_adjustment_ctx']==200


def test_review_matches_config_and_uses_changed_parameters():
    from brokerlab.config import apply_overrides
    cfg=load_config(Path(__file__).with_name('config.yaml'))
    cfg=apply_overrides(cfg,['exp.tdr_epsilon=0.7','exp.tdr_q_min=0.2','exp.tdr_ewma_half_life=30'])
    d=review_design(cfg)
    assert d['methods']['tdr']['epsilon']==0.7
    assert d['methods']['tdr']['q_min']==0.2
    assert d['methods']['tdr']['ewma_half_life_blocks']==30
    assert d['common']['user_ctx_per_method']==40000
    assert d['common']['total_initial_broker_eth']=='36000'
    assert d['economics']['broker_retained_fee_per_service_wei']==2100000


def test_primary_report_and_analysis_include_composite_metric(tmp_path):
    import json
    from experiments.exp030_five_method_rebalance.analysis.analyze import plot
    cfg=load_config(Path(__file__).with_name('config.yaml'))
    tasks=[]
    # Synthetic arithmetic fixture only; no chains or experimental claims.
    for method,relay,adjustment in [('brokerchain',40,0),('horcrux',20,30),
                                    ('shaduf',25,20),('lai',10,50),('tdr',15,10)]:
        income=(100-relay)*2100000; cost=adjustment*21000000
        stats=dict(n=100,served=100-relay,relayed=relay,failed=0,retained_fee_income_wei=income,
                   tdr=dict(events_opened=1 if adjustment else 0,transfers_done=adjustment,moved_wei=adjustment),
                   legs=dict(ctx_legs=300-relay,tdr_legs=2*adjustment),throughput_ctx_per_s=1,
                   e2e_secs=dict(p50=2,p95=3),wall_ctx_s=100,
                   audit=dict(broker_count=50,nominal_cost_per_burn_wei=21000000,
                              trigger_reference_ctx_count=999,confirmed_onchain_legs=300-relay+2*adjustment,
                              onchain_gas_used=1,rebalance_gas_used=1,modeled_rebalance_cost_wei=cost,
                              modeled_net_profit_wei=income-cost,actual_chain_gas_cost_wei=0,
                              rebalance_burn_legs=adjustment,decision_time_ms=1))
        path=tmp_path/f'{method}.json'
        path.write_text(json.dumps({'stats':stats}),encoding='utf-8')
        tasks.append(dict(method=method,round=1,route_seed=7,status='complete',summary=path.name))
    assert build_report(tmp_path,dict(tasks=tasks,pilot=True),cfg)
    rows=read_csv(tmp_path/'primary_metrics.csv')
    assert {r['method']:int(r['relay_plus_adjustment_ctx']) for r in rows}=={
        'brokerchain':40,'horcrux':50,'shaduf':45,'lai':60,'tdr':25}
    assert 'trigger_reference_ctx' not in rows[0]
    report=json.loads((tmp_path/'report.json').read_text(encoding='utf-8'))
    plot(tmp_path,report['raw_results'])
    assert (tmp_path/'comparison.png').stat().st_size>0
