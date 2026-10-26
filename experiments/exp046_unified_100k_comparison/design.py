"""Fixed design for five methods x three actual 100k-CTX runs."""
from __future__ import annotations
import copy
import csv
import hashlib
import json
from pathlib import Path

PROTOCOL='exp046-five-methods-100k-v1'
METHODS={
    'autoloop':dict(label='AutoLoop',engine='autoloop',epsilon=.95),
    'spider':dict(label='Spider-rebalance',engine='spider',epsilon=.95),
    'prop_ewma':dict(label='Proportional + EWMA',engine='proportional',epsilon=.95),
    'topup_e0':dict(label='Top-up + EWMA (epsilon=0)',engine='topup',epsilon=0.),
    'topup':dict(label='TDR (Top-up + EWMA)',engine='topup',epsilon=.95),
}
FEE_WEI=2_100_000
COST_WEI=21_000_000

def read_json(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def write_json(p,value):
    p=Path(p);q=p.with_suffix(p.suffix+'.tmp')
    q.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8');q.replace(p)
def read_csv(p):
    with Path(p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write_csv(p,rows):
    rows=list(rows)
    if not rows:return
    p=Path(p);q=p.with_suffix(p.suffix+'.tmp')
    with q.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    q.replace(p)
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def fingerprint(root):return {p.relative_to(root).as_posix():sha(p) for p in sorted(Path(root).rglob('*.py'))}
def verify_hashes(root,hashes):
    for rel,h in hashes.items():
        if sha(Path(root)/rel)!=h:raise ValueError('Changed frozen file: '+str(Path(root)/rel))

def validate(c,smoke=False):
    e=c['exp'];n=e['transactions'];nb=c['scale']['num_brokers'];ns=c['chain']['num_shards']
    if c['label']!='exp046_unified_100k_comparison':raise ValueError('Wrong experiment label')
    if not smoke and (n!=100000 or e['repetitions']!=3 or nb!=50 or ns!=16 or c['scale']['num_users']!=200):
        raise ValueError('Formal protocol requires 100000 CTX, 3 rounds, 50 brokers, 16 shards, 200 users')
    if n<=0 or n%nb or e['ctx_per_broker']*nb!=n:raise ValueError('CTX count must match brokers x ctx_per_broker')
    if list(e['methods'])!=list(METHODS):raise ValueError('Keep the exact five-method design')
    required=dict(balances_eth=45,tdr_epsilon=.95,tdr_q_min=.3,tdr_ewma_half_life=20,
        tdr_chi_blocks=10,tdr_window_blocks=20,tdr_timeout_blocks=20,tdr_ewma_two_sided=False,
        sota_autoloop_low=.3,sota_autoloop_high=.7,sota_capacity_mult=2.,sota_check_blocks=10,
        sota_spider_vol_mult=.25,route_seed=7,route_seed_step=10,modeled_tdr_cost_wei=COST_WEI)
    if any(e.get(k)!=v for k,v in required.items()):raise ValueError('Fixed policy parameters changed')
    if c['broker']['initial_balance_eth']!=45:raise ValueError('Requires 45 ETH per sub-account')
    if not c['b2e']['enabled'] or c['b2e']['charges_rebalancing']:raise ValueError('Use identical service fees for all five methods')
    b=c['b2e']
    if b['gas_units_per_ctx']!=21000 or b['ref_gas_price_wei']!=1000 or b['fee_share']!=.1:
        raise ValueError('Fixed service income must be 2100000 wei = .0021 Gwei')
    if e['max_backlog']<n:raise ValueError('Backlog must cover the workload')
    if len(set(e['shift_cycle_absolute']))<2 or any(s<0 or s>=ns for s in e['shift_cycle_absolute']):
        raise ValueError('Invalid shard shift cycle')
    if e['shift_period_transactions']<=0 or not 0<=e['shift_start_index']<n:raise ValueError('Invalid shift schedule')

def build_tasks(c,smoke=False):
    validate(c,smoke);tasks=[];e=c['exp'];methods=list(METHODS)
    for rep in range(1,e['repetitions']+1):
        order=methods[rep-1:]+methods[:rep-1]
        for arm in order:
            tasks.append(dict(id=f'r{rep:02d}_n{e["transactions"]}_{arm}',round=rep,
                route_seed=e['route_seed']+(rep-1)*e['route_seed_step'],policy=arm,
                engine=METHODS[arm]['engine'],balance_eth=45,transactions=e['transactions'],status='pending',attempts=[]))
    return tasks

def effective(c,t,base_port,anvil):
    d=copy.deepcopy(c);e=d['exp'];spec=METHODS[t['policy']]
    if t['engine']!=spec['engine']:raise ValueError('Method/engine mismatch; no fallback allowed')
    e.update(arms=t['policy'],route_seed=t['route_seed'],balances_eth=45,
        ctx_per_broker=t['transactions']//d['scale']['num_brokers'],tdr_epsilon=spec['epsilon'],shift_to_shard=-1)
    d['tdr'].update(enabled=True,epsilon=spec['epsilon'],q_min=.3,chi_blocks=10,window_blocks=20,timeout_blocks=20)
    d['chain'].update(base_port=base_port,anvil_bin=anvil)
    return d

def summarize(c,rows,moves,finals):
    nb=c['scale']['num_brokers'];billing={i:0 for i in range(nb)};control=0;relay=0
    for r in rows:
        amount=int(r['xmit_bytes']);i=int(r['broker_idx'])
        if r['route']=='broker':
            if amount!=100:raise ValueError('Broker billing must be 100 B/CTX')
            control+=amount
        else:
            if amount!=int(r['t1_tx_bytes'])+int(r['t1_receipt_bytes']):raise ValueError('Relay byte accounting mismatch')
            relay+=amount
        billing[i]+=amount
    for a in finals:
        a.update(user_ctx_communication_bytes=billing[int(a['broker_idx'])],profit_gwei=int(a['net_profit_wei'])/1e9)
    if sum(int(a['tdr_transfers']) for a in finals)!=len(moves):raise ValueError('Transfer count mismatch')
    totals={k:sum(int(a[k]) for a in finals) for k in ['assigned','served','relay','tdr_transfers','income_wei','modeled_cost_wei','net_profit_wei']}
    totals.update(broker_control_bytes=control,relay_user_ctx_bytes=relay,
        user_ctx_communication_bytes=control+relay,user_ctx_communication_MB=(control+relay)/1e6,
        profit_gwei=totals['net_profit_wei']/1e9)
    return totals
