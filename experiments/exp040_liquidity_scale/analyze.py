"""Plot only verified whole-run observations; never treat brokers as repetitions."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from design import REVISION, file_hash, read_csv, write_json, write_csv, route_bin_statistics


def fig1_bars(run, tasks, config, out, label):
    ordered=sorted(tasks,key=lambda t:t['round'])
    if any(t['policy']!='plain' for t in ordered):
        raise ValueError('Fig1 repetitions must contain TDR-off runs only')
    rounds=[read_csv(run/t['attempts'][-1]['path']/'plain'/'ctx_rows.csv') for t in ordered]
    width=config['exp']['fig1_bin_width_blocks']
    summary,observations=route_bin_statistics(rounds,width)
    for row in observations:
        task=ordered[row.pop('round_index')-1]
        row.update(round=task['round'],route_seed=task['route_seed'],task_id=task['id'])
    write_csv(out/'fig1a_bins_by_round.csv',observations)
    write_csv(out/'fig1a_bin_statistics.csv',summary)
    x=np.array([r['block_end'] for r in summary])
    broker=np.array([r['broker_mean'] for r in summary]);relay=np.array([r['relay_mean'] for r in summary])
    fig,ax=plt.subplots(figsize=(6.4,4.2),layout='constrained')
    ax.bar(x,broker,width=.8*width,color='#3598db',edgecolor='black',linewidth=.8,label='Broker CTX')
    ax.bar(x,relay,bottom=broker,width=.8*width,color='#ec9d34',edgecolor='black',linewidth=.8,label='Relay CTX')
    if len(ordered)>1:
        ax.errorbar(x,broker,yerr=[r['broker_sd'] for r in summary],fmt='none',
                    ecolor='#123b58',capsize=4,elinewidth=1.1,capthick=1.1)
        ax.errorbar(x,broker+relay,yerr=[r['total_sd'] for r in summary],fmt='none',
                    ecolor='black',capsize=4,elinewidth=1.1,capthick=1.1)
    ax.set(xlabel='Submission block height (bin right edge)',ylabel='# of CTXs',
           title=f'TDR off: mean counts, n={len(ordered)}{label}')
    ax.set_xticks(x);ax.set_ylim(bottom=0);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    if len(x)>12:ax.tick_params(axis='x',labelrotation=60)
    ax.legend(fontsize=9)
    for suffix in ('png','pdf'):fig.savefig(out/f'fig1a_tdr_off.{suffix}',dpi=180)
    plt.close(fig)
    write_json(out/'fig1a_metadata.json',dict(n=len(ordered),bin_width_blocks=width,
        origin=0,source='Measured submit_block, same absolute bins in every whole run; no per-run rescaling',
        stack='Broker blue below Relay orange; no communication curve or right axis',
        error_caps='Sample SD across whole runs: broker boundary and stacked total; total SD calculated directly',
        n1='No estimated error bars when n=1',
        independent_shards='Submission block heights follow the historical Fig1 convention, not synchronized wall time'))


def analyze(run, allow_partial=False):
    run=Path(run).resolve()
    manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    config=json.loads((run/'config.json').read_text(encoding='utf-8'))
    if manifest['revision']!=REVISION:
        raise ValueError('Historical paired-plan data are retained; use the matching historical plotter')
    tasks=[t for t in manifest['tasks'] if t['status']=='complete']
    if not tasks:raise ValueError('No completed tasks')
    complete=len(tasks)==len(manifest['tasks'])
    if not complete and not allow_partial:
        raise ValueError('Experiment incomplete; --allow-partial explicitly labels exploratory plots')
    for t in tasks:
        path=run/t['attempts'][-1]['path']
        summary=json.loads((path/'summary.json').read_text(encoding='utf-8'))
        for name,sha in summary['artifacts'].items():
            if file_hash(path/t['policy']/name)!=sha:raise ValueError('Artifact hash mismatch')
        for name,sha in summary['derived'].items():
            if file_hash(path/name)!=sha:raise ValueError('Derived accounting hash mismatch')
    out=run/'figs';out.mkdir(exist_ok=True)
    results=read_csv(run/'results.csv');brokers=read_csv(run/'per_broker.csv')
    broker=config['exp']['profit_broker_idx']
    fee=config['b2e']['gas_units_per_ctx']*config['b2e']['ref_gas_price_wei']
    plain=config['exp']['experiment_kind']=='plain_repeats'
    policies=['plain' if plain else config['exp']['tdr_policy']]
    colors=['#3598db' if plain else '#ec9d34']
    partial=' [SMOKE]' if manifest['smoke'] else (' [PARTIAL]' if not complete else '')
    if not manifest['formal'] and not partial:partial=' [EXPLORATORY]'
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    plot_values={}
    if plain:
        fig1_bars(run,tasks,config,out,partial)
    for scope,data in [('system',results),(f'broker_{broker}',[r for r in brokers if int(r['broker_idx'])==broker])]:
        for dimension,key,xlabel in [('balance','balance_eth','Initial balance per sub-account (ETH)'),
                                      ('transactions','transactions','Total user transactions')]:
            group=[r for r in data if dimension in r['sweeps'].split(',')]
            if not group:continue
            xs=sorted({float(r[key]) for r in group})
            stem='fig5b' if dimension=='balance' else 'fig5c'
            for j,(metric,ylabel,scale) in enumerate([
                    ('income_wei','Broker service fee income (fixed fee F)',1/fee),
                    ('net_profit_wei','Modeled net profit (fixed fee F)',1/fee)]):
                fig,ax=plt.subplots(figsize=(6.5,4.2),layout='constrained')
                for pi,policy in enumerate(policies):
                    for xi,x in enumerate(xs):
                        selected=[r for r in group if float(r[key])==x and r['policy']==policy and r[metric] not in ('','None')]
                        values=[float(r[metric])*scale for r in selected]
                        if not values:continue
                        pos=xi
                        ax.boxplot([values],positions=[pos],widths=.5,patch_artist=True,manage_ticks=False,
                            showfliers=False,boxprops=dict(facecolor=colors[pi],alpha=.35),
                            medianprops=dict(color=colors[pi],linewidth=1.8))
                        ax.scatter([pos]*len(values),values,color=colors[pi],s=12,alpha=.75,
                                   label=policy if xi==0 else None,zorder=3)
                        ax.text(pos,.98,f'n={len(values)}',transform=ax.get_xaxis_transform(),
                                ha='center',va='top',fontsize=8)
                        plot_values[f'{scope}/{dimension}/{metric}/{x}/{policy}']=values
                labels=[f'{int(x/1000)}k' if dimension=='transactions' else f'{x:g}' for x in xs]
                ax.set_xticks(range(len(xs)),labels,rotation=35)
                ax.set_xlabel(xlabel);ax.set_ylabel(ylabel);ax.grid(axis='y',alpha=.2)
                ax.legend(fontsize=8)
                ax.axhline(0,color='gray',lw=.7)
                fig.suptitle(f'{scope}: TDR on, {dimension} sweep{partial}')
                filename=f'{stem}_{scope}' if metric=='net_profit_wei' else f'{stem}_{scope}_fee_income'
                for suffix in ('png','pdf'):fig.savefig(out/f'{filename}.{suffix}',dpi=180)
                plt.close(fig)
        # Each experiment supplies its own reference curve; no hidden counterpart arm.
        for policy,stem in [(policies[0],'fig1b' if plain else 'fig5a')]:
            baseline=[t for t in tasks if t['balance_eth']==config['exp']['reference_balance_eth']
                      and t['transactions']==config['exp']['reference_transactions'] and t['policy']==policy]
            if not baseline:continue
            runs=[]
            for t in baseline:
                rows=read_csv(run/t['attempts'][-1]['path']/'revenue_curve.csv')
                runs.append([r for r in rows if r['scope']==scope])
            x=np.array([int(r['transaction']) for r in runs[0]])
            if any([int(r['transaction']) for r in rows]!=list(x) for rows in runs):
                raise ValueError('Curve coordinate mismatch')
            fig,ax=plt.subplots(figsize=(6.3,4),layout='constrained')
            for column,color,label in [('income_wei','#3598db','Recorded service fee income'),
                                       ('upper_wei','#555555','All assigned CTX: theoretical upper bound')]:
                matrix=np.array([[int(r[column])/fee for r in rows] for rows in runs])
                mean=matrix.mean(axis=0)
                ax.plot(x,mean,color=color,label=label)
                if len(runs)>1:
                    sd=matrix.std(axis=0,ddof=1)
                    ax.fill_between(x,mean-sd,mean+sd,color=color,alpha=.18)
            ax.set(xlabel='Global user transaction arrival position',ylabel='Cumulative fee income (fixed fee F)',
                   title=f'{scope}, {policy}, n={len(runs)}{partial}')
            ax.legend(fontsize=8);ax.grid(alpha=.2)
            for suffix in ('png','pdf'):fig.savefig(out/f'{stem}_{scope}.{suffix}',dpi=180)
            plt.close(fig)
    write_json(out/'plot_data.json',plot_values)
    write_json(out/'metadata.json',dict(complete=complete,smoke=manifest['smoke'],
        curve_bands='Mean +/- sample SD across whole runs; no CI when n=1',
        boxplot='One observation per whole run; median, Q1/Q3, 1.5 IQR whiskers, raw run dots',
        revenue='10% of fixed service fee; not transaction principal; upper includes assigned relay CTX',
        profit='Final recorded service fee income minus modeled per-transfer cost; actual gasPrice=0',
        routing='Dynamic assignment; same broker index does not imply the same workload between arms'))
    print(f'Figures saved: {out}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--allow-partial',action='store_true')
    args=parser.parse_args();analyze(args.source,args.allow_partial)
