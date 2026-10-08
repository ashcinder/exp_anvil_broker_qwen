"""All exp014 brokers/shards: actual Fig5b balance/target/bounds and TDR events."""
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

import argparse

BASE=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description='Plot all broker/shard balance snapshots and confirmed TDR events.')
parser.add_argument('--arm-dir', type=Path, required=True, help='Completed strategy directory containing balance_snapshots.csv and tdr_move_events.csv')
parser.add_argument('--out-dir', type=Path, help='Gallery output directory (default: strategy directory/balance_gallery)')
parser.add_argument('--max-block', type=int, default=400)
parser.add_argument('--epsilon', type=float, help='Override epsilon inferred from the strategy directory name')
args=parser.parse_args()
ARM=args.arm_dir.resolve()
OUT=args.out_dir.resolve() if args.out_dir else ARM/'balance_gallery'
MAX_BLOCK=args.max_block
EPSILON=args.epsilon if args.epsilon is not None else float(ARM.name.split('@')[2])
if MAX_BLOCK <= 0 or not 0 <= EPSILON < 1:
    parser.error('max-block must be positive and epsilon must be in [0,1)')
EPS_SCALE=int(EPSILON*1000000)
BLUE,ORANGE,RED,GRAY='#28A8F2','#FFA537','#DE2B03','#888888'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    snapshots=defaultdict(list)
    with (ARM/'balance_snapshots.csv').open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            if int(r['block'])<=MAX_BLOCK:
                snapshots[int(r['broker_idx'])].append(r)
    moves=defaultdict(list)
    with (ARM/'tdr_move_events.csv').open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            if int(r['burn_block']) > 0 and int(r['mint_block']) > 0:
                moves[int(r['broker_idx'])].append(r)
    assert set(snapshots)==set(range(50))
    plt.rcParams.update({'font.family':'DejaVu Sans','font.weight':'bold','axes.labelweight':'bold',
                         'axes.titleweight':'bold','font.size':17,'pdf.fonttype':42})
    handles=[Line2D([0],[0],color=GRAY,ls='--',lw=1.8,label='Upper bound'),
             Line2D([0],[0],color=GRAY,ls=':',lw=1.8,label='Lower bound'),
             Line2D([0],[0],color=BLUE,lw=3,label='Current balance'),
             Line2D([0],[0],color=ORANGE,lw=3.5,label='Target balance'),
             Line2D([0],[0],color=RED,marker='o',ls='None',label='Confirmed TDR out'),
             Line2D([0],[0],color=RED,marker='^',ls='None',label='Confirmed TDR in')]
    summary=[]
    for broker in range(50):
        folder=OUT/f'broker_{broker:02d}'
        folder.mkdir(exist_ok=True)
        rows=sorted(snapshots[broker],key=lambda r:int(r['block']))
        x=np.array([int(r['block']) for r in rows])
        assert len(set(x))==len(x)
        series={};events={};export=[];eventexport=[]
        for s in range(16):
            beta=np.array([int(r[f'shard_{s}_balance'])/1e18 for r in rows])
            tau=[];upper=[];lower=[]
            for r in rows:
                raw=r[f'shard_{s}_tau']
                if raw:
                    v=int(raw);margin=v*EPS_SCALE//1000000
                    assert int(r[f'shard_{s}_upper_bound'])==v+margin
                    tau.append(v/1e18);upper.append((v+margin)/1e18);lower.append((v-margin)/1e18)
                else:
                    tau.append(np.nan);upper.append(np.nan);lower.append(np.nan)
            series[s]=tuple(np.array(a) for a in (beta,tau,upper,lower))
            events[s]=[]
            for r in moves[broker]:
                for shard,block,kind in ((int(r['src_shard']),int(r['burn_block']),'out'),
                                          (int(r['dst_shard']),int(r['mint_block']),'in')):
                    if shard==s and x[0]<=block<=MAX_BLOCK:
                        i=np.searchsorted(x,block,side='right')-1
                        events[s].append((block,beta[i],kind))
                        eventexport.append(dict(broker=broker,shard=s,block=block,direction=kind,
                            amount_wei=r['amount_wei'],burn_block=r['burn_block'],mint_block=r['mint_block']))
            for i,block in enumerate(x):
                export.append(dict(block=int(block),broker=broker,shard=s,balance_eth=beta[i],
                    target_eth=tau[i],upper_eth=upper[i],lower_eth=lower[i]))

        def draw(ax,s,lw,points):
            beta,tau,upper,lower=series[s]
            ax.fill_between(x,lower,upper,step='post',color=ORANGE,alpha=.09,zorder=1)
            ax.step(x,upper,where='post',color=GRAY,ls='--',lw=lw*.6,zorder=2)
            ax.step(x,lower,where='post',color=GRAY,ls=':',lw=lw*.6,zorder=2)
            ax.step(x,beta,where='post',color=BLUE,lw=lw,zorder=4)
            ax.step(x,tau,where='post',color=ORANGE,lw=lw*1.15,zorder=3)
            for kind,marker in (('out','o'),('in','^')):
                selected=[e for e in events[s] if e[2]==kind]
                if selected:
                    ax.scatter([e[0] for e in selected],[e[1] for e in selected],s=points,
                        color=RED,marker=marker,edgecolor='white',lw=.6,zorder=7)
            ax.set_xlim(0,MAX_BLOCK);ax.set_xticks(np.linspace(0,MAX_BLOCK,6,dtype=int))
            ax.grid(axis='y',linestyle=':',alpha=.45)

        fig,axs=plt.subplots(4,4,figsize=(16,12),sharex=True)
        for s,ax in enumerate(axs.flat):
            draw(ax,s,1.7,30)
            ax.set_title(f'Shard {s}',fontsize=13);ax.tick_params(labelsize=9)
        fig.suptitle(f'Broker {broker:02d} - TDR bounds and confirmed transfers (epsilon={EPSILON:g})',fontsize=19,fontweight='bold')
        fig.supxlabel('Block heights',fontsize=18,y=.08);fig.supylabel('Balance (ETH)',fontsize=18)
        fig.legend(handles=handles,loc='lower center',ncol=3,fontsize=11,frameon=False)
        fig.subplots_adjust(left=.065,right=.985,bottom=.13,top=.92,hspace=.35,wspace=.30)
        stem=f'broker_{broker:02d}_all16_bounds'
        fig.savefig(folder/f'{stem}.png',dpi=170,bbox_inches='tight',facecolor='white')
        fig.savefig(folder/f'{stem}.pdf',bbox_inches='tight',facecolor='white')
        plt.close(fig)
        for s in range(16):
            fig,ax=plt.subplots(figsize=(10,8))
            draw(ax,s,3,95)
            ax.set_xlabel('Block heights',fontsize=30,labelpad=12)
            ax.set_ylabel(f"Broker {broker}'s balance in shard {s} (ETH)",fontsize=24,labelpad=12)
            ax.tick_params(labelsize=23)
            fig.legend(handles=handles,loc='lower center',ncol=2,fontsize=12,framealpha=1,edgecolor='#999999')
            fig.subplots_adjust(left=.15,right=.98,bottom=.31,top=.96)
            fig.savefig(folder/f'broker_{broker:02d}_shard_{s:02d}_bounds.png',dpi=160,bbox_inches='tight',facecolor='white')
            plt.close(fig)
        for name,data in (('balance_and_bounds.csv',export),('tdr_events.csv',eventexport)):
            with (folder/name).open('w',encoding='utf-8',newline='') as f:
                fields=list(data[0]) if data else ['broker','shard','block','direction','amount_wei','burn_block','mint_block']
                w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(data)
        links=' '.join(f'<a href="#shard{s}">Shard {s}</a>' for s in range(16))
        cards=''.join(f'<section id="shard{s}"><h2>Broker {broker:02d} · Shard {s}</h2><img loading="lazy" src="broker_{broker:02d}_shard_{s:02d}_bounds.png"></section>' for s in range(16))
        (folder/'index.html').write_text(f'<!doctype html><meta charset="utf-8"><title>Broker {broker:02d} Fig5b</title><style>body{{font-family:Arial;padding:24px}}img{{width:100%;max-width:1300px}}section{{margin:30px 0}}a{{margin-right:12px}}</style><a href="../index.html">返回全部 broker</a><h1>Broker {broker:02d} · 全部16分片</h1><p>{links}</p><a href="{stem}.pdf">总览矢量 PDF</a><img src="{stem}.png">{cards}',encoding='utf-8')
        summary.append(dict(broker=broker,snapshots=len(rows),first_block=int(x[0]),last_block=int(x[-1]),
            event_markers=len(eventexport),shards_with_events=sum(bool(events[s]) for s in range(16))))
        print(f'[{broker+1}/50] Broker {broker:02d}: 16 PNGs + overview PNG/PDF; {len(eventexport)} confirmed event markers',flush=True)
    cards=''.join(f'<section data-broker="{b:02d}"><h2>Broker {b:02d}</h2><a href="broker_{b:02d}/index.html">展开全部16分片单独图</a> · <a href="broker_{b:02d}/broker_{b:02d}_all16_bounds.pdf">总览 PDF</a><img loading="lazy" src="broker_{b:02d}/broker_{b:02d}_all16_bounds.png"></section>' for b in range(50))
    html=f'<!doctype html><meta charset="utf-8"><title>Broker balance gallery</title><style>body{{font-family:Arial;margin:30px;background:#f3f4f6;color:#222}}section{{padding:20px;margin:24px 0;background:white;border-radius:8px}}img{{display:block;max-width:100%;margin-top:12px}}input{{font-size:18px;padding:10px}}a{{color:#0969da}}</style><h1>All brokers / shards - epsilon={EPSILON:g}</h1><p>Blocks 0-{MAX_BLOCK}. Actual confirmed balances and last decision targets. Targets may remain held during active TDR/cooldown. Bounds: (1-epsilon)*tau and (1+epsilon)*tau. Markers show source burn / destination mint confirmations on independent shard clocks. Panel y-axes adapt independently.</p><input id="q" placeholder="Broker number 0-49">'+cards+'<script>document.querySelector("#q").oninput=e=>{let q=e.target.value.trim();document.querySelectorAll("section").forEach(s=>s.hidden=q!==""&&Number(s.dataset.broker)!==Number(q))}</script>'
    (OUT/'index.html').write_text(html,encoding='utf-8')
    manifest=dict(experiment='exp014',arm=ARM.name,broker_count=50,shard_count=16,
        block_range=[0,MAX_BLOCK],epsilon=EPSILON,trigger_mode='topup_shortage_with_surplus_donors',lower_bound_triggers=True,
        data='Actual policy snapshots, targets, upper bounds; derived lower bounds use exact integer margin; no interpolation of missing target observations',
        markers='Measured source burn / destination mint heights, y from last policy snapshot at or before height; clocks are independent',
        sources=[dict(path=str(ARM/n),sha256=hashlib.sha256((ARM/n).read_bytes()).hexdigest()) for n in ('balance_snapshots.csv','tdr_move_events.csv')],
        summaries=summary,expected_files=dict(overview_pdf=50,overview_png=50,individual_shard_png=800))
    assert len(list(OUT.glob('broker_*/*_shard_*_bounds.png')))==800
    assert len(list(OUT.glob('broker_*/*_all16_bounds.pdf')))==50
    (OUT/'source_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Finished: {OUT}',flush=True)

if __name__=='__main__':
    main()
