"""Draft measured comparison; upper boxes refer specifically to baseline assignments."""
from pathlib import Path
import argparse,csv,json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

def analyze(out):
    out=Path(out);m=json.loads((out/'manifest.json').read_text());base=Path(m['baseline']);b=json.loads((base/'manifest.json').read_text())
    data=[]
    for origin,tasks in [(base,b['tasks']),(out,m['tasks'])]:
        for t in tasks:
            if t['status']!='complete':continue
            folder=origin/t['attempts'][-1]['path'];doc=json.loads((folder/'summary.json').read_text())
            import hashlib
            assert hashlib.sha256((folder/'per_broker.csv').read_bytes()).hexdigest()==doc['derived']['per_broker.csv']
            rows=list(csv.DictReader((folder/'per_broker.csv').open(encoding='utf-8-sig')));r=next(r for r in rows if int(r['broker_idx'])==19)
            data.append(dict(method=t['policy'],round=t['round'],transactions=t['transactions'],assigned=int(r['assigned']),served=int(r['served']),relay=int(r['relay']),tdr_transfers=int(r['tdr_transfers']),profit_gwei=int(r['net_profit_wei'])/1e9,own_upper_gwei=int(r['upper_wei'])/1e9))
    xs=sorted({r['transactions'] for r in data});fig=plt.figure(figsize=(125.6/72,134.7/72));ax=fig.add_axes([25.5/125.6,24.2/134.7,93.7/125.6,94/134.7])
    handles=[]
    for method,key,color,offset,label in [('topup','profit_gwei','#ef6548',-.27,'Broker with TDR'),('autoloop','profit_gwei','#e8a11b',-.09,'Autoloop'),('spider','profit_gwei','#9467bd',.09,'Spider-rebalance'),('topup','own_upper_gwei','#28a8f2',.27,'Potential profit of Broker 19')]:
        for i,n in enumerate(xs):
            values=[r[key] for r in data if r['method']==method and r['transactions']==n]
            if values:ax.boxplot([values],positions=[i+offset],widths=.15,patch_artist=True,manage_ticks=False,boxprops=dict(facecolor=color,edgecolor=color,alpha=.6,linewidth=.4),whiskerprops=dict(color=color,linewidth=.4),capprops=dict(color=color,linewidth=.4),medianprops=dict(color='black',linewidth=.3),flierprops=dict(marker='.',markersize=1.2,markeredgecolor=color))
        handles.append(Patch(facecolor=color,label=label))
    ax.set_xticks(range(len(xs)),[f'{n//1000}k' for n in xs],rotation=30);ax.tick_params(labelsize=5.14,pad=1,length=1.8,width=.35)
    ax.set_xlabel('Number of user transactions',fontsize=6.705,labelpad=3);ax.set_ylabel('Profit of broker 19 (Gwei)',fontsize=6.3,labelpad=2)
    ax.grid(axis='y',linestyle=':',linewidth=.3,alpha=.4);ax.set_axisbelow(True);ax.legend(handles=handles,fontsize=4.0,loc='best',handlelength=.8)
    for sp in ax.spines.values():sp.set_linewidth(.35)
    fig.savefig(out/'fig5b_comparison_draft.png',dpi=400);plt.close(fig)
    with (out/'broker19_comparison.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    (out/'comparison_notes.txt').write_text('Each box uses independent whole rounds. Broker 19 sums all subaccounts. Upper bound boxes use With TDR actual assigned transactions only; other policies may receive different assignments. All per-method assigned counts and own bounds are included in broker19_comparison.csv. Draft uses linear y axis. No fabricated variance. Completed supplement tasks: '+str(sum(t['status']=='complete' for t in m['tasks']))+'/160.',encoding='utf-8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'out/matched_transactions');analyze(p.parse_args().output)
