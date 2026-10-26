"""Plot only completed, verified runs from one exp046 manifest."""
from pathlib import Path
import argparse
import json
import math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator,FuncFormatter
import pymupdf
from design import PROTOCOL,METHODS,read_json,read_csv,write_csv,write_json,verify_hashes,sha

def mapped(y,source,target):
    """Strictly monotone mapping, including linear extrapolation beyond limits."""
    y=np.asarray(y,dtype=float);result=np.interp(y,source,target)
    result=np.where(y<source[0],target[0]+(y-source[0])*(target[1]-target[0])/(source[1]-source[0]),result)
    return np.where(y>source[-1],target[-1]+(y-source[-1])*(target[-1]-target[-2])/(source[-1]-source[-2]),result)

def scale_for(distributions):
    ranges=sorted((min(v),max(v)) for v in distributions)
    lower=min(0,min(x[0] for x in ranges));upper=max(0,max(x[1] for x in ranges))
    pad=max((upper-lower)*.04,.03);lo=lower-pad;hi=upper+pad
    gaps=[];end=ranges[0][1]
    for a,b in ranges[1:]:
        if a>end:gaps.append((a-end,end,a))
        end=max(end,b)
    if gaps:
        size,a,b=max(gaps)
        if size>.35*(hi-lo) and size>1:
            a+=size*.05;b-=size*.05
            assert all(not (a<y<b) for v in distributions for y in v)
            return np.array([lo,a,b,hi]),np.array([0,.48,.54,1]),(a,b)
    return np.array([lo,hi]),np.array([0,1]),None

