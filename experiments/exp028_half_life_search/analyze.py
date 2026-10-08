"""Completed-run summaries, paired differences, and finite-horizon diagnostics."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.exp025_regime_shift_sensitivity import runner as base, paper_data

def bootstrap_difference(values, rng):
    values = np.asarray(values, dtype=float)
    if len(values) < 3:
        return float('nan'), float('nan')
    means = values[rng.integers(0, len(values), size=(10000, len(values)))].mean(axis=1)
    return tuple(np.quantile(means, [.025, .975]))

def summarize(df):
    rows = []
    for h, g in df.groupby('half_life', sort=True):
        r = dict(half_life=h, repeats=len(g), rounds=','.join(map(str, sorted(g['round']))))
        for col in ['relay', 'tdr', 'combined', 'relay_rate', 'throughput', 'submission_span_blocks']:
            v = g[col]
            r.update({col+'_mean':v.mean(), col+'_median':v.median(), col+'_q1':v.quantile(.25),
                      col+'_q3':v.quantile(.75), col+'_sd':v.std()})
        rows.append(r)
    return pd.DataFrame(rows)

def decisions(df, table, tolerance):
    rng = np.random.default_rng(28028)
    pairs = []
    bests = {}
    for metric in ['relay', 'combined']:
        best = float(table.loc[table[metric+'_mean'].idxmin(), 'half_life'])
        bests[metric] = best
        reference = df[df.half_life == best].set_index('route_seed')[metric]
        for h, g in df.groupby('half_life'):
            current = g.set_index('route_seed')[metric]
            seeds = current.index.intersection(reference.index)
            delta = (current.loc[seeds] - reference.loc[seeds]).to_numpy()
            low, high = bootstrap_difference(delta, rng)
            pairs.append(dict(metric=metric, candidate_h=h, reference_h=best,
                              paired_repeats=len(seeds), mean_difference=float(delta.mean()),
                              ci95_low=low, ci95_high=high))
    best_mean = table.combined_mean.min()
    near = sorted(table[table.combined_mean <= best_mean * (1+tolerance)].half_life.tolist())
    front = []
    for _, r in table.iterrows():
        dominated = ((table.relay_mean <= r.relay_mean) & (table.combined_mean <= r.combined_mean)
                     & ((table.relay_mean < r.relay_mean) | (table.combined_mean < r.combined_mean))).any()
        if not dominated: front.append(float(r.half_life))
    hs = sorted(table.half_life.tolist())
    shortest = near[0]
    i = hs.index(shortest)
    low, high = hs[max(0, i-1)], hs[min(len(hs)-1, i+1)]
    fine = sorted(set([max(1, int(round(x))) for x in np.geomspace(low, high, 7)]
                      + [int(round(bests['combined'])), int(round(shortest)), 20]))
    outcome = dict(primary_metric='relay + tdr', secondary_metric='relay',
                   empirical_best_h=bests, combined_best_mean=float(best_mean),
                   tolerance_fraction=tolerance, within_tolerance_h=near,
                   shortest_within_tolerance=float(shortest), pareto_h=sorted(front),
                   proposed_fine_values=fine, comparisons_are_exploratory=True,
                   selection_on_same_data=True,
                   interpretation='Within-tolerance refers to sample means, not proven statistical equivalence. '
                   'Pairwise bootstrap intervals are exploratory and unadjusted for winner selection/multiple comparisons.')
    return outcome, pd.DataFrame(pairs)

def figures(df, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import StrMethodFormatter
    groups = list(df.groupby('half_life', sort=True))
    if not groups: return
    if all(len(g) == 1 for _, g in groups):
        fig, axes = plt.subplots(1, 2, figsize=(13, 5), layout='constrained')
        hs = [h for h, _ in groups]
        for ax, metric, label, color in zip(axes, ['relay', 'combined'],
                         ['Relay CTX', 'Relay CTX + TDR CTX'], ['#1876B5', '#D76436']):
            values = [int(g[metric].iloc[0]) for _, g in groups]
            ax.plot(hs, values, marker='o', color=color, linewidth=1.6)
            ax.set_xscale('log')
            ax.set_xticks(hs, [f'{h:g}' for h in hs], fontsize=8)
            ax.set_xlabel('EWMA half-life (blocks; log scale)')
            ax.set_ylabel(label + ' (count)')
            ax.set_ylim(bottom=0)
            ax.grid(alpha=.2)
            ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
        fig.suptitle('Exp028 | One completed run per point | q_min=0.3; epsilon=0.95', fontsize=14)
        fig.supxlabel('Preliminary observations; connecting lines show sampled trends. No variability estimate.', fontsize=9)
        for ext in ['png', 'svg']: fig.savefig(out/f'half_life_trend.{ext}', dpi=180)
        plt.close(fig)
        return
    fig, axes = plt.subplots(1, 2, figsize=(max(13, len(groups)*1.0), 5.4), layout='constrained')
    for ax, metric, label, color in zip(axes, ['relay', 'combined'],
                          ['Relay CTX', 'Relay CTX + TDR CTX'], ['#1876B5', '#D76436']):
        for x, (h,g) in enumerate(groups):
            v=g[metric].to_numpy()
            if len(v)>1:
                b=ax.boxplot([v],positions=[x],widths=.5,patch_artist=True,manage_ticks=False,
                             showfliers=False,medianprops={'color':'#25313D'})
                b['boxes'][0].set(facecolor=color,alpha=.3)
            ax.scatter(x+np.linspace(-.1,.1,len(v)),v,s=16,color=color,zorder=3)
        ax.set_xticks(range(len(groups)),[f'{h:g}\nn={len(g)}' for h,g in groups],fontsize=8)
        ax.set_xlabel('EWMA half-life (blocks)'); ax.set_ylabel(label+' (count)')
        ax.set_ylim(bottom=0); ax.grid(axis='y',alpha=.2)
        ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
    fig.suptitle('Exp028 | Completed runs only | q_min=0.3; epsilon=0.95',fontsize=14)
    fig.supxlabel('Box: Q1-Q3; center: median; whiskers: 1.5 IQR; dots: runs. One sample is shown as a dot, not a box.',fontsize=9)
    for ext in ['png','svg']: fig.savefig(out/f'half_life_boxplots.{ext}',dpi=180)
    plt.close(fig)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--tolerance',type=float,default=.01,help='Practical mean-cost tolerance; default 1 percent')
    ap.add_argument('--verify-raw',action='store_true')
    ap.add_argument('--no-plots',action='store_true')
    args=ap.parse_args()
    if not 0<=args.tolerance<1:ap.error('--tolerance must be in [0,1)')
    source=args.source.resolve(); out=(args.output or source/'analysis').resolve(); out.mkdir(parents=True,exist_ok=True)
    manifest_bytes=(source/'manifest.json').read_bytes(); m=json.loads(manifest_bytes)
    if m.get('experiment')!='exp028':raise ValueError('This analyzer only accepts exp028 runs, not historical data')
    completed=[t for t in m['tasks'] if t['status']=='complete']
    workload=paper_data.read_csv(source/'workload.csv') if args.verify_raw else None
    rows=[]; hashes={}
    for t in completed:
        path=source/t['summary']; b=path.read_bytes(); doc=json.loads(b); a=doc['arms'][0]
        assert doc['task_id']==t['id'] and doc['workload_sha256']==m['workload_sha256']
        assert a['arm']==t['tag'] and doc['params']['exp']['route_seed']==t['route_seed']
        assert a['n']==m['total_ctx_per_arm'] and a['failed']==0 and a['served']+a['relayed']==a['n']
        assert a['burn_reconcile']['ok'] and all(a['gates'].values())
        assert a['tdr']['transfers_lost']==a['tdr']['lost_in_transit_wei']==0
        arm_dir=path.parent/t['tag']
        if args.verify_raw:paper_data.validate_arm(a,arm_dir,workload)
        hashes[t['summary']]=hashlib.sha256(b).hexdigest()
        with (arm_dir/'block_stats.csv').open(encoding='utf-8-sig',newline='') as f:
            blocks=[int(r['block']) for r in csv.DictReader(f) if int(r['broker_ctx'])+int(r['relay_ctx'])>0]
        span=max(blocks)-min(blocks)+1 if blocks else float('nan')
        rows.append(dict(task_id=t['id'],half_life=t['value'],round=t['round'],route_seed=t['route_seed'],
                         relay=a['relayed'],tdr=a['tdr']['transfers_done'],combined=a['relayed']+a['tdr']['transfers_done'],
                         relay_rate=a['relayed']/a['n'],throughput=a['throughput_ctx_per_s'],
                         submission_span_blocks=span,wall_ctx_s=a['wall_ctx_s']))
    (out/'manifest_snapshot.json').write_bytes(manifest_bytes)
    if not rows:
        print('No completed runs yet; no curves or recommendations generated.');return 0
    df=pd.DataFrame(rows)
    df.to_csv(out/'completed_runs.csv',index=False,encoding='utf-8-sig')
    table=summarize(df);table.to_csv(out/'parameter_summary.csv',index=False,encoding='utf-8-sig')
    sets=[set(df[df.half_life==v]['round']) for v in m['sweep_space']['half_life']['values']]
    common=sorted(set.intersection(*sets))
    outcome=dict(source=str(source),stage=m['stage'],smoke=m['smoke'],completed=len(rows),
                 planned=len(m['tasks']),common_complete_rounds=common,raw_revalidated=args.verify_raw,
                 source_summary_sha256=hashes,finite_horizon=True,historical_data_pooled=False)
    report=[f'# exp028 半衰期搜索分析\n\n完成{len(rows)}/{len(m["tasks"])}次，共同完整轮次：{common}。',
            '主指标为Relay+TDR次数，另报告Relay；每次TDR搬运计一次。来源为本轮当前实现，未合并历史submit_regime_shift数据。',
            '下表使用全部完成样本；进行中不同参数点n可能不同，建议以共同完整轮次做配对决策。',
            '|半衰期|n|Relay均值|Relay中位数|TDR均值|总和均值|总和中位数|实测提交跨度（块）|',
            '|---|---:|---:|---:|---:|---:|---:|---:|']
    for _,r in table.iterrows():
        report.append(f'|{r.half_life:g}|{r.repeats}|{r.relay_mean:.2f}|{r.relay_median:.2f}|{r.tdr_mean:.2f}|{r.combined_mean:.2f}|{r.combined_median:.2f}|{r.submission_span_blocks_mean:.1f}|')
    if len(common)>=3 and not m['smoke']:
        balanced=df[df['round'].isin(common)]
        balanced_table=summarize(balanced)
        balanced_table.to_csv(out/'balanced_summary.csv',index=False,encoding='utf-8-sig')
        ranking,pairs=decisions(balanced,balanced_table,args.tolerance)
        outcome.update(ranking)
        pairs.to_csv(out/'paired_differences.csv',index=False,encoding='utf-8-sig')
        fine=','.join(str(x) for x in ranking['proposed_fine_values'])
        fine_text=f'python .\\run.py --stage fine --values {fine} --rounds 10'
        (out/'suggested_fine_command.txt').write_text(fine_text+'\n',encoding='utf-8')
        report.extend(['\n## 共同轮次上的描述性决策',
                       f'Relay均值最低：h={ranking["empirical_best_h"]["relay"]:g}；总和均值最低：h={ranking["empirical_best_h"]["combined"]:g}。',
                       f'总和均值在最低值的{100*args.tolerance:g}%以内：{ranking["within_tolerance_h"]}；其中最短半衰期：{ranking["shortest_within_tolerance"]:g}。这只是工程容差候选，不能当作已证明等效。',
                       f'两指标的样本均值Pareto候选：{ranking["pareto_h"]}。',
                       'paired_differences.csv比较每个候选与该指标样本最低点的同route_seed差值，正数表示候选较差。95%配对自助区间仅供探索，未校正挑选赢家和多重比较，不能据此宣称全局最优或确认显著U型。',
                       '若中间点均值最低且短、长两端持续更差，可描述为U型候选；若最小值仍在边界，范围不足；若长区间差距很小则按平台处理，不能强行二次拟合制造谷底。',
                       '下一阶段命令（需先读结果再决定，不会自动启动）：`'+fine_text+'`。fine默认新route_seed=1007,1017,…，防止用同一批种子确认筛选赢家。'])
    else:
        if len(common) >= 1 and not m['smoke']:
            observed = summarize(df[df['round'].isin(common)])
            minima = {metric: float(observed.loc[observed[metric+'_mean'].idxmin(), 'half_life'])
                      for metric in ['relay', 'combined']}
            outcome['observed_min_h'] = minima
            report.append(f'\n仅按当前共同轮次观测比较：Relay最低点h={minima["relay"]:g}；总和最低点h={minima["combined"]:g}。不代表已确认的最优参数。')
        report.append('\n不足3个共同完整轮次，或为smoke：只展示初步数值趋势，不估计统计显著性、不生成自动精扫建议。单轮每点仅一笔观测，使用趋势折线图而非箱线图。')
    report.extend(['\n## 时长与外推限制',
                   '实测提交跨度来自block_stats.csv中有用户CTX的最早到最晚提交块，并非按120 CTX/s臆测。若半衰期接近或超过这个跨度，该点主要表现为有限长度负载下的慢记忆策略，不能外推到稳态最优。',
                   '即便完成10轮，相同选流负载上的多route_seed重复也不能替代不同负载的验证。若需要长时间稳定结论，另设计更长负载并保持实际热点切换节奏、负载组成与资金条件可比；不更改负载来追求U型形状。'])
    (out/'analysis_report.md').write_text('\n\n'.join(report).replace('|\n\n|','|\n|'),encoding='utf-8')
    (out/'decision.json').write_text(json.dumps(outcome,indent=2,ensure_ascii=False),encoding='utf-8')
    if not args.no_plots:figures(df,out)
    print(table[['half_life','repeats','relay_mean','combined_mean','submission_span_blocks_mean']].round(2).to_string(index=False))
    print(f'Analysis saved: {out}')
    if 'proposed_fine_values' in outcome:print('Suggested next command: '+fine_text)
    return 0

if __name__=='__main__':raise SystemExit(main())
