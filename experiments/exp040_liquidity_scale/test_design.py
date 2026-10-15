"""Meaningful design/accounting tests; no Anvil required."""
import copy
import sys
from pathlib import Path
import pytest
import yaml

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from design import build_tasks, retained_fee, shift_master, verify_and_account, route_bin_statistics
from run import effective_config, resolved_config, backend_root, validate_completed


@pytest.fixture
def config():
    return yaml.safe_load((HERE/'config.yaml').read_text(encoding='utf-8'))


def test_tdr_scan_has_no_plain_runs_and_single_reference(config):
    tasks=build_tasks(config)
    assert len(tasks)==170
    assert sum(t['transactions'] for t in tasks)==10_200_000
    assert len({t['id'] for t in tasks})==170
    for round_no in range(1,11):
        current=[t for t in tasks if t['round']==round_no]
        cells={(t['balance_eth'],t['transactions']) for t in current}
        assert len(cells)==17
        assert {t['route_seed'] for t in current}=={7+(round_no-1)*10}
        for fund,n in cells:
            assert {t['policy'] for t in current if t['balance_eth']==fund and t['transactions']==n}=={'topup'}
        ref=[t for t in current if t['balance_eth']==45 and t['transactions']==40000]
        assert len(ref)==1 and ref[0]['sweeps']==['balance','transactions']
    assert len(build_tasks(config,'balance'))==100
    assert len(build_tasks(config,'transactions'))==80


def test_fig1_is_a_separate_off_only_experiment():
    c=yaml.safe_load((HERE.parent/'exp041_fig1_plain_repeats/config.yaml').read_text(encoding='utf-8'))
    tasks=build_tasks(c)
    assert len(tasks)==10 and sum(t['transactions'] for t in tasks)==400000
    assert {t['policy'] for t in tasks}=={'plain'}
    assert {t['balance_eth'] for t in tasks}=={45}
    assert {t['transactions'] for t in tasks}=={40000}
    assert {t['route_seed'] for t in tasks}==set(range(7,98,10))
    assert all(t['sweeps']==['fig1'] for t in tasks)
    assert all(effective_config(c,t)['tdr']['enabled'] is False for t in tasks)
    with pytest.raises(ValueError):build_tasks(c,'balance')


def test_common_master_design_and_exact_fixed_total_error_caps(config):
    off=yaml.safe_load((HERE.parent/'exp041_fig1_plain_repeats/config.yaml').read_text(encoding='utf-8'))
    for key in ['master_transactions','pool_scan','group_cap_mult','shift_start_index','shift_period_transactions','shift_cycle_absolute']:
        assert off['exp'][key]==config['exp'][key]
    row=lambda block,route: dict(submit_block=block,route=route,ok=True)
    summary,detail=route_bin_statistics([
        [row(1,'broker'),row(2,'broker'),row(21,'relay')],
        [row(1,'relay'),row(2,'relay')]],10)
    assert summary[0]['total_sd']==0
    assert summary[0]['broker_sd']==pytest.approx(2**.5)
    assert summary[0]['relay_sd']==pytest.approx(2**.5)
    assert summary[1]['total_mean']==0
    assert summary[2]['total_mean']==.5
    assert summary[2]['total_sd']==pytest.approx(.5**.5)
    assert sum(r['total_ctx'] for r in detail)==5
    one,_=route_bin_statistics([[row(1,'broker')]],10)
    assert one[0]['broker_sd'] is None


def test_per_arm_settings_really_change_balance_scale_seed(config):
    task=next(t for t in build_tasks(config) if t['transactions']==200000 and t['policy']=='topup' and t['round']==10)
    effective=effective_config(config,task)
    assert effective['exp']['ctx_per_broker']==4000
    assert effective['exp']['route_seed']==97
    assert effective['broker']['initial_balance_eth']==45
    assert effective['exp']['balances_eth']==45
    assert effective['exp']['shift_to_shard']==-1
    high=next(t for t in build_tasks(config) if t['balance_eth']==1000)
    assert effective_config(config,high)['broker']['initial_balance_eth']==1000
    assert config['broker']['initial_balance_eth']==45