def draw(run,m,rows,brokers,compact=False):
    arms=list(METHODS);x=np.arange(5);groups=[[float(b['profit_gwei']) for b in brokers if b['method']==a] for a in arms]
    knots,screen,gap=scale_for(groups)
    forward=lambda y:mapped(y,knots,screen)
    inverse=lambda y:mapped(y,screen,knots)
    w,h=(125.6,134.7) if compact else (251.2,205)
    label=6.7 if compact else 9;tick=5.36 if compact else 7.5;other=5.1 if compact else 7.5
    plt.rcParams.update({'font.family':'DejaVu Sans','font.weight':'bold','font.size':other,
        'axes.labelweight':'bold','axes.labelsize':label,'xtick.labelsize':tick,'ytick.labelsize':tick,'pdf.fonttype':42,'axes.linewidth':.65})
    fig=plt.figure(figsize=(w/72,h/72))
    ax=fig.add_axes([.23 if compact else .13,.34,.525 if compact else .715,.46 if compact else .49]);right=ax.twinx()
    ax.set_xlim(-.55,4.55);right.set_yscale('function',functions=(forward,inverse));right.set_ylim(knots[0],knots[-1])
    amounts=[np.array([float(r['user_ctx_communication_MB']) for r in rows if r['method']==a]) for a in arms]
    means=np.array([v.mean() for v in amounts]);errors=np.array([[v.mean()-v.min() for v in amounts],[v.max()-v.mean() for v in amounts]])
    axis_max=max(1e-6,max(v.max() for v in amounts)*1.08)
    ax.set_ylim(0,axis_max)
    ax.axvspan(3.53,4.47,color='#fff1e7',lw=0,zorder=-2)
    bars=ax.bar(x,means,width=.73,color='#b9d9e6',edgecolor='#79a7ba',lw=.45,zorder=1)
    bars[-1].set_facecolor('#64abc4');bars[-1].set_edgecolor('#287794')
    ax.errorbar(x,means,yerr=errors,fmt='none',ecolor='#29454e',elinewidth=.8,capsize=1.2 if compact else 2,zorder=3)
    bp=right.boxplot(groups,positions=x,widths=.48,manage_ticks=False,patch_artist=True,whis=1.5,showfliers=True,
        boxprops=dict(fc='#d7e1ed',ec='#34475a',lw=.8),medianprops=dict(color='#222222',lw=1),
        whiskerprops=dict(color='#333333',lw=.7),capprops=dict(color='#333333',lw=.7),
        flierprops=dict(marker='.',markersize=1.7 if compact else 2.5,mfc='#333333',mec='none'))
    bp['boxes'][-1].set_facecolor('#ed7559');bp['boxes'][-1].set_edgecolor('#b6371e')
    for p in bp['whiskers'][-2:]+bp['caps'][-2:]:p.set_color('#b6371e')
    right.axhline(0,color='#666666',lw=.7,ls=(0,(3,2)),zorder=0)
    # Place the label in a free span next to zero, preserving all boxes.
    zero=right.text(.56,forward(0)-.035,'Break-even',transform=right.transAxes,ha='center',va='top',
        fontsize=other-.3,color='#555555',bbox=dict(fc='white',ec='none',pad=.25))
    if gap:
        mid=(screen[1]+screen[2])/2
        for dy in [-.012,.012]:
            xx=[.985,1.015];yy=[mid+dy-.009,mid+dy+.009]
            right.plot(xx,yy,transform=right.transAxes,color='white',lw=2.3,clip_on=False,zorder=8)
            right.plot(xx,yy,transform=right.transAxes,color='#222222',lw=.65,clip_on=False,zorder=9)
        ticks=[]
        for a,b in [(knots[0],knots[1]),(knots[2],knots[3])]:
            ticks.extend(t for t in MaxNLocator(nbins=3).tick_values(a,b) if a<=t<=b)
        if not any(abs(t)<1e-12 for t in ticks):ticks.append(0.)
        # Remove near-duplicate labels in compressed display coordinates.
        keep=[]
        for t in sorted(ticks,key=lambda t:(abs(t)>1e-12,abs(t))):
            if all(abs(forward(t)-forward(u))>.095 for u in keep):keep.append(t)
        right.set_yticks(sorted(keep))
    else:
        ticks=[t for t in MaxNLocator(nbins=6).tick_values(knots[0],knots[-1]) if knots[0]<=t<=knots[-1]]
        right.set_yticks(ticks)
    right.yaxis.set_major_formatter(FuncFormatter(lambda y,p:f'{y:g}'))
    ax.set_yticks([t for t in MaxNLocator(nbins=4).tick_values(0,axis_max) if 0<=t<=axis_max])
    if max(v.max() for v in amounts)<.1:
        ax.ticklabel_format(axis='y',style='sci',scilimits=(0,0),useMathText=True)
        ax.yaxis.get_offset_text().set_fontsize(tick)
    ax.set_ylabel('Transfer costs (MB)',labelpad=1.5 if compact else 3)
    ax.yaxis.set_label_coords(-.30 if compact else -.11,.5)
    right.set_ylabel('Profit (Gwei)' if compact else 'Absolute broker profit (Gwei)',labelpad=1.5 if compact else 3)
    labels=['AutoLoop','Spider','P-EWMA',r'T-$\epsilon=0$','TDR'] if compact else ['AutoLoop','Spider-\nrebalance','Prop.\nEWMA','Top-up\n'+r'$\epsilon=0$','TDR\nEWMA']
    ax.set_xticks(x,labels)
    for t in ax.get_xticklabels():t.set_fontsize(other);t.set_rotation(90);t.set_linespacing(1.15)
    ax.get_xticklabels()[-1].set_color('#ba351d');ax.set_xlabel('Method',labelpad=3 if compact else 5)
    ax.tick_params(axis='both',length=2,pad=1);right.tick_params(axis='y',length=2,pad=1)
    ax.grid(axis='y',color='#eeeeee',lw=.5);ax.set_axisbelow(True)
    legend=fig.legend(handles=[Patch(fc='#b9d9e6',ec='#79a7ba',label='Transfer costs (left)'),
        Patch(fc='#d7e1ed',ec='#34475a',label='Absolute broker profit (right)')],loc='upper center',bbox_to_anchor=(.5,.985),
        fontsize=other,frameon=False,borderpad=0,labelspacing=.3,handlelength=1.3,handletextpad=.45)
    if m['smoke']:fig.text(.95,.845,'SMOKE CHECK',ha='right',fontsize=other,color='#b6371e')
    fig.canvas.draw();renderer=fig.canvas.get_renderer()
    protected=[ax.xaxis.label,ax.yaxis.label,right.yaxis.label,ax.yaxis.get_offset_text(),*ax.get_xticklabels(),*ax.get_yticklabels(),*right.get_yticklabels(),*legend.get_texts()]
    clipped=[t.get_text() for t in protected if not fig.bbox.contains(*t.get_window_extent(renderer).p0) or not fig.bbox.contains(*t.get_window_extent(renderer).p1)]
    if clipped:raise ValueError('Clipped figure text: '+str(clipped))
    if any(zero.get_window_extent(renderer).overlaps(p.get_window_extent(renderer)) for p in bp['boxes']):
        placed=False
        for va,dy in [('top',-.025),('bottom',.025)]:
            for xx in [.78,.22,.5]:
                zero.set_position((xx,float(forward(0))+dy));zero.set_va(va)
                zb=zero.get_window_extent(renderer)
                if ax.bbox.contains(*zb.p0) and ax.bbox.contains(*zb.p1) and all(not zb.overlaps(p.get_window_extent(renderer)) for p in bp['boxes']):
                    placed=True;break
            if placed:break
    yt=[t.get_window_extent(renderer) for t in right.get_yticklabels()]
    assert all(not a.overlaps(b) for a,b in zip(yt,yt[1:])),'Profit tick labels overlap'
    out=run/'figures';out.mkdir(exist_ok=True)
    name=('smoke_' if m['smoke'] else '')+('fig5e_panel' if compact else 'fig5e')
    fig.savefig(out/(name+'.pdf'));plt.close(fig)
    with pymupdf.open(out/(name+'.pdf')) as doc:doc[0].get_pixmap(matrix=pymupdf.Matrix(600/72,600/72)).save(out/(name+'.png'))
    return dict(file=name+'.pdf',page_pt=[w,h],empty_profit_gap_gwei=gap,smoke=m['smoke'])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path);p.add_argument('--allow-smoke',action='store_true');a=p.parse_args();run=a.run.resolve()
    m=read_json(run/'manifest.json');report=read_json(run/'report.json')
    if m['protocol']!=PROTOCOL or not report['complete'] or any(t['status']!='complete' for t in m['tasks']):
        raise ValueError('All planned runs must pass before this figure is generated')
    if m['smoke'] and not a.allow_smoke:raise ValueError('Use --allow-smoke for a visibly labelled smoke preview')
    verify_hashes(run/'code',m['code_hashes']);verify_hashes(run/'backend',m['backend_hashes'])
    rows=read_csv(run/'results.csv');brokers=read_csv(run/'per_broker.csv');nb=m['plan']['brokers'];reps=m['plan']['runs_per_method']
    stats=[]
    for arm,spec in METHODS.items():
        rr=[r for r in rows if r['method']==arm];bb=[r for r in brokers if r['method']==arm]
        assert len(rr)==reps and len(bb)==reps*nb
        for r in rr:
            if int(r['transactions'])!=m['plan']['user_CTX_per_run']:raise ValueError('Different workload length')
            if int(r['income_wei'])-int(r['modeled_cost_wei'])!=int(r['net_profit_wei']):raise ValueError('Profit mismatch')
        vals=np.array([int(b['net_profit_wei'])/1e9 for b in bb]);mb=[float(r['user_ctx_communication_MB']) for r in rr]
        stats.append(dict(method=arm,label=spec['label'],runs=len(rr),broker_records=len(bb),
            communication_MB_mean=np.mean(mb),communication_MB_min=min(mb),communication_MB_max=max(mb),
            system_profit_gwei_mean=np.mean([int(r['net_profit_wei'])/1e9 for r in rr]),
            broker_profit_gwei_mean=vals.mean(),q1_gwei=np.percentile(vals,25),median_gwei=np.median(vals),q3_gwei=np.percentile(vals,75)))
    write_csv(run/'figure_statistics.csv',stats)
    validation=[draw(run,m,rows,brokers),draw(run,m,rows,brokers,True)]
    write_json(run/'figures/validation.json',dict(protocol=PROTOCOL,smoke=m['smoke'],common_workload_sha256=m['workload_sha256'],
        generator_sha256=sha(Path(__file__)),outputs=validation))
    caption=(f"Five methods at 45 ETH per sub-account and {m['plan']['user_CTX_per_run']:,} user CTXs per run, "
        f"with {nb} brokers and {m['plan']['shards']} shards. Each method has {reps} actual repetitions using the same frozen workload and runtime, "
        "with identical per-round route seeds and fresh chains. Bars show mean system-wide user-CTX communication billing in MB, with run-level min-max error bars. "
        "Billing includes declared 100 B broker control overhead or recorded relay transaction and receipt bytes, excluding unrecorded rebalance traffic and other messages. "
        f"Boxes show {reps*nb} broker-run observations per method: quartiles, medians, 1.5-IQR whiskers and outliers. These broker records are clustered within actual runs. "
        "Absolute nominal modeled profit is 0.0021*S - 0.021*A Gwei, with served user CTXs S and completed internal transfers A; modeled transfer cost is not an observed gas charge. "
        "The dashed line marks Break-even. Any empty interval compressed on the profit axis is visibly marked and recorded in validation.json.\n")
    (run/'figures/caption.txt').write_text(caption,encoding='utf-8')
    print('FIGURES='+str(run/'figures'))

if __name__=='__main__':main()
