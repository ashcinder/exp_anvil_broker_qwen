"""Package-only sensitivity review. Retains old figures; creates a new dated folder."""
from pathlib import Path
from datetime import datetime
import json, re, statistics as st, csv, sys, hashlib
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from brokerlab.plotting import plt, COLOR_RELAY, COLOR_ALT, COLOR_BROKER
PKG=Path(__file__).resolve().parents[1]
OUT=PKG/'results'/('relay_tdr_analysis_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
OUT.mkdir(parents=True,exist_ok=False)
rows=[]; excluded=[]; manifest=[]
for p in sorted((PKG/'data/summaries').glob('*.json')):
    d=json.loads(p.read_text(encoding='utf-8')); a=d['arms'][0]
    manifest.append(dict(file=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    valid=all(a['gates'].values()) and a['burn_reconcile']['ok'] and a['failed']==0 and not a['coord']['aborted']
    if not valid:
        excluded.append(dict(file=p.name,failed=a['failed'],gates=a['gates'])); continue
    m=re.search(r'arm_Y_(hl|q)([\d.]+)_r(\d+)',p.stem)
    if not m: continue
    R=a['relayed']; T=a['tdr']['transfers_done']; L=a['legs']['tdr_legs']
    assert a['n']==40000 and a['fund_eth']==45 and a['tdr']['transfers_lost']==0
    assert L==2*T
    rows.append(dict(sweep='half_life' if m[1]=='hl' else 'q_min',x=float(m[2]),rep=int(m[3]),
                     relay=R,tdr=T,total=R+T,relay_legs=2*R,tdr_legs=L,total_legs=2*R+L,
                     relay_pct=100*R/a['n'],total_pct=100*(R+T)/a['n'],source=str(p)))
with (OUT/'per_run.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
groups={s:{} for s in ['half_life','q_min']}
for r in rows: groups[r['sweep']].setdefault(r['x'],[]).append(r)
agg=[]
for s,g in groups.items():
    for x,rs in sorted(g.items()):
        z=dict(sweep=s,x=x,n=len(rs))
        for metric in ['relay','tdr','total','total_legs','relay_pct','total_pct']:
            vals=[r[metric] for r in rs]
            for key,value in [('median',st.median(vals)),('min',min(vals)),('max',max(vals))]: z[metric+'_'+key]=value
        agg.append(z)
with (OUT/'parameter_summary.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=list(agg[0]));w.writeheader();w.writerows(agg)

def finish(fig,name):
    fig.tight_layout(rect=(0,0.04,1,.96))
    for ext in ['png','pdf','svg']: fig.savefig(OUT/(name+'.'+ext),dpi=220,bbox_inches='tight')
    plt.close(fig)

def draw(ax,s,metric,color):
    points=[z for z in agg if z['sweep']==s];xs=[z['x'] for z in points]
    ys=[z[metric+'_median'] for z in points]
    lo=[z[metric+'_min'] for z in points];hi=[z[metric+'_max'] for z in points]
    ax.fill_between(xs,lo,hi,color=color,alpha=.15,label='Min-max range')
    ax.plot(xs,ys,'o-',color=color,lw=1.8,label='Median')
    for z in points:
        vals=[r[metric] for r in groups[s][z['x']]]
        ax.scatter([z['x']]*len(vals),vals,color=color,alpha=.45,s=12,zorder=3)
    if s=='half_life':ax.set_xscale('log')
    ax.set_xticks(xs);ax.set_xticklabels([f'{x:g}' for x in xs]);ax.minorticks_off()
    if metric=='relay':ax.set_yscale('log')
    ax.grid(alpha=.22);ax.set_xlabel('EWMA half-life (blocks)' if s=='half_life' else 'Minimum target ratio q_min')
    ax.set_ylabel({'relay':'Relay CTX (count)','total':'Relay CTX + TDR transfers (count)','total_legs':'Relay + TDR on-chain legs (count)'}[metric])
    best=min(points,key=lambda z:z[metric+'_median'])
    ax.scatter([best['x']],[best[metric+'_median']],s=140,facecolors='none',edgecolors='black',lw=1.1,zorder=5)
    ax.set_title(('q_min = 0.3' if s=='half_life' else 'Half-life = 10 blocks')+f"; lowest median @ {best['x']:g}")
    ax.legend(loc='best',frameon=False)

fig,axs=plt.subplots(2,2,figsize=(12,8))
for col,s in enumerate(groups):
    draw(axs[0,col],s,'relay',COLOR_RELAY);draw(axs[1,col],s,'total',COLOR_ALT)
fig.suptitle('Regime-shift sensitivity: service fallback and combined activity',fontsize=12)
fig.text(.5,.015,'40,000 CTX/run; 45 ETH/broker/shard; epsilon=0.95. Valid runs: n=6, except half-life 20: n=5.\nPoints are runs; bands are min-max, not confidence intervals. Combined totals are summed per run before aggregation.',ha='center',fontsize=8)
finish(fig,'01_relay_and_combined_cost')
fig,axs=plt.subplots(1,2,figsize=(12,4.6))
for col,s in enumerate(groups):draw(axs[col],s,'total_legs',COLOR_BROKER)
fig.suptitle('Equivalent on-chain accounting: 2 x Relay CTX + TDR legs',fontsize=12)
fig.text(.5,.015,'Every valid TDR transfer has 2 legs; therefore this metric equals 2 x (Relay CTX + TDR transfers).',ha='center',fontsize=8)
finish(fig,'02_onchain_legs')

def mdtable(s):
    txt=['| 参数 | n | Relay中位 | TDR搬运中位 | R+T中位（最小–最大） | R+T / 40000 |','|---:|---:|---:|---:|---:|---:|']
    for z in agg:
        if z['sweep']!=s:continue
        txt.append(f"| {z['x']:g} | {z['n']} | {z['relay_median']:g} | {z['tdr_median']:g} | {z['total_median']:g}（{z['total_min']:g}–{z['total_max']:g}） | {z['total_pct_median']:.3f}% |")
    return '\n'.join(txt)
report='''# submit_regime_shift 参数敏感性：Relay与TDR总活动量

数据仅取本交付包data/summaries的96份摘要，不使用exp011后续补跑。95份有效，其中77份为敏感性样本（hl41份，q_min36份），其余18份为baseline。本次排除hl20第6轮：failed344，G1/G3a失败。其余参数每点6轮，hl20为5轮。

计数口径：R=成功通过Relay完成的用户CTX；T=完成的TDR资金转移（transfers_done）。主指标R+T按逻辑跨片活动计数，一次TDR不是事件数events_opened。辅助链上口径是2R+tdr_legs；本批所有有效场没有lost，每次搬运2段，因此等于2(R+T)，候选排名相同。R+T仅计算用户指定的Relay与TDR活动，不包含broker服务的用户CTX，不是系统全部交易量或精确gas/网络成本。相同权重也是这个指标的明确假设。

每场先计算R+T，再在同参数多场中取中位与极值；不能用median(R)+median(T)替代median(R+T)。每场用户CTX40000，45ETH/broker/片，epsilon.95。hl扫描q_min.3；q扫描hl10。两条扫描不能证明二维联合最优。

## 半衰期

'''+mdtable('half_life')+'\n\n## q_min\n\n'+mdtable('q_min')+'''

## 结果解读

半衰期：仅Relay中位最低在20（111）；R+T中位最低在400（2947.5），150为2948，50为2952，三者差异极小且区间重叠。hl20的总和2996，相对400只多48.5（约1.65%），却有更少Relay。hl2到20总和减少约39.53%；20到400仅再减少约1.62%。因此总活动量曲线下降后趋于平台，不能声称hl20也是总开销唯一最优。

q_min：Relay和R+T最低中位都在0.3。0.3总和3229.5，0.5为3263.5，仅多34（约1.05%），区间重叠；但是0.5的Relay301约为0.3的2.04倍。0.7的总和9928约为0.3的3.07倍，0.9为26876约8.32倍。因此0.3兼顾服务能力和总活动量，0.3/0.5属于总开销接近区间，不能仅凭小差异声称总开销显著更优。

Relay与R+T最优候选可能不同：短半衰期过度追逐波动，TDR代价高；长半衰期略有更多Relay，但搬运进一步减少。q_min过高显著增加Relay，搬运节省无法抵消回退增加；中等q_min取得更低总活动量。实际最低点见表，不把最低样本中位解释为统计显著或全局最优。范围带为min-max，不是置信区间。固定trace/seed的多轮主要反映执行波动。

图1上排Relay采用对数纵轴，便于查看数量级；下排R+T采用线性纵轴，横向比较总量。半衰期横轴对数，q_min横轴线性；没有拟合平滑曲线或人为制造U形。图2提供链上段数口径。所有图保留原始散点和极值带，最低中位候选用空心圈标出。

文件：01_relay_and_combined_cost、02_onchain_legs均有PNG/PDF/SVG；per_run.csv逐次结果；parameter_summary.csv聚合；source_manifest.json记录SHA256与排除清单。代码使用仓库brokerlab.plotting既有样式，原图未覆盖。
'''
(OUT/'分析说明.md').write_text(report,encoding='utf-8')
(OUT/'source_manifest.json').write_text(json.dumps(dict(sources=manifest,excluded=excluded,valid_sensitivity_runs=len(rows),metrics={'R':'relayed','T':'tdr.transfers_done','combined':'per-run R+T','onchain':'2R+legs.tdr_legs'}),ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'index.html').write_text('<meta charset="utf-8"><title>Relay与TDR参数敏感性</title><style>body{font-family:Arial;max-width:1250px;margin:30px auto}img{width:100%}</style><h1>submit_regime_shift：Relay与TDR参数敏感性</h1><p>仅交付包有效数据；hl20 n=5，其余n=6。总和先逐场计算再聚合。</p><img src="01_relay_and_combined_cost.png"><img src="02_onchain_legs.png"><p><a href="分析说明.md">分析说明</a> · <a href="parameter_summary.csv">汇总数据</a> · <a href="per_run.csv">逐次数据</a></p>',encoding='utf-8')
print('OUTPUT='+str(OUT))
print(mdtable('half_life'));print(mdtable('q_min'))
