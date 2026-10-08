"""Analyze only completed, gated runs; no fabricated samples or forced U curves."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from experiments.exp025_regime_shift_sensitivity import runner as base,paper_data

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--verify-raw',action='store_true')
    ap.add_argument('--no-plots',action='store_true')
    args=ap.parse_args();source=args.source.resolve();out=source/'analysis';out.mkdir(exist_ok=True)
    b=(source/'manifest.json').read_bytes();manifest=json.loads(b)
    if manifest['experiment']!='exp029':raise ValueError('Expected an exp029 run')
    (out/'manifest_snapshot.json').write_bytes(b)
    workload=paper_data.read_csv(source/'workload.csv') if args.verify_raw else None
    rows=[];hashes={}
    for t in manifest['tasks']:
        if t['status']!='complete':continue
        p=source/t['summary'];raw=p.read_bytes();doc=json.loads(raw);a=doc['arms'][0]
        assert doc['task_id']==t['id'] and doc['workload_sha256']==manifest['workload_sha256']
        assert a['arm']==t['tag'] and a['n']==manifest['total_ctx_per_arm']
        assert all(a['gates'].values()) and a['burn_reconcile']['ok'] and a['failed']==0
        assert a['served']+a['relayed']==a['n'] and a['tdr']['transfers_lost']==0 and a['tdr']['lost_in_transit_wei']==0
        if args.verify_raw:paper_data.validate_arm(a,p.parent/t['tag'],workload)
        hashes[t['summary']]=hashlib.sha256(raw).hexdigest()
        rows.append(dict(task_id=t['id'],sweep=t['sweep'],value=t['value'],round=t['round'],
                         route_seed=t['route_seed'],relay=a['relayed'],tdr=a['tdr']['transfers_done'],
                         combined=a['relayed']+a['tdr']['transfers_done'],relay_rate=a['relayed']/a['n'],
                         throughput=a['throughput_ctx_per_s'],effective_tdr=doc['effective_tdr']))
    if not rows:
        print('No completed runs yet; no statistics or plots created.');return 0
    df=pd.DataFrame(rows);df.to_csv(out/'completed_runs.csv',index=False,encoding='utf-8-sig')
    stats=[]
    for (sweep,value),g in df.groupby(['sweep','value'],sort=False):
        r=dict(sweep=sweep,value=value,repeats=len(g))
        for col in ['relay','tdr','combined','relay_rate']:
            v=g[col];r.update({col+'_mean':v.mean(),col+'_median':v.median(),col+'_q1':v.quantile(.25),
                              col+'_q3':v.quantile(.75),col+'_sd':v.std(),col+'_min':v.min(),col+'_max':v.max()})
        stats.append(r)
    table=pd.DataFrame(stats);table.to_csv(out/'parameter_summary.csv',index=False,encoding='utf-8-sig')
    common={};ranking={};paired=[]
    rng=np.random.default_rng(29029)
    for sweep,spec in manifest['sweep_space'].items():
        sets=[set(df[(df.sweep==sweep)&(df.value==v)]['round']) for v in spec['values']]
        common[sweep]=sorted(set.intersection(*sets))
        if not common[sweep]:continue
        g=df[(df.sweep==sweep)&df['round'].isin(common[sweep])]
        means=g.groupby('value')[['relay','combined']].mean()
        ranking[sweep]={col:float(means[col].idxmin()) for col in ['relay','combined']}
        if len(common[sweep])<3:continue
        for metric in ['relay','combined']:
            best=ranking[sweep][metric];ref=g[g.value==best].set_index('route_seed')[metric]
            for value,current in g.groupby('value'):
                cur=current.set_index('route_seed')[metric];seeds=cur.index.intersection(ref.index)
                d=(cur.loc[seeds]-ref.loc[seeds]).to_numpy()
                boot=d[rng.integers(0,len(d),size=(10000,len(d)))].mean(axis=1)
                lo,hi=np.quantile(boot,[.025,.975])
                paired.append(dict(sweep=sweep,metric=metric,candidate=value,reference=best,
                                   paired_repeats=len(d),mean_difference=d.mean(),ci95_low=lo,ci95_high=hi))
    if paired:pd.DataFrame(paired).to_csv(out/'paired_differences.csv',index=False,encoding='utf-8-sig')
    report=[f'# exp029 新增参数敏感性\n\n完成{len(df)}/{len(manifest["tasks"])}次，smoke={manifest["smoke"]}。',
            'Relay+TDR逐次相加后统计，一次完成搬运计一次。仅统计完成且通过完整性与守恒门的运行。',
            '每点仅1次时，均值与中位数就是该次观测，只用于查看初步趋势，不估计统计显著性。',
            f'共同完整轮次：{common}。共同轮次均值最低观测点：{ranking}；不等于已证明的全局最优。',
            '|扫描|值|n|Relay均值|Relay中位数|TDR均值|总和均值|总和中位数|',
            '|---|---:|---:|---:|---:|---:|---:|---:|']
    for sweep in manifest['sweep_space']:
        for _,r in table[table.sweep==sweep].sort_values('value').iterrows():
            report.append(f'|{sweep}|{r.value:g}|{r.repeats}|{r.relay_mean:.2f}|{r.relay_median:.2f}|{r.tdr_mean:.2f}|{r.combined_mean:.2f}|{r.combined_median:.2f}|')
    report+=['\n注意：epsilon=1是单侧Top-up的无触发边界，仍保留需求观测、轮询和目标计算；TDR次数应为零。它不能与接近1的正常触发策略混为一谈。',
             '本实验默认只扫新增epsilon与q_min区间，不合并历史旧点。判断完整U型需结合原扫描的左右范围及相同负载/实现条件；不因新增区间单调就强行拟合二次曲线。',
             '配对差值正值表示候选比该指标样本最低点更差。bootstrap区间是探索性结果，未校正样本挑选赢家和多重比较。每轮的各点使用相同路由种子；轮间种子为7,17,…,97。']
    (out/'analysis_report.md').write_text('\n\n'.join(report).replace('|\n\n|','|\n|'),encoding='utf-8')
    metadata=dict(source=str(source),completed=len(df),planned=len(manifest['tasks']),common_rounds=common,
                  empirical_best=ranking,raw_revalidated=args.verify_raw,source_sha256=hashes,
                  epsilon_one_boundary=True,historical_data_pooled=False)
    (out/'decision.json').write_text(json.dumps(metadata,indent=2,ensure_ascii=False),encoding='utf-8')
    if not args.no_plots:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.ticker import StrMethodFormatter
        from matplotlib.patches import Patch
        for sweep in manifest['sweep_space']:
            groups=list(df[df.sweep==sweep].groupby('value',sort=True))
            if not groups:continue
            single_round=all(len(g)==1 for _,g in groups)
            fig,left=plt.subplots(figsize=(max(10,len(groups)*.9),5.8));right=left.twinx()
            pos=np.arange(len(groups))
            for ax,col,color,offset in [(left,'relay','#1876B5',-.17),(right,'combined','#D76436',.17)]:
                if single_round:
                    ax.plot(pos+offset,[g[col].iloc[0] for _,g in groups],color=color,linewidth=1.2,alpha=.8)
                for x,(_,g) in zip(pos,groups):
                    vals=g[col].to_numpy()
                    if len(vals)>1:
                        bp=ax.boxplot([vals],positions=[x+offset],widths=.28,patch_artist=True,manage_ticks=False,
                                      showfliers=False,medianprops={'color':'#25313D'})
                        bp['boxes'][0].set(facecolor=color,alpha=.35)
                    ax.scatter(x+offset+np.linspace(-.05,.05,len(vals)),vals,s=13,color=color,zorder=3)
                ax.set_ylim(bottom=0);ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
                ax.tick_params(axis='y',colors=color)
            left.set_xticks(pos,[f'{v:g}\nn={len(g)}' for v,g in groups]);left.set_xlabel(sweep)
            left.set_ylabel('Relay CTX (count)',color='#1876B5');right.set_ylabel('Relay CTX + TDR CTX (count)',color='#D76436')
            left.set_xlim(-.6,len(groups)-.4);left.grid(axis='y',alpha=.2)
            left.set_title(f'Exp029 {sweep} sensitivity | '+str(manifest['sweep_space'][sweep]['fixed']))
            left.legend(handles=[Patch(facecolor='#1876B5',alpha=.35,label='Relay: left axis'),
                                 Patch(facecolor='#D76436',alpha=.35,label='Relay + TDR: right axis')],loc='upper center',ncol=2,frameon=False)
            caption=('One run per point | dots: observations; lines: sampled trends | independent vertical scales'
                     if single_round else 'Completed runs only | box: Q1-Q3; line: median; dots: runs | independent vertical scales')
            fig.text(.5,.015,caption,ha='center',fontsize=9)
            fig.tight_layout(rect=[0,.05,1,1])
            for ext in ['png','svg']:fig.savefig(out/f'sensitivity_{sweep}.{ext}',dpi=180)
            plt.close(fig)
    print(table[['sweep','value','repeats','relay_mean','tdr_mean','combined_mean']].round(2).to_string(index=False))
    print(f'Analysis: {out}');return 0

if __name__=='__main__':raise SystemExit(main())