def test_nested_prefix_schedule_does_not_scale_with_n():
    rows=[dict(ctx_id=str(i),src_shard=i%3,dst_shard=(i+1)%3,amount_wei=1) for i in range(30)]
    master=shift_master(rows,12,4,[1,2])
    assert master[:10]==shift_master(rows[:10],12,4,[1,2])
    assert all(r['src_shard']!=r['dst_shard'] for r in master)
    assert master[12]['dst_shard']==1
    assert master[16]['dst_shard']==2
    assert rows[16]['dst_shard']==2  # Inputs not mutated.
    assert [r['arrival_pos'] for r in master]==list(range(30))


def evidence(config):
    workload=[dict(ctx_id='a',src_shard=0,dst_shard=1,arrival_pos=0,amount_wei=999),
              dict(ctx_id='b',src_shard=1,dst_shard=0,arrival_pos=1,amount_wei=10**21)]
    fee=retained_fee(config)
    rows=[dict(ctx_id=w['ctx_id'],src=w['src_shard'],dst=w['dst_shard'],arrival_pos=w['arrival_pos'],
               amount_wei=w['amount_wei'],broker_idx=19,route=route,ok='True',fee_broker_wei=charge)
          for w,route,charge in zip(workload,['broker','relay'],[fee,0])]
    stats=dict(n=2,served=1,relayed=1,failed=0,retained_fee_income_wei=fee,coord=dict(aborted=None),
        gates={k:True for k in ['G1_all_ok','G2_no_dup_no_loss','G3a_broker_ledger_eq_chain','G3b_global_net_zero','G4_reports_complete']},
        burn_reconcile=dict(ok=True),tdr=dict(transfers_done=1,moved_wei=100,transfers_lost=0,lost_in_transit_wei=0))
    moves=[dict(broker_idx=19,amount_wei=100,state='done',burn_block=1,mint_block=2)]
    return workload,rows,stats,moves


def test_fixed_fee_upper_includes_relay_excludes_internal_moves_and_principal(config):
    workload,rows,stats,moves=evidence(config)
    final,curve=verify_and_account(config,dict(policy='topup'),stats,rows,moves,workload)
    b=final[19]
    assert b['assigned']==2 and b['served']==1 and b['relay']==1
    assert b['income_wei']==2_100_000 and b['upper_wei']==4_200_000
    assert b['net_profit_wei']==2_100_000-21_000_000
    assert b['income_realization']==.5
    end=[r for r in curve if r['transaction']==2 and r['scope']=='broker_19'][0]
    assert end['upper_wei']==b['upper_wei'] and end['income_wei']==b['income_wei']
    assert final[0]['income_realization'] is None  # No fabricated zero-denominator ratio.


@pytest.mark.parametrize('corruption',['duplicate','workload','income','gate','moves','plain_moves'])
def test_bad_experiments_are_rejected(config,corruption):
    workload,rows,stats,moves=evidence(config);policy='topup'
    if corruption=='duplicate':rows[1]=dict(rows[0])
    if corruption=='workload':rows[0]['amount_wei']=1
    if corruption=='income':rows[1]['fee_broker_wei']=2_100_000
    if corruption=='gate':stats['gates']['G3a_broker_ledger_eq_chain']=False
    if corruption=='moves':moves[0]['amount_wei']=101
    if corruption=='plain_moves':policy='plain'
    with pytest.raises(ValueError):verify_and_account(config,dict(policy=policy),stats,rows,moves,workload)


@pytest.mark.parametrize('key,value',[('balances_grid_eth',[45,45]),('transactions_grid',[10001,40000]),
    ('curve_points',0),('repetitions',0),('max_backlog',100),('tdr_policy','unknown')])
def test_invalid_plan_fails_before_starting_chains(config,key,value):
    config['exp'][key]=value
    with pytest.raises(ValueError):build_tasks(config)
