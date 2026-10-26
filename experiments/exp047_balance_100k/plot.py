"""Plot Broker 19 from verified, paired exp047 task outputs."""
from pathlib import Path
import csv,hashlib,json,math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch,Rectangle
from matplotlib.ticker import MaxNLocator

METHODS=[('topup','#ef6548','Broker with TDR'),('plain','#219e73','Broker without rebalancing'),
         ('autoloop','#e8a11b','Broker with Autoloop [3]'),('spider','#9467bd','Broker with Spider [41]')]

def observations(run,partial=False):
    manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8-sig'))
    if manifest['smoke']:raise ValueError('Use formal outputs for the paper figure')
    if not partial and any(t['status']!='complete' for t in manifest['tasks']):
        raise ValueError('Run is incomplete; use --partial for matched completed rounds')
    complete=[t for t in manifest['tasks'] if t['status']=='complete']
    paired={}
    for t in complete:paired.setdefault((t['balance_eth'],t['round']),set()).add(t['policy'])
    cells={cell for cell,methods in paired.items() if methods=={m[0] for m in METHODS}}
    result=[]
    for task in complete:
        if (task['balance_eth'],task['round']) not in cells:continue
        folder=run/task['attempts'][-1]['path']
        summary=json.loads((folder/'summary.json').read_text(encoding='utf-8-sig'))
        assert hashlib.sha256((folder/'per_broker.csv').read_bytes()).hexdigest()==summary['derived']['per_broker.csv']
        with (folder/'per_broker.csv').open(encoding='utf-8-sig',newline='') as f:
            broker=next(r for r in csv.DictReader(f) if int(r['broker_idx'])==19)
        result.append(dict(balance=int(task['balance_eth']),method=task['policy'],round=task['round'],
            assigned=int(broker['assigned']),served=int(broker['served']),relay=int(broker['relay']),
            transfers=int(broker['tdr_transfers']),profit=float(broker['net_profit_wei'])/1e9))
    if not result:raise ValueError('No complete four-method paired round is available')
    return result

def choose_break(data):
    # Compress a data-free negative interval without cutting any box or whisker.
    intervals=[]
    for b in {r['balance'] for r in data}:
        for method,_,_ in METHODS:
            values=[r['profit'] for r in data if r['balance']==b and r['method']==method]
            intervals.append((min(values),max(values)))
    merged=[]
    for lo,hi in sorted(intervals):
        if merged and lo<=merged[-1][1]:merged[-1]=(merged[-1][0],max(hi,merged[-1][1]))
        else:merged.append((lo,hi))
    gaps=[(a[1],b[0]) for a,b in zip(merged,merged[1:]) if b[0]<-4.5 and b[0]-a[1]>1.0]
    if not gaps:return None
    lo,hi=max(gaps,key=lambda x:x[1])
    return lo+(hi-lo)*.12,hi-(hi-lo)*.12

