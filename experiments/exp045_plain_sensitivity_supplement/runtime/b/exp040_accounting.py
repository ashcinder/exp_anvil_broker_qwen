"""Pure experimental design and fee accounting; no chain side effects."""
from __future__ import annotations
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

REVISION = 'exp040-041-split-v2'
ETH = 10**18


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        raise ValueError(f'Cannot write empty CSV: {path}')
    with Path(path).open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def validate_config(c):
    e, s = c['exp'], c['scale']
    if e.get('experiment_kind') not in ('tdr_scan', 'plain_repeats'):
        raise ValueError('Choose tdr_scan or plain_repeats')
    for key in ('balances_grid_eth', 'transactions_grid'):
        vs = e[key]
        if not vs or len(vs) != len(set(vs)) or any(not math.isfinite(v) or v <= 0 for v in vs):
            raise ValueError(f'Invalid grid: {key}')
    if any(int(n) != n or int(n) % s['num_brokers'] for n in e['transactions_grid']):
        raise ValueError('Transaction counts must be integers divisible by num_brokers')
    if e['reference_balance_eth'] not in e['balances_grid_eth'] or e['reference_transactions'] not in e['transactions_grid']:
        raise ValueError('Reference cell must occur in both grids')
    if int(e['repetitions']) != e['repetitions'] or e['repetitions'] <= 0 or e['route_seed_step'] <= 0:
        raise ValueError('Positive integer rounds and positive seed step required')
    if e['tdr_policy'] not in ('topup', 'topup_lowwater'):
        raise ValueError('Unsupported policy')
    if not 0 <= e['tdr_epsilon'] <= 1 or not 0 <= e['tdr_q_min'] <= 1 or e['tdr_ewma_half_life'] <= 0:
        raise ValueError('Invalid TDR parameters')
    if not 0 <= e['low_water_ratio'] <= 1:
        raise ValueError('Invalid low-water ratio')
    if e['shift_start_index'] < 0 or e['shift_period_transactions'] <= 0:
        raise ValueError('Invalid absolute shift schedule')
    cycle = e['shift_cycle_absolute']
    if len(set(cycle)) < 2 or any(int(x) != x or not 0 <= x < c['chain']['num_shards'] for x in cycle):
        raise ValueError('Shift cycle needs two distinct valid shards')
    b = c['b2e']
    if not b['enabled'] or b['charges_rebalancing'] or b['gas_units_per_ctx'] <= 0 or b['ref_gas_price_wei'] <= 0:
        raise ValueError('Enable fixed B2E fees, positive fee basis, and no B2E charge on internal TDR')
    if Decimal(str(b['fee_share'])) != Decimal('0.1'):
        raise ValueError('This experiment requires exactly 10% of the fixed service fee')
    if e['modeled_tdr_cost_wei'] < 0 or not 0 <= e['profit_broker_idx'] < s['num_brokers']:
        raise ValueError('Invalid cost/broker')
    if e['max_backlog'] < max(e['transactions_grid']):
        raise ValueError('max_backlog must cover the largest transaction count')
    master = e['master_transactions']
    if int(master) != master or master < max(e['transactions_grid']) or master % s['num_brokers']:
        raise ValueError('Master workload must cover the grid and be divisible by num_brokers')
    if e['experiment_kind'] == 'plain_repeats' and (e['balances_grid_eth'] != [e['reference_balance_eth']]
            or e['transactions_grid'] != [e['reference_transactions']]):
        raise ValueError('Plain repeats must contain exactly the reference cell')
    width = e['fig1_bin_width_blocks']
    if int(width) != width or width <= 0:
        raise ValueError('fig1_bin_width_blocks must be a positive integer')
    if int(e['curve_points']) != e['curve_points'] or e['curve_points'] <= 0:
        raise ValueError('curve_points must be a positive integer')


