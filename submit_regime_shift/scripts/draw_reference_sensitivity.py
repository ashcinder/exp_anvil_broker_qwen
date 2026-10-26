"""Plot only this package using the same saved paper Fig6 code as exp025."""
from pathlib import Path
from types import SimpleNamespace
import csv
import gzip
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import pandas as pd
import matplotlib
matplotlib.use('Agg')

PKG = Path(__file__).resolve().parents[1]
OUT = PKG/'results/参数敏感性_论文样式_20261007'
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'code').mkdir(exist_ok=True)
REFERENCE = Path('D:/实验/exp8_latest_plotting/out/20260928_203441/最新实验图/code/adapted_redraw_fig6_paper.py')
rows, excluded, provenance = [], [], []
for path in sorted((PKG/'data/summaries').glob('*.json')):
    raw = path.read_bytes()
    doc = json.loads(raw)
    a = doc['arms'][0]
    provenance.append(dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest()))
    valid = (bool(a['gates']) and all(a['gates'].values()) and a['burn_reconcile']['ok']
             and a['failed']==0 and not a['coord'].get('aborted')
             and a['served']+a['relayed']==a['n']
             and a['tdr']['transfers_lost']==a['tdr']['lost_in_transit_wei']==0)
    if not valid:
        excluded.append(dict(file=path.name,failed=a['failed'],gates=a['gates']))
        continue
    match = re.fullmatch(r'arm_Y_(hl|q)([\d.]+)_r(\d+)',path.stem)
    if not match:
        continue
    exp = doc['params']['exp']
    sweep = 'half_life' if match[1]=='hl' else 'q_min'
    value, rep = float(match[2]), int(match[3])
    assert a['n']==40000 and a['fund_eth']==45 and exp['rate']==120
    assert exp['shift_cycle']=='11,3' and exp['shift_start_frac']==.3 and exp['shift_period_frac']==.04
    assert doc['params']['chain']['num_shards']==16 and doc['params']['scale']['num_brokers']==50
    token = a['arm'].split('@')
    assert token[0]=='topup' and float(token[1])==45 and float(token[2])==.95
    assert float(token[3])==(value if sweep=='half_life' else 10)
    assert float(token[4])==(.3 if sweep=='half_life' else value)
    ledger = PKG/'data/tdr_moves'/f'{path.stem}.csv.gz'
    with gzip.open(ledger,'rt',encoding='utf-8-sig',newline='') as stream:
        moves=list(csv.DictReader(stream))
    assert len(moves)==a['tdr']['transfers_done']
    # Burn and mint belong to different shard chains, so their heights need
    # not be ordered numerically; both must have a recorded confirmation.
    assert all(m['state']=='done' and min(int(m['burn_block']),int(m['mint_block']))>=0 for m in moves)
    assert sum(int(m['amount_wei']) for m in moves)==a['tdr']['moved_wei']
    provenance.append(dict(path=str(ledger),sha256=hashlib.sha256(ledger.read_bytes()).hexdigest()))
    rows.append(dict(file=path.name,sweep=sweep,value=value,rep=rep,route_seed=exp['route_seed'],
                     relay_ctx=a['relayed'],tdr_ctx=a['tdr']['transfers_done'],
                     total_ctx=a['relayed']+a['tdr']['transfers_done']))
df=pd.DataFrame(rows)
assert not df.duplicated(['sweep','value','rep']).any()
assert len(df)==77
df.to_csv(OUT/'all_valid_sensitivity_runs.csv',index=False,encoding='utf-8-sig')
common={s:sorted(set.intersection(*(set(g.rep) for _,g in sub.groupby('value'))))
        for s,sub in df.groupby('sweep')}
paired=pd.concat([df[(df.sweep==s)&df.rep.isin(rounds)] for s,rounds in common.items()])
assert len(paired)==71
paired.to_csv(OUT/'plotted_runs.csv',index=False,encoding='utf-8-sig')
stats=paired.groupby(['sweep','value']).agg(n=('rep','size'),
    relay_mean=('relay_ctx','mean'),relay_median=('relay_ctx','median'),
    tdr_mean=('tdr_ctx','mean'),total_mean=('total_ctx','mean'),total_median=('total_ctx','median')).reset_index()
stats.to_csv(OUT/'parameter_summary.csv',index=False,encoding='utf-8-sig')

