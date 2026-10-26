"""One figure, fourteen matched-condition methods, one profit=0 boundary."""
from pathlib import Path
import argparse,importlib.util,sys,json,csv,statistics,math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pymupdf as fitz
from protocol import METHODS,PROTOCOL,validate_protocol
import label_layout as layout
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
from brokerlab.config import load_config

def economics(served,baseline_served,transfers,kappa,mu):
 return kappa*(served-baseline_served)-mu*transfers

def load_rows(source,allow_analysis=False):
 manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
 report=json.loads((source/'report.json').read_text(encoding='utf-8'))
 if manifest.get('experiment')!='exp043' or manifest.get('protocol_revision')!=PROTOCOL:
  raise ValueError('Only new matched exp043 results accepted; historical runs rejected')
 cfg=load_config(source/'params.json')
 validate_protocol(cfg,smoke=manifest.get('smoke',False),analysis=manifest.get('analysis_pass',False))
 spec=importlib.util.spec_from_file_location('checked_exp043',ROOT/'experiments/exp033_sota_comparison/plot_pareto.py')
 checked=importlib.util.module_from_spec(spec);spec.loader.exec_module(checked)
 rows=checked.load([str(source)])
 if set(r['arm'] for r in rows)!=set(METHODS):raise ValueError('All fourteen methods required')
 if not report['status']['complete'] or len(rows)!=len(manifest['tasks']):raise ValueError('Incomplete run')
 formal=manifest['formal'] and report['formal'] and len(rows)==42 and all(r['n']==40000 for r in rows)
 if not formal and not allow_analysis:raise ValueError('Not formal; use --allow-analysis for a labeled preview')
 for r in rows:
  t=next(t for t in manifest['tasks'] if t['round']==r['round'] and t['value']==r['arm'])
  st=json.loads((source/t['summary']).read_text(encoding='utf-8'))['arms'][0]
  if not math.isclose(r['moved_eth'],st['tdr']['moved_wei']/1e18,rel_tol=1e-10,abs_tol=1e-8):raise ValueError('Moved-volume mismatch')
 return rows,cfg,formal

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--experiment-source',type=Path,required=True)
 ap.add_argument('--output',type=Path,required=True);ap.add_argument('--heatmap-end-s',type=float,default=684,help=argparse.SUPPRESS)
 ap.add_argument('--allow-analysis',action='store_true');args=ap.parse_args()
 rows,cfg,formal=load_rows(args.experiment_source,args.allow_analysis)
 kappa=float(cfg.exp['model_income_per_service']);mu=float(cfg.exp['model_cost_per_transfer']);rho=kappa/mu
 baseline={r['round']:r for r in rows if r['arm']=='plain'}
 for r in rows:r['profit_delta']=economics(r['served'],baseline[r['round']]['served'],r['transfers'],kappa,mu)
 groups={a:[r for r in rows if r['arm']==a] for a in METHODS}
 points={a:(statistics.mean(r['relay'] for r in g),statistics.mean(r['transfers'] for r in g)) for a,g in groups.items()}
 R0=points['plain'][0]
 layout.NAMES.update({a:v[0] for a,v in METHODS.items()});layout.NAMES['topup_e0']=r'Top-up + EWMA ($\epsilon=0$)'
 plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.labelsize':11,'axes.labelweight':'bold','xtick.labelsize':10,'ytick.labelsize':10,'pdf.fonttype':42})
 fig,ax=plt.subplots(figsize=(177.942341/25.4,4.9));fig.subplots_adjust(left=.145,right=.98,top=.975,bottom=.15)
 ax.set_xscale('symlog',linthresh=100);ax.set_yscale('symlog',linthresh=3000)
 ax.set_xlim(-8,max(100,max(x for x,y in points.values())*1.5));ax.set_ylim(-40,max(1000,max(y for x,y in points.values())*1.5))
 if R0>0:
  xs=np.r_[np.linspace(0,min(100,R0),200),np.geomspace(min(100,R0),R0,400)]
  ax.plot(xs,rho*(R0-xs),'--',color='black',lw=1.5,zorder=2)
  ax.text(.60,.45,'profit = 0',transform=ax.transAxes,fontsize=10,weight='bold',bbox=dict(facecolor='white',edgecolor='none',pad=2))
 for a,(x,y) in points.items():
  g=groups[a];component=a in ('prop_hard','prop_ewma','topup_hard','topup_e0')
  color='#c53616' if a=='topup' else '#7b3eb1' if component else '#777777' if a=='plain' else '#22a7e8'
  marker='*' if a=='topup' else 's' if a=='plain' else 'D' if component else 'o'
  if len(g)>1:ax.errorbar(x,y,xerr=[[x-min(r['relay'] for r in g)],[max(r['relay'] for r in g)-x]],yerr=[[y-min(r['transfers'] for r in g)],[max(r['transfers'] for r in g)-y]],fmt='none',ecolor=color,elinewidth=.8,capsize=2,zorder=3)
  ax.scatter(x,y,s=155 if a=='topup' else 85 if a in ('flexichain','plain') else 52,marker=marker,facecolor='white' if a in ('flexichain','plain') else color,edgecolor=color if a=='flexichain' else 'black',linewidth=.8,zorder=4)
 ax.set_xlabel('Relay-triggered CTXs');ax.set_ylabel('TDR-triggered relay CTXs')
 ax.xaxis.set_major_formatter(FuncFormatter(lambda v,p:f'{v:,.0f}'));ax.yaxis.set_major_formatter(FuncFormatter(lambda v,p:f'{v:,.0f}'))
 ax.grid(color='.9',lw=.5,zorder=0)
 if not formal:ax.text(.02,.98,'ANALYSIS ONLY',transform=ax.transAxes,va='top',weight='bold',color='red')
 overlap=layout.labels_without_overlap(fig,ax,points)
 args.output.mkdir(parents=True,exist_ok=True);pdf=args.output/'unified_tradeoff.pdf';fig.savefig(pdf)
 with fitz.open(pdf) as doc:
  p=doc[0];p.get_pixmap(matrix=fitz.Matrix(300/72,300/72),alpha=False).save(args.output/'unified_tradeoff.png')
  clipped=[s['text'] for b in p.get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] if not p.rect.contains(fitz.Rect(s['bbox']))]
 if overlap or clipped:raise ValueError(f'Data saved, figure needs label repair: overlaps={overlap}, clipped={clipped}')
 with (args.output/'point_values.csv').open('w',encoding='utf-8-sig',newline='') as out:
  w=csv.writer(out);w.writerow(['method','relay_mean','rebalancing_mean','profit_delta_mean','rounds'])
  for a,g in groups.items():
   x,y=points[a];p=statistics.mean(r['profit_delta'] for r in g)
   assert math.isclose(p,kappa*(R0-x)-mu*y,abs_tol=1e-7)
   w.writerow([METHODS[a][0],x,y,p,len(g)])
 with (args.output/'per_round_values.csv').open('w',encoding='utf-8-sig',newline='') as out:
  w=csv.writer(out);w.writerow(['method','round','seed','n','relay','served','rebalancing','moved_ETH','model_incremental_profit'])
  for r in rows:w.writerow([METHODS[r['arm']][0],r['round'],r['seed'],r['n'],r['relay'],r['served'],r['transfers'],r['moved_eth'],r['profit_delta']])
 (args.output/'figure_info.json').write_text(json.dumps(dict(protocol=PROTOCOL,formal=formal,source=str(args.experiment_source.resolve()),R0_mean=R0,kappa=kappa,mu=mu,
  economic_boundary='A=(kappa/mu)*(R0-R); post-hoc incremental model relative to single Plain',points='means; error bars=min-max',label_overlaps=overlap,clipped_text=clipped),indent=2),encoding='utf-8')
 print(args.output/'unified_tradeoff.pdf')
if __name__=='__main__':main()
