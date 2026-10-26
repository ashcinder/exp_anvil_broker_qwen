"""Merge measured TDR OFF and original Fig5b/c data; never synthesize missing rounds."""
from pathlib import Path
import argparse
import csv
import json
import hashlib
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.text import Text


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def rows(p):
    with p.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def broker(p):
    doc = read(p / 'summary.json')
    raw = p / 'per_broker.csv'
    if hashlib.sha256(raw.read_bytes()).hexdigest() != doc['derived']['per_broker.csv']:
        raise ValueError('Measured per-broker file changed: ' + str(raw))
    r = next(r for r in rows(raw) if int(r['broker_idx']) == 19)
    if int(r['income_wei']) - int(r['modeled_cost_wei']) != int(r['net_profit_wei']):
        raise ValueError('Profit arithmetic mismatch')
    return r


def observations(run, sweep, partial=False):
    m = read(run / 'manifest.json')
    if m['smoke']:
        raise ValueError('Smoke observations cannot enter formal plots')
    tasks = [t for t in m['tasks'] if t['sweep'] == sweep]
    if not tasks:
        raise ValueError('No tasks for this sweep')
    other = Path(m['sota_results'][sweep])
    sm = read(other / 'manifest.json')
    if sm.get('smoke'):
        raise ValueError('SOTA source is a smoke run')
    if not partial and (any(t['status'] != 'complete' for t in tasks) or any(t['status'] != 'complete' for t in sm['tasks'])):
        raise ValueError('Formal five-series figure requires all methods to finish; use --allow-partial for an explicitly labelled preview')
    available = {}
    for t in sm['tasks']:
        if t['status'] == 'complete':
            key = (int(t['round']), float(t['balance_eth']), int(t['transactions']))
            available.setdefault(key, {})[t['policy']] = t
    reference = {r['task_id']: r for r in rows(run / 'reference' / sweep / 'per_broker.csv') if int(r['broker_idx']) == 19}
    out = []
    for t in tasks:
        key = (t['round'], t['balance_eth'], t['transactions'])
        peers = available.get(key, {})
        if t['status'] != 'complete' or set(peers) != {'autoloop', 'spider'}:
            continue
        original = reference[t['source_task_id']]
        if (int(original['round']), int(original['route_seed']), float(original['balance_eth']), int(original['transactions'])) != (t['round'], t['route_seed'], t['balance_eth'], t['transactions']):
            raise ValueError('Original TDR pairing mismatch')
        measured = {'plain': broker(run / t['attempts'][-1]['path'])}
        for method, peer in peers.items():
            if peer['route_seed'] != t['route_seed']:
                raise ValueError('SOTA seed mismatch')
            measured[method] = broker(other / peer['attempts'][-1]['path'])
        measured['topup'] = original
        meta = dict(sweep=sweep, source_task_id=t['source_task_id'], round=t['round'], route_seed=t['route_seed'],
                    balance_eth=t['balance_eth'], transactions=t['transactions'], broker_idx=19)
        for method, r in measured.items():
            out.append(dict(**meta, method=method, profit_gwei=int(r['net_profit_wei']) / 1e9,
                assigned=int(r['assigned']), served=int(r['served']), relay=int(r['relay']),
                internal_transfers=int(r['tdr_transfers']), own_potential_gwei=int(r['upper_wei']) / 1e9))
        out.append(dict(**meta, method='potential', profit_gwei=int(original['upper_wei']) / 1e9,
            assigned=int(original['assigned']), served=int(original['served']), relay=int(original['relay']),
            internal_transfers=0, own_potential_gwei=int(original['upper_wei']) / 1e9))
    if not out:
        raise ValueError('No matched cells with all four measured methods complete')
    return out