code=REFERENCE.read_text(encoding='utf-8')
code=code.replace('"# of rebalancing transfers"','"Relay CTX"').replace('"Tokens moved (ETH)"','"Relay + TDR CTX"')
code=code.replace('"transfers"','"relay_ctx"').replace("'transfers'","'relay_ctx'")
code=code.replace('"moved_eth"','"total_ctx"').replace("'moved_eth'","'total_ctx'")
start=code.index('        note = ('); end=code.index('        fig.text(',start)
code=code[:start]+'''        note = (f"submit_regime_shift | 40k user CTX/run | {observed[name][xs[0]]['replicates']} common valid repetitions/point\\n"
                "Red/left: Relay CTX | Blue/right: Relay + completed TDR transfers | Independent y-scales")
'''+code[end:]
start=code.index('        for pos,x in enumerate(xs):\n            ax.text(');end=code.index('        ax.set_xlabel(',start)
code=code[:start]+code[end:]
code=code.replace('xtick_size = 32 if compact else 34','xtick_size = 28 if compact else 26')
adapter=OUT/'code/reference_plotter_adapted.py'
adapter.write_text(code,encoding='utf-8')
shutil.copy2(REFERENCE,OUT/'code/reference_original.py')
shutil.copy2(Path(__file__),OUT/'code/draw_reference_sensitivity.py')
spec=importlib.util.spec_from_file_location('submit_reference_plotter',adapter)
plotter=importlib.util.module_from_spec(spec);sys.modules[spec.name]=plotter;spec.loader.exec_module(plotter)
mapping={'half_life':'ewma_half_life','q_min':'ewma_q_min'}
samples,expected,observed={},{},{}
for sweep,name in mapping.items():
    sub=paired[paired.sweep==sweep]
    expected[name]=sorted(sub.value.unique());samples[name]={};observed[name]={}
    for value,g in sub.groupby('value'):
        g=g.sort_values('rep')
        assert g.rep.tolist()==common[sweep]
        samples[name][value]={'relay_ctx':g.relay_ctx.tolist(),'total_ctx':g.total_ctx.tolist()}
        observed[name][value]={'replicates':len(g)}
baseline={'ewma_half_life':20,'ewma_q_min':.3}
provider=SimpleNamespace(BASELINE_KEYS={n:n for n in mapping.values()},
    source_data=lambda run:({},baseline,expected,observed),replicate_samples=lambda *args:samples)
plotter.load_source=lambda:provider
plotter.RUN=PKG;plotter.STAGE=OUT
plotter.NAMES={'ewma_half_life':'fig6b_half_life_relay_total','ewma_q_min':'fig6c_qmin_relay_total'}
plotter.title_ticks=lambda name,xs:list(range(len(xs)))
plotter.main()
manifest=dict(data_source=str(PKG),reference_code=str(REFERENCE),
    reference_sha256=hashlib.sha256(REFERENCE.read_bytes()).hexdigest(),
    available_summaries=96,valid_sensitivity_runs=77,plotted_runs=71,excluded=excluded,
    common_repetitions=common,route_seeds=sorted(df.route_seed.unique().tolist()),
    raw_ctx_ledgers_available=False,tdr_ledgers_validated=True,
    metrics={'red_left':'relay_ctx','blue_right':'relay_ctx + transfers_done'},
    fixed_parameters={'half_life':{'q_min':.3,'epsilon':.95},'q_min':{'half_life':10,'epsilon':.95}},
    sources=provenance)
(OUT/'source_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
readme='''# submit_regime_shift 参数敏感性图

沿用实验25所用的指定目录 Fig6 绘图代码，输出300 dpi PNG和矢量PDF。红色左轴为Relay CTX；蓝色右轴为Relay+TDR CTX。TDR以transfers_done计，一次完成搬运计一次。逐次相加后形成箱线样本；两轴独立刻度且从0起。箱体Q1～Q3、白色中位数线、1.5 IQR箱须、黑色实测散点。灰色竖线是基准hl=20、q_min=.3，不是最优点；横轴按离散扫描点等距排布。

数据仅来自用户指定的本交付包。共96份摘要，其中1份arm_Y_hl20_r6失败（failed=344，G1/G3a失败），已剔除；其余95份有效，其中77份参数敏感性样本、18份baseline。逐笔CTX台账未提供；所有敏感性摘要与TDR gzip台账已核对搬运数量、状态、确认块号和搬运总量。

同实验25，优先使用各参数点共同有效的重复编号：半衰期各点r1～r5，q_min各点r1～r6，71次观测入图。所有route_seed=7，重复编号主要控制样本覆盖，不代表不同随机种子。全部77次有效扫描数据另存all_valid_sensitivity_runs.csv。本包没有epsilon扫描数据。

扫描固定值：hl固定q_min=.3、epsilon=.95；q_min固定hl=10、epsilon=.95。负载为step+flicker；每次40,000用户CTX，每broker每分片45 ETH，16分片、50 broker。两项均越小越好，但不能把不同扫描谷底拼成已验证联合最优。

parameter_summary.csv为绘图样本汇总；plotted_runs.csv为入图原始值；source_manifest.json记录校验和排除信息；code保存原绘图程序及适配代码。

## 绘图样本汇总

|扫描|值|n|Relay均值|Relay中位数|总CTX均值|总CTX中位数|
|---|---:|---:|---:|---:|---:|---:|
'''
for _,r in stats.iterrows():
    readme+=f'|{r.sweep}|{r.value:g}|{int(r.n)}|{r.relay_mean:.2f}|{r.relay_median:.2f}|{r.total_mean:.2f}|{r.total_median:.2f}|\n'
(OUT/'README.md').write_text(readme,encoding='utf-8')
print(stats.round(2).to_string(index=False));print('OUTPUT:',OUT)
