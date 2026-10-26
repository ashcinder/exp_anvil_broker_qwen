"""Pure planning and conservation checks for paired EWMA figure repetitions."""
import csv,hashlib,json
from collections import Counter
from pathlib import Path

REVISION='exp042-ewma-completion-index-v1'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):
    with Path(p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write(p,rows):
    rows=list(rows)
    with Path(p).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def save(p,obj):
    p=Path(p);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(p)
def plan(c):
    s,e=c['study'],c['exp'];n=c['scale']['num_brokers']*e['ctx_per_broker']
    if s['rounds']<2:raise ValueError('At least two rounds are required for a sample SD')
    if not 0<=s['broker_idx']<c['scale']['num_brokers']:raise ValueError('Invalid selected broker')
    if e['tdr_ewma_half_life']<=0:raise ValueError('EWMA must be enabled')
    if not c['b2e']['enabled'] or c['b2e']['fee_share']!=.1:raise ValueError('This study requires enabled fixed 10% retained fees')
    if s['tdr_cost_wei']<0:raise ValueError('Negative TDR cost')
    if s['fig1_bin_blocks']<=0 or s['fig1_end_block']<=0 or s['fig1_end_block']%s['fig1_bin_blocks']:raise ValueError('Invalid Fig1 block bins')
    result=[]
    for r in range(1,s['rounds']+1):
        arms=['plain','topup'] if r%2 else ['topup','plain']
        for arm in arms:
            result.append(dict(id=f'r{r:02d}_{arm}',round=r,route_seed=e['route_seed']+(r-1)*e['route_seed_step'],arm=arm,n=n,status='pending',attempts=[]))
    return result
def audit_task(c,task,arm_dir,stats,workload,out):
    rows=read(arm_dir/'ctx_rows.csv');moves=read(arm_dir/'tdr_moves.csv')
    expected={r['ctx_id']:r for r in workload};n=task['n']
    assert len(rows)==len(expected)==n and len({r['ctx_id'] for r in rows})==n
    assert all(stats['gates'].values()) and stats['burn_reconcile']['ok'] and stats['failed']==0
    assert not stats['tdr']['transfers_lost'] and not stats['tdr']['lost_in_transit_wei']
    assert sorted(int(r['accounting_index']) for r in rows)==list(range(1,n+1))
    fee=c['b2e']['gas_units_per_ctx']*c['b2e']['ref_gas_price_wei']//10
    cost=c['study']['tdr_cost_wei'];b=c['study']['broker_idx']
    for r in rows:
        e=expected[r['ctx_id']]
        for x,y in [('arrival_pos','arrival_pos'),('src','src_shard'),('dst','dst_shard'),('amount_wei','amount_wei')]:assert int(r[x])==int(e[y])
        assert r['ok'].lower()=='true' and r['route'] in ('broker','relay')
        assert int(r['fee_broker_wei'])==(fee if r['route']=='broker' else 0)
    assert sum(r['route']=='broker' for r in rows)==stats['served']
    assert sum(int(r['fee_broker_wei']) for r in rows)==stats['retained_fee_income_wei']
    events=[]
    for f in sorted((arm_dir/'accounting').glob('*_costs.csv')):events.extend(read(f))
    assert len(events)==len(moves)==stats['tdr']['transfers_done']
    assert all(r['state']=='done' for r in moves)
    assert len({(r['broker_idx'],r['transfer_hash']) for r in events})==len(events)
    identity=lambda r:(int(r['broker_idx']),int(r['src_shard']),int(r['dst_shard']),int(r['amount_wei']),int(r['burn_block']))
    assert Counter(map(identity,events))==Counter(map(identity,moves))
    assert all(0<=int(r['transaction_index'])<=n and int(r['cost_wei'])==cost for r in events)
    if task['arm']=='plain':assert not events
    ordered=sorted(rows,key=lambda r:int(r['accounting_index']))
    charges=Counter()
    for r in events:
        if int(r['broker_idx'])==b:charges[int(r['transaction_index'])]+=int(r['cost_wei'])
    assigned=served=income=0;expense=charges[0]
    curve=[dict(transaction=0,assigned=0,served=0,income_wei=0,cost_wei=expense,profit_wei=-expense,upper_wei=0)]
    for k,r in enumerate(ordered,1):
        if int(r['broker_idx'])==b:
            assigned+=1;served+=r['route']=='broker';income+=int(r['fee_broker_wei'])
        expense+=charges[k]
        curve.append(dict(transaction=k,assigned=assigned,served=served,income_wei=income,cost_wei=expense,profit_wei=income-expense,upper_wei=assigned*fee))
    assert expense==sum(int(r['broker_idx'])==b for r in moves)*cost
    write(out/'curve.csv',curve)
    bins=[]
    for start in range(0,c['study']['fig1_end_block'],c['study']['fig1_bin_blocks']):
        rs=[r for r in rows if start<=int(r['submit_block'])<start+c['study']['fig1_bin_blocks']]
        broker=sum(r['route']=='broker' for r in rs)
        bins.append(dict(start=start,end=start+c['study']['fig1_bin_blocks'],broker=broker,relay=len(rs)-broker,total=len(rs)))
    write(out/'bins.csv',bins)
    artifacts={str(f.relative_to(out)):sha(f) for f in [arm_dir/'ctx_rows.csv',arm_dir/'tdr_moves.csv',out/'curve.csv',out/'bins.csv']+sorted((arm_dir/'accounting').glob('*_costs.csv'))}
    return dict(round=task['round'],route_seed=task['route_seed'],arm=task['arm'],n=n,assigned=assigned,served=served,broker_tdr_moves=sum(int(r['broker_idx'])==b for r in moves),income_wei=income,cost_wei=expense,profit_wei=income-expense,system_served=stats['served'],system_relay=stats['relayed'],system_tdr_moves=len(moves),wall_s=stats['wall_s'],artifacts=artifacts,checks_passed=True)
