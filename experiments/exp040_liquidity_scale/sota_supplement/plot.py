"""Merge completed paired baseline trials with READ-ONLY original TDR results."""
from pathlib import Path
import argparse
import csv
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.text import Text


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def build_observations(run, allow_partial=False):
    m = json.loads((run / 'manifest.json').read_text(encoding='utf-8'))
    if m['smoke']:
        raise ValueError('Smoke changes topology/workload and must not be merged with formal data')
    if set(t['policy'] for t in m['tasks']) != {'autoloop', 'spider'}:
        raise ValueError('Run both baselines in one plan before drawing the four-series figure')
    if not allow_partial and any(t['status'] != 'complete' for t in m['tasks']):
        raise ValueError('Supplement incomplete; finish/resume first, or explicitly use --allow-partial')
    reference = {r['task_id']: r for r in read_csv(run / 'reference/per_broker.csv') if int(r['broker_idx']) == 19}
    added = {r['task_id']: r for r in read_csv(run / 'per_broker.csv') if int(r['broker_idx']) == 19}
    # Partial previews include only cells where BOTH new arms finished. Never zero-fill missing runs.
    cells = {}
    for t in m['tasks']:
        if t['status'] == 'complete':
            cells.setdefault(t['source_task_id'], {})[t['policy']] = t
    observations = []
    for source_id, arms in cells.items():
        if set(arms) != {'autoloop', 'spider'}:
            continue
        r = reference[source_id]
        meta = dict(source_task_id=source_id, round=int(r['round']), route_seed=int(r['route_seed']),
                    balance_eth=float(r['balance_eth']), broker_idx=19)
        observations.extend([dict(**meta, method='potential', profit_gwei=int(r['upper_wei']) / 1e9),
                             dict(**meta, method='topup', profit_gwei=int(r['net_profit_wei']) / 1e9)])
        for method, t in arms.items():
            a = added[t['id']]
            if any(str(a[k]) != str(r[k]) for k in ('route_seed', 'round', 'transactions')):
                raise ValueError('Unpaired baseline/reference metadata')
            observations.append(dict(**meta, method=method, profit_gwei=int(a['net_profit_wei']) / 1e9))
    if not observations:
        raise ValueError('No complete paired AutoLoop/Spider cells to plot')
    return m, observations


def plot(run, allow_partial=False):
    m, observations = build_observations(run, allow_partial)
    out = run / 'figs'
    out.mkdir(exist_ok=True)
    balances = sorted({r['balance_eth'] for r in observations})
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.weight': 'bold', 'axes.labelweight': 'bold',
        'pdf.fonttype': 42, 'ps.fonttype': 42, 'axes.linewidth': .35, 'xtick.major.width': .35,
        'ytick.major.width': .35})
    w, h = 125.6, 134.7
    fig = plt.figure(figsize=(w / 72, h / 72), dpi=300)
    ax = fig.add_axes([25.5 / w, (h - 110.5) / h, 93.7 / w, 94 / h])
    specs = [('topup', '#DE2B03', 'Broker with TDR'),
             ('autoloop', '#F39C12', 'Broker with\nAutoLoop'),
             ('spider', '#9763B5', 'Broker with\nSpider-rebalance'),
             ('potential', '#28A8F2', 'Potential profit\nof Broker 19')]
    handles, summaries = [], []
    for j, (method, color, label) in enumerate(specs):
        groups = [[r['profit_gwei'] for r in observations if r['method'] == method and r['balance_eth'] == b] for b in balances]
        ax.boxplot(groups, positions=np.arange(len(balances)) + (j - 1.5) * .19, widths=.16,
            manage_ticks=False, patch_artist=True, showfliers=False, whis=1.5,
            boxprops=dict(facecolor=color, edgecolor=color, linewidth=.45, alpha=.52),
            medianprops=dict(color='black', linewidth=.35),
            whiskerprops=dict(color=color, linewidth=.4), capprops=dict(color=color, linewidth=.4))
        handles.append(Patch(facecolor=color, edgecolor=color, alpha=.52, label=label))
        for b, values in zip(balances, groups):
            q = np.percentile(values, [25, 50, 75])
            summaries.append(dict(balance_eth=b, method=method, n=len(values), q1_gwei=q[0],
                median_gwei=q[1], q3_gwei=q[2], minimum_gwei=min(values), maximum_gwei=max(values)))
    values = [r['profit_gwei'] for r in observations]
    lo, hi = min(0, min(values)), max(values)
    pad = max((hi - lo) * .07, .05)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-.6, len(balances) - .4)
    ax.set_xticks(np.arange(len(balances)), [f'{b:g}' for b in balances], rotation=30, ha='right')
    ax.set_xlabel('Initial balance (ETH)', fontsize=6.705, labelpad=1)
    ax.set_ylabel('Profit of Broker 19 (Gwei)', fontsize=6.3, labelpad=1)
    ax.tick_params(axis='x', labelsize=5.14, pad=1, length=1.5)
    ax.tick_params(axis='y', labelsize=5.364, pad=1.4, length=1.5)
    ax.axhline(0, color='.55', lw=.35, zorder=0)
    ax.grid(axis='y', linestyle=':', linewidth=.3, alpha=.4)
    ax.set_axisbelow(True)
    ax.legend(handles=handles, loc='lower right', fontsize=5.1, borderpad=.3, handlelength=1,
              framealpha=1, labelspacing=.25)
    complete = all(t['status'] == 'complete' for t in m['tasks'])
    if not complete:
        fig.text(.5, .98, 'PARTIAL - COMPLETE PAIRED CELLS ONLY', fontsize=3.8, ha='center', va='top', color='#888888')
    fig.canvas.draw()
    ren = fig.canvas.get_renderer()
    clipped = [t.get_text() for t in fig.findobj(Text) if t.get_visible() and t.get_text()
        and not (fig.bbox.contains(*t.get_window_extent(ren).p0) and fig.bbox.contains(*t.get_window_extent(ren).p1))]
    if clipped:
        raise ValueError('Clipped labels: ' + repr(clipped))
    stem = out / ('fig5c_broker19' if complete else 'fig5c_broker19_partial')
    fig.savefig(stem.with_suffix('.pdf'), facecolor='white')
    fig.savefig(stem.with_suffix('.png'), dpi=600, facecolor='white')
    plt.close(fig)
    for name, rows in [('observations.csv', observations), ('box_statistics.csv', summaries)]:
        with (out / name).open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    note = dict(base_run=m['base_run'], paired_cells=len(observations) // 4, complete=complete,
        broker_idx=19, scope='Broker 19, all 16 subaccounts summed',
        potential='Original TDR-assigned-user zero-cost fee-income reference; dynamic assignments can differ between methods, so not a common upper bound',
        profit='0.0021 * broker-served user CTX - 0.021 * completed rebalance transfers, in Gwei',
        box='Across matched rounds: Q1-Q3, median, 1.5 IQR whiskers; fliers hidden; no synthetic padding',
        experimental_setting='All 50 brokers receive the same B ETH per shard; saved original 40k workload replayed without resampling',
        nominal_cost='Model fee, not actual gas; on-chain gas price is zero',
        frame_pt=[25.5, 16.5, 93.7, 94], page_pt=[w, h], clipped_text=clipped)
    (out / 'provenance.json').write_text(json.dumps(note, indent=2), encoding='utf-8')
    print('FIGURE=' + str(stem.with_suffix('.pdf')))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path)
    p.add_argument('--allow-partial', action='store_true')
    a = p.parse_args()
    plot(a.run.resolve(), a.allow_partial)