def plot(run,partial=False):
    run=Path(run).resolve();data=observations(run,partial);out=run/'figures';out.mkdir(exist_ok=True)
    xs=sorted({r['balance'] for r in data});W,H=125.6,134.7;left,width=25.5/W,93.7/W
    plt.rcParams.update({'font.family':'DejaVu Sans','font.weight':'bold','axes.labelweight':'bold','pdf.fonttype':42,'axes.linewidth':.35})
    fig=plt.figure(figsize=(W/72,H/72));cut=choose_break(data)
    ymin=min(r['profit'] for r in data);ymax=max(r['profit'] for r in data)
    main_min=cut[1] if cut else ymin
    yhigh=math.ceil((ymax+max(.3,.06*(ymax-main_min)))*2)/2
    ylow=math.floor((ymin-max(.3,.04*(ymax-ymin)))*2)/2
    def axis(y,h,lo,hi,sharex=None):
        ax=fig.add_axes([left,y/H,width,h/H],sharex=sharex)
        ax.set_xlim(-.6,len(xs)-.4);ax.set_ylim(lo,hi);ax.set_axisbelow(True)
        ax.grid(axis='y',linestyle=':',linewidth=.3,alpha=.35)
        ax.tick_params(axis='y',labelsize=5.9,pad=.4,length=1.5,width=.35)
        ax.tick_params(axis='x',labelsize=5.65,pad=1,length=1.5,width=.35)
        for spine in ax.spines.values():spine.set_visible(False)
        return ax
    if cut:
        low_top,main_bottom=cut
        bottom=axis(24.2,9.5,ylow,low_top)
        main=axis(34.7,56.0,main_bottom,yhigh,sharex=bottom)
        bottom.set_yticks([round((ylow+low_top)/10)*5])
        main.tick_params(axis='x',which='both',bottom=False,labelbottom=False)
        axes=[bottom,main]
        for x in (left,(25.5+93.7)/W):
            fig.lines.append(plt.Line2D([x-.006,x+.006],[(34.2-.55)/H,(34.2+.55)/H],
                transform=fig.transFigure,color='black',linewidth=.45,clip_on=False))
    else:
        bottom=main=axis(24.2,66.5,ylow,yhigh);axes=[main]
    main.yaxis.set_major_locator(MaxNLocator(nbins=4));main.axhline(0,color='gray',lw=.35,zorder=0)
    for ax in axes:
        for j,(method,color,_) in enumerate(METHODS):
            values=[[r['profit'] for r in data if r['method']==method and r['balance']==b] for b in xs]
            ax.boxplot(values,positions=[i+(j-1.5)*.19 for i in range(len(xs))],widths=.16,
                manage_ticks=False,patch_artist=True,showfliers=False,whis=(0,100),
                boxprops=dict(facecolor=color,edgecolor=color,alpha=.62,linewidth=.4),
                medianprops=dict(color='black',linewidth=.38),
                whiskerprops=dict(color=color,linewidth=.38),capprops=dict(color=color,linewidth=.38))
    bottom.set_xticks(range(len(xs)),[str(x) for x in xs],rotation=30,ha='right')
    fig.text(.5,3.2/H,'Initial balance (ETH)',ha='center',va='bottom',fontsize=7.4,fontweight='bold')
    fig.text(27.37/W,(H-8.5)/H,'(Gwei)',fontsize=5.7,ha='left',va='top')
    fig.text(5.3/W,71.2/H,'Profit of the 19th Broker',rotation=90,va='center',ha='center',fontsize=7.2,fontweight='bold')
    legend_ax=fig.add_axes([left,90.7/H,width,27.5/H]);legend_ax.set_axis_off()
    handles=[Patch(facecolor=c,edgecolor=c,alpha=.62,label=label) for _,c,label in METHODS]
    legend_ax.legend(handles=handles,loc='center left',bbox_to_anchor=(.01,.5),borderaxespad=0,
        fontsize=5.3,frameon=True,framealpha=1,facecolor='white',edgecolor='#999999',
        borderpad=.12,handlelength=.4,handletextpad=.2,labelspacing=.22)
    fig.add_artist(Rectangle((left,24.2/H),width,94/H,transform=fig.transFigure,fill=False,
        edgecolor='black',linewidth=.35,clip_on=False))
    fig.savefig(out/'balance_100k.png',dpi=600);fig.savefig(out/'balance_100k.pdf');plt.close(fig)
    with (out/'broker19_observations.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)
    counts={b:sum(r['balance']==b and r['method']=='topup' for r in data) for b in xs}
    (out/'caption.txt').write_text('Profit of Broker 19 summed over all 16 subaccounts, versus initial balance per subaccount at 100,000 user CTXs per run. '
        f'Paired whole-run observations per method and balance: {counts}. Boxes show Q1–Q3, black lines show medians, and whiskers show the observed minimum and maximum. '
        'Net profit deducts the modeled fee for confirmed internal transfers. No scatter markers or potential-profit series are added. '
        +(f'One y-axis break compresses the empty interval {cut}; all observations are retained. ' if cut else 'Linear y axis. ')
        +'Autoloop [3]; Spider [41].',encoding='utf-8')
    print('FIGURE_PDF='+str(out/'balance_100k.pdf'))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run',type=Path)
    parser.add_argument('--partial',action='store_true',help='Only plot completed rounds paired across all four methods')
    args=parser.parse_args();plot(args.run,args.partial)
