"""Plot exp034 OAT results: means and sample SD across paired routing rounds."""
import argparse
import csv
import json
import statistics
import math
from collections import defaultdict
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.exp025_regime_shift_sensitivity.runner import write_json, validate_task_params

def collect(run):
    manifest = json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    report = json.loads((run/'report.json').read_text(encoding='utf-8'))
    if manifest['experiment'] != 'exp034' or manifest.get('algorithm_revision') != 'exp034-independent-lowwater-v3':
        raise ValueError('Only exp034 independent-lowwater v3 results are accepted')
    tasks = {task['id']: task for task in manifest['tasks']}
    groups = defaultdict(list)
    rows = list(csv.DictReader((run/'raw_results.csv').open(encoding='utf-8')))
    seen = set()
    for row in rows:
        task = tasks[row['task_id']]
        if task['id'] in seen or task['status'] != 'complete':
            raise ValueError('Duplicate or uncompleted task')
        seen.add(task['id'])
        doc = json.loads((run/task['summary']).read_text(encoding='utf-8'))
        validate_task_params(task, doc)
        stats = doc['arms'][0]
        if (doc['task_id'] != task['id'] or stats['arm'] != task['tag'] or
            doc['workload_sha256'] != manifest['workload_sha256'] or
            int(row['round']) != task['round'] or int(row['route_seed']) != task['route_seed'] or
            row['sweep'] != task['sweep'] or float(row['value']) != task['value'] or
            doc['params']['exp']['route_seed'] != task['route_seed'] or
            doc['params']['exp']['low_water_ratio'] != task['low_water_ratio'] or
            not doc['status']['complete'] or not all(stats['gates'].values()) or
            not stats['burn_reconcile']['ok']):
            raise ValueError('Task identity, workload or accounting gate mismatch')
        if any(int(row[key]) != stats[key] for key in ('n','served','relayed')):
            raise ValueError('CSV/task count mismatch')
        if int(row['transfers']) != stats['tdr']['transfers_done']:
            raise ValueError('CSV/task transfer mismatch')
        if not math.isclose(float(row['moved_eth']), stats['tdr']['moved_wei']/10**18, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError('CSV/task moved amount mismatch')
        groups[(task['sweep'],task['value'])].append(dict(
            service_ratio=stats['served']/stats['n'],
            transfers=stats['tdr']['transfers_done'],
            moved_eth=stats['tdr']['moved_wei']/10**18,
            relay=stats['relayed'], throughput=stats['throughput_ctx_per_s']))
    if not groups:
        raise ValueError('No completed results')
    summary = []
    for (sweep,value), records in sorted(groups.items()):
        entry = dict(sweep=sweep, value=value, rounds=len(records))
        for metric in records[0]:
            values=[r[metric] for r in records]
            entry[metric+'_mean']=statistics.mean(values)
            entry[metric+'_sd']=statistics.stdev(values) if len(values)>1 else 0
        summary.append(entry)
    formal = bool(manifest['formal'] and report['formal'] and report['status']['complete']
                  and len(rows)==len(tasks) and all(r['rounds']==10 for r in summary)
                  and all({task['round'] for task in tasks.values()
                           if task['sweep']==r['sweep'] and task['value']==r['value']} == set(range(1,11))
                          for r in summary))
    return summary, formal

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment-source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--heatmap-end-s', type=float, default=684)
    a=p.parse_args()
    summary,formal=collect(a.experiment_source)
    a.output.mkdir(parents=True,exist_ok=True)
    with (a.output/'sensitivity_summary.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(summary[0]))
        writer.writeheader();writer.writerows(summary)
    write_json(a.output/'sensitivity_summary.json',dict(formal=formal,summary=summary))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    dimensions=[x for x in ('q_min','half_life','epsilon','low_water_ratio') if any(r['sweep']==x for r in summary)]
    fig,axes=plt.subplots(3,len(dimensions),figsize=(5*len(dimensions),10),squeeze=False)
    for column,dimension in enumerate(dimensions):
        records=sorted((r for r in summary if r['sweep']==dimension),key=lambda r:r['value'])
        for index,(metric,label) in enumerate((('service_ratio','Broker service ratio'),('transfers','Completed transfers'),('moved_eth','Moved amount (ETH)'))):
            ax=axes[index,column]
            ax.errorbar([r['value'] for r in records],[r[metric+'_mean'] for r in records],
                        yerr=[r[metric+'_sd'] for r in records],marker='o',capsize=3)
            if dimension in ('epsilon','half_life'):
                ax.set_xscale('log')
            ax.set_xlabel(dimension);ax.set_ylabel(label);ax.grid(alpha=.25)
            if metric=='service_ratio': ax.set_ylim(0,1.05)
    fig.suptitle('exp034 sensitivity: mean +/- sample SD ('+('formal' if formal else 'analysis/smoke')+')')
    fig.tight_layout(rect=(0,0,1,.96))
    fig.savefig(a.output/'exp034_sensitivity.png',dpi=180)
    plt.close(fig)
    print('Saved sensitivity figures and verified summary:',a.output)
    return 0
if __name__=='__main__':
    raise SystemExit(main())