def plot(run, sweep, partial=False, png_only=False):
    data = observations(run, sweep, partial)
    field = 'balance_eth' if sweep == 'c' else 'transactions'
    xs = sorted({r[field] for r in data})
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.weight': 'bold', 'axes.labelweight': 'bold',
        'pdf.fonttype': 42, 'ps.fonttype': 42, 'axes.linewidth': .35,
        'xtick.major.width': .35, 'ytick.major.width': .35})
    w, h = 125.6, 134.7
    fig = plt.figure(figsize=(w / 72, h / 72))
    ax = fig.add_axes([25.5 / w, 24.2 / h, 93.7 / w, 94 / h])
    specs = [('topup', '#DE2B03', 'Broker with TDR'), ('plain', '#219E73', 'Broker without TDR'),
             ('autoloop', '#F39C12', 'AutoLoop'), ('spider', '#9763B5', 'Spider-rebalance'),
             ('potential', '#28A8F2', 'Potential profit\nof Broker 19')]
    handles, stats = [], []
    for j, (method, color, label) in enumerate(specs):
        groups = [[r['profit_gwei'] for r in data if r['method'] == method and r[field] == x] for x in xs]
        ax.boxplot(groups, positions=np.arange(len(xs)) + (j - 2) * .155, widths=.125,
            manage_ticks=False, patch_artist=True, showfliers=False, whis=1.5,
            boxprops=dict(facecolor=color, edgecolor=color, alpha=.52, linewidth=.4),
            medianprops=dict(color='black', linewidth=.3), whiskerprops=dict(color=color, linewidth=.35),
            capprops=dict(color=color, linewidth=.35))
        handles.append(Patch(facecolor=color, edgecolor=color, alpha=.52, label=label))
        for x, values in zip(xs, groups):
            q1, median, q3 = np.percentile(values, [25, 50, 75])
            stats.append(dict(parameter=x, method=method, n=len(values), q1_gwei=q1, median_gwei=median,
                q3_gwei=q3, minimum_gwei=min(values), maximum_gwei=max(values)))
    values = [r['profit_gwei'] for r in data]
    lo, hi = min(0, min(values)), max(values)
    pad = max((hi - lo) * .07, .05)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-.6, len(xs) - .4)
    labels = [f'{x:g}' if sweep == 'c' else f'{int(x)//1000}k' for x in xs]
    ax.set_xticks(np.arange(len(xs)), labels, rotation=30, ha='right')
    ax.set_xlabel('Initial balance (ETH)' if sweep == 'c' else 'User transactions', fontsize=6.705, labelpad=1)
    ax.set_ylabel('Profit of Broker 19 (Gwei)', fontsize=6.3, labelpad=1)
    ax.tick_params(axis='x', labelsize=5.14, pad=1, length=1.5)
    ax.tick_params(axis='y', labelsize=5.364, pad=1.4, length=1.5)
    ax.axhline(0, color='.55', lw=.35, zorder=0)
    ax.grid(axis='y', linestyle=':', linewidth=.3, alpha=.4)
    ax.set_axisbelow(True)
    legend = ax.legend(handles=handles, loc='lower right', fontsize=4.15, borderpad=.3, handlelength=1,
                      framealpha=1, labelspacing=.25)
    # Reserve real space below the lowest box, rather than allowing the legend to
    # obscure negative-profit observations. Keep the paper's fixed axes frame.
    fig.canvas.draw()
    fraction = legend.get_window_extent(fig.canvas.get_renderer()).height / ax.bbox.height + .035
    ax.set_ylim(min(lo - pad, (lo - fraction * (hi + pad)) / (1 - fraction)), hi + pad)
    if partial:
        fig.text(.5, .98, 'PARTIAL: matched completed rounds only', ha='center', va='top', fontsize=3.5, color='.45')
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    clipped = [t.get_text() for t in fig.findobj(Text) if t.get_visible() and t.get_text()
        and not (fig.bbox.contains(*t.get_window_extent(renderer).p0) and fig.bbox.contains(*t.get_window_extent(renderer).p1))]
    if clipped:
        raise ValueError('Clipped text: ' + repr(clipped))
    folder = run / 'figs'
    folder.mkdir(exist_ok=True)
    stem = folder / (f'fig5{s}_with_plain' + ('_partial' if partial else ''))
    if not png_only:
        fig.savefig(stem.with_suffix('.pdf'), facecolor='white')
    fig.savefig(stem.with_suffix('.png'), facecolor='white', dpi=600)
    plt.close(fig)
    for suffix, records in [('observations', data), ('box_statistics', stats)]:
        with (folder / f'{stem.name}_{suffix}.csv').open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    (folder / f'{stem.name}_provenance.json').write_text(json.dumps(dict(partial=partial, paired_cells=len(data)//5,
        scope='Broker 19, all 16 subaccounts', units='Gwei', page_pt=[w,h], axes_pt=[25.5,16.5,93.7,94],
        potential='With-TDR assigned-user fee income; not a shared strict ceiling for other assignments',
        formula='broker-served user CTX * 0.0021 - completed internal transfers * 0.021',
        box='Q1-Q3, median, 1.5 IQR whiskers; no fabricated variability', clipped=clipped), indent=2), encoding='utf-8')
    print('FIGURE=' + str(stem))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path)
    p.add_argument('--only', choices=['b', 'c', 'both'], default='both')
    p.add_argument('--allow-partial', action='store_true')
    p.add_argument('--png-only', action='store_true')
    a = p.parse_args()
    for s in ('b', 'c') if a.only == 'both' else (a.only,):
        plot(a.run.resolve(), s, a.allow_partial, a.png_only)