def build_tasks(c, only='all'):
    validate_config(c)
    e = c['exp']
    cells = {}
    if e['experiment_kind'] == 'plain_repeats':
        if only != 'all':
            raise ValueError('Fig1 Plain repetitions have no scan dimensions; use --only all')
        cells[(float(e['reference_balance_eth']), int(e['reference_transactions']))] = ['fig1']
    elif only in ('all', 'balance'):
        for fund in e['balances_grid_eth']:
            cells.setdefault((float(fund), int(e['reference_transactions'])), []).append('balance')
    if e['experiment_kind'] == 'tdr_scan' and only in ('all', 'transactions'):
        for n in e['transactions_grid']:
            cells.setdefault((float(e['reference_balance_eth']), int(n)), []).append('transactions')
    if not cells:
        raise ValueError('No selected dimensions')
    tasks = []
    for rep in range(1, int(e['repetitions'])+1):
        current = list(cells.items())
        # Rotate cells between rounds to reduce batch drift.
        offset = (rep-1) % len(current)
        current = current[offset:] + current[:offset]
        for (fund, n), sweeps in current:
            policies = ('plain',) if e['experiment_kind'] == 'plain_repeats' else (e['tdr_policy'],)
            for policy in policies:
                tasks.append(dict(id=f'r{rep:02d}_b{fund:g}_n{n}_{policy}', round=rep,
                                  route_seed=int(e['route_seed']+(rep-1)*e['route_seed_step']),
                                  balance_eth=fund, transactions=n, policy=policy, sweeps=sweeps,
                                  status='pending', attempts=[]))
    return tasks


def route_bin_statistics(round_rows, width):
    """Common absolute submission-block bins; variability comes from whole runs.

    A fixed total can have zero SD while the broker/relay boundary still varies.
    Never add component SDs to obtain the stacked total SD.
    """
    if not round_rows or int(width) != width or width <= 0:
        raise ValueError('Need completed rounds and a positive integer bin width')
    counts=[]
    last=0
    for rows in round_rows:
        if not rows:
            raise ValueError('Empty run is not a zero-transaction observation')
        grouped=defaultdict(lambda: [0,0])
        for row in rows:
            block=int(row['submit_block'])
            if block < 0 or row['route'] not in ('broker','relay') or str(row['ok']).lower()!='true':
                raise ValueError('Invalid route observation')
            index=block//width
            grouped[index][0 if row['route']=='broker' else 1]+=1
            last=max(last,index)
        counts.append(grouped)
    summary=[]; observations=[]
    sd=lambda values: statistics.stdev(values) if len(values)>1 else None
    for index in range(last+1):
        broker=[group[index][0] for group in counts]
        relay=[group[index][1] for group in counts]
        total=[b+r for b,r in zip(broker,relay)]
        summary.append(dict(block_start=index*width,block_end=(index+1)*width,n=len(counts),
            broker_mean=statistics.mean(broker),relay_mean=statistics.mean(relay),total_mean=statistics.mean(total),
            broker_sd=sd(broker),relay_sd=sd(relay),total_sd=sd(total)))
        for rep,(b,r) in enumerate(zip(broker,relay),1):
            observations.append(dict(round_index=rep,block_start=index*width,block_end=(index+1)*width,
                                     broker_ctx=b,relay_ctx=r,total_ctx=b+r))
    return summary,observations


