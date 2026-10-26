"""Export individual vector PDFs from an existing broker balance gallery."""
import argparse
import csv
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gallery-dir', type=Path, required=True)
parser.add_argument('--broker', type=int, default=25)
args=parser.parse_args()
folder=args.gallery_dir/f'broker_{args.broker:02d}'
series=defaultdict(list)
events=defaultdict(list)
with (folder/'balance_and_bounds.csv').open(newline='',encoding='utf-8') as f:
    for r in csv.DictReader(f):
        series[int(r['shard'])].append(r)
with (folder/'tdr_events.csv').open(newline='',encoding='utf-8') as f:
    for r in csv.DictReader(f):
        events[int(r['shard'])].append(r)
BLUE,ORANGE,RED,GREEN,GRAY='#28A8F2','#FFA537','#DE2B03','#249B45','#888888'
plt.rcParams.update({'font.family':'DejaVu Sans','font.weight':'bold',
                    'axes.labelweight':'bold','font.size':17,'pdf.fonttype':42})
handles=[Line2D([0],[0],color=GRAY,ls='--',lw=1.8,label='Upper bound'),
         Line2D([0],[0],color=GRAY,ls=':',lw=1.8,label='Lower bound'),
         Line2D([0],[0],color=BLUE,lw=3,label='Current balance'),
         Line2D([0],[0],color=ORANGE,lw=3.5,label='Target balance'),
         Line2D([0],[0],color=RED,marker='v',ls='None',label='Confirmed TDR out'),
         Line2D([0],[0],color=GREEN,marker='^',ls='None',label='Confirmed TDR in')]
paths=[]
html=(folder/'index.html').read_text(encoding='utf-8')
for s in range(16):
    rows=series[s]
    x=np.array([int(r['block']) for r in rows])
    beta,tau,upper,lower=(np.array([float(r[k]) for r in rows]) for k in (
        'balance_eth','target_eth','upper_eth','lower_eth'))
    fig,ax=plt.subplots(figsize=(10,8))
    ax.fill_between(x,lower,upper,step='post',color=ORANGE,alpha=.09,zorder=1)
    ax.step(x,upper,where='post',color=GRAY,ls='--',lw=1.8,zorder=2)
    ax.step(x,lower,where='post',color=GRAY,ls=':',lw=1.8,zorder=2)
    ax.step(x,beta,where='post',color=BLUE,lw=3,zorder=4)
    ax.step(x,tau,where='post',color=ORANGE,lw=3.45,zorder=3)
    for kind,marker,color in (('out','v',RED),('in','^',GREEN)):
        selected=[r for r in events[s] if r['direction']==kind]
        blocks=[int(r['block']) for r in selected]
        indices=np.searchsorted(x,blocks,side='right')-1
        ax.scatter(blocks,beta[indices],s=95,color=color,marker=marker,
                   edgecolor='white',lw=.6,zorder=7)
    ax.set_xlim(0,400)
    ax.set_xticks(np.linspace(0,400,6,dtype=int))
    ax.grid(axis='y',linestyle=':',alpha=.45)
    ax.set_xlabel('Block heights',fontsize=30,labelpad=12)
    ax.set_ylabel(f"Broker {args.broker}'s balance in shard {s} (ETH)",fontsize=24,labelpad=12)
    ax.tick_params(labelsize=23)
    fig.legend(handles=handles,loc='lower center',ncol=2,fontsize=12,
               framealpha=1,edgecolor='#999999')
    fig.subplots_adjust(left=.15,right=.98,bottom=.31,top=.96)
    stem=f'broker_{args.broker:02d}_shard_{s:02d}_bounds'
    output=folder/f'{stem}.pdf'
    fig.savefig(output,bbox_inches='tight',facecolor='white')
    plt.close(fig)
    paths.append(output)
    if f'href="{stem}.pdf"' not in html:
        html=html.replace(f'<img loading="lazy" src="{stem}.png">',
                          f'<a href="{stem}.pdf">单图矢量 PDF</a><img loading="lazy" src="{stem}.png">')
archive=folder/f'broker_{args.broker:02d}_individual16_pdfs.zip'
with ZipFile(archive,'w',ZIP_DEFLATED) as z:
    for path in paths:
        z.write(path,path.name)
(folder/'index.html').write_text(html,encoding='utf-8')
print(f'Exported {len(paths)} individual vector PDFs and {archive.name}')