def shift_master(rows, start, period, cycle):
    result = []
    for i, source in enumerate(rows):
        row = dict(source)
        row['arrival_pos'] = i
        row['original_dst_shard'] = int(row['dst_shard'])
        if i >= start:
            phase = ((i-start)//period) % len(cycle)
            choices = cycle[phase:] + cycle[:phase]
            row['dst_shard'] = next(s for s in choices if s != int(row['src_shard']))
        if int(row['src_shard']) == int(row['dst_shard']):
            raise ValueError('Same-shard user transaction')
        result.append(row)
    return result


def retained_fee(c):
    b = c['b2e']
    return int(Decimal(b['gas_units_per_ctx'])*Decimal(b['ref_gas_price_wei'])*Decimal(str(b['fee_share'])))


def verify_and_account(c, task, stats, rows, moves, workload):
    """Check saved evidence and return per-broker final/cumulative fee accounts."""
    expected = {x['ctx_id']: x for x in workload}
    if len(expected) != len(workload) or len(rows) != len(workload) or len({x['ctx_id'] for x in rows}) != len(rows):
        raise ValueError('Missing/duplicated CTX')
    gates = stats.get('gates', {})
    required = ('G1_all_ok','G2_no_dup_no_loss','G3a_broker_ledger_eq_chain','G3b_global_net_zero','G4_reports_complete')
    if any(gates.get(k) is not True for k in required) or not stats.get('burn_reconcile',{}).get('ok'):
        raise ValueError('Conservation/integrity gates failed')
    if stats.get('failed') != 0 or stats.get('coord',{}).get('aborted'):
        raise ValueError('Failed/aborted task')
    if stats['n'] != len(rows) or stats['served'] + stats['relayed'] != len(rows):
        raise ValueError('Summary count mismatch')
    fee = retained_fee(c)
    nb = c['scale']['num_brokers']
    accounts = {b:dict(assigned=0, served=0, relay=0, income_wei=0, upper_wei=0,
                       tdr_transfers=0, modeled_cost_wei=0) for b in range(nb)}
    seen_pos = set()
    for x in rows:
        source = expected.get(x['ctx_id'])
        if source is None or str(x['ok']).lower() != 'true' or x['route'] not in ('broker','relay'):
            raise ValueError('Unexpected/failed transaction')
        for key, original in [('src','src_shard'),('dst','dst_shard'),('amount_wei','amount_wei'),('arrival_pos','arrival_pos')]:
            if int(x[key]) != int(source[original]):
                raise ValueError(f'Workload mismatch {x["ctx_id"]}/{key}')
        b = int(x['broker_idx']); pos = int(x['arrival_pos'])
        if b not in accounts or pos in seen_pos:
            raise ValueError('Invalid broker/arrival position')
        seen_pos.add(pos)
        a = accounts[b]; served = x['route'] == 'broker'
        if int(x.get('fee_broker_wei',0)) != (fee if served else 0):
            raise ValueError('Recorded income differs from fixed 10% fee')
        a['assigned'] += 1; a['upper_wei'] += fee
        a['served'] += int(served); a['relay'] += int(not served)
        a['income_wei'] += fee if served else 0
    if seen_pos != set(range(len(rows))):
        raise ValueError('Arrival positions must be contiguous from zero')
    tdr = stats['tdr']
    if len(moves) != tdr['transfers_done'] or sum(int(x['amount_wei']) for x in moves) != int(tdr['moved_wei']):
        raise ValueError('TDR ledger mismatch')
    if tdr['transfers_lost'] or int(tdr['lost_in_transit_wei']):
        raise ValueError('Unsettled TDR funds')
    if task['policy'] == 'plain' and moves:
        raise ValueError('Plain unexpectedly rebalanced')
    for x in moves:
        if x['state'] != 'done' or min(int(x['burn_block']),int(x['mint_block'])) < 0:
            raise ValueError('Incomplete transfer')
        a = accounts[int(x['broker_idx'])]
        a['tdr_transfers'] += 1
        a['modeled_cost_wei'] += c['exp']['modeled_tdr_cost_wei']
    if sum(x['served'] for x in accounts.values()) != stats['served']:
        raise ValueError('Route count mismatch')
    if sum(x['income_wei'] for x in accounts.values()) != int(stats['retained_fee_income_wei']):
        raise ValueError('Summary fee mismatch')
    finals = []
    for b, a in accounts.items():
        finals.append(dict(broker_idx=b, **a, net_profit_wei=a['income_wei']-a['modeled_cost_wei'],
                           income_realization=a['income_wei']/a['upper_wei'] if a['upper_wei'] else None))
    # Curves use the same global arrival-position coordinate in every repetition.
    # Upper and income are for this arm's own actual dynamic assignment.
    by_pos = {int(x['arrival_pos']): x for x in rows}
    selected = c['exp']['profit_broker_idx']
    bins = int(c['exp']['curve_points'])
    checkpoints = {0, len(rows)} | {round(i*len(rows)/bins) for i in range(1,bins)}
    cumulative = {b:dict(assigned=0, served=0) for b in range(nb)}
    curve = []
    for k in range(len(rows)+1):
        if k:
            x = by_pos[k-1]; a = cumulative[int(x['broker_idx'])]
            a['assigned'] += 1; a['served'] += int(x['route']=='broker')
        if k in checkpoints:
            for scope, a in [('system',dict(assigned=k, served=sum(v['served'] for v in cumulative.values()))),
                             (f'broker_{selected}',cumulative[selected])]:
                curve.append(dict(transaction=k, scope=scope, assigned=a['assigned'],served=a['served'],
                                  income_wei=a['served']*fee,upper_wei=a['assigned']*fee))
    return finals, curve
