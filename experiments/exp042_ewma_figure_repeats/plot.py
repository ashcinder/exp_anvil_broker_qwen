"""One run -> Fig1a capped bars, Fig1b OFF band, Fig5a paired profit bands."""
import argparse,json,zipfile
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter,MaxNLocator
from matplotlib.text import Text
from design import read,write,save,sha

BLUE,ORANGE,GRAY='#28A8F2','#FFA537','#555555'
SOFTWARE_CHECK=False
plt.rcParams.update({'font.family':'DejaVu Sans','font.weight':'bold','axes.labelweight':'bold','pdf.fonttype':42,'ps.fonttype':42,'axes.linewidth':.35,'xtick.major.width':.35,'ytick.major.width':.35,'xtick.major.size':1.5,'ytick.major.size':1.5})
def moments(matrix):
    v=np.asarray(matrix,dtype=float);mean=v.mean(axis=0)
    sd=v.std(axis=0,ddof=1) if len(v)>1 else np.zeros_like(mean)
    fixed=np.ptp(v,axis=0)==0
    return np.where(fixed,v[0],mean),np.where(fixed,0,sd)
def shade(ax,x,values,color,label,ls='-'):
    mean,sd=moments(values)
    ax.fill_between(x,mean-sd,mean+sd,color=color,alpha=.22,lw=0)
    ax.plot(x,mean,color=color,lw=.8,linestyle=ls,label=label)
def frame(fig,left=25.5,width=93.7):
    w,h=fig.get_size_inches()*72
    return fig.add_axes([left/w,(h-110.5)/h,width/w,94/h])
def ticks(ax):
    ax.tick_params(axis='x',labelsize=5.140,pad=1);ax.tick_params(axis='y',labelsize=5.364,pad=1.4)
    ax.grid(linestyle=':',linewidth=.25,alpha=.45)
def legend(ax):
    ax.legend(loc='upper left',fontsize=4.9,framealpha=1,edgecolor='#999999',borderpad=.25,handlelength=1.1,handletextpad=.5,labelspacing=.25).get_frame().set_linewidth(.3)
def profit(ax,x,off,on,ideal,limit,with_on):
    keep=x<=limit;xx=x[keep]
    shade(ax,xx,ideal[:,keep],GRAY,'Ideal upper bound',(0,(2,2)))
    if with_on:shade(ax,xx,on[:,keep],BLUE,'EWMA profit')
    shade(ax,xx,off[:,keep],ORANGE if with_on else BLUE,'Without TDR')
    top=max(float(ideal[:,keep].max()),.01)*1.35
    low=0.0
    for values in [off[:,keep]]+([on[:,keep]] if with_on else []):
        mean,sd=moments(values);low=min(low,float((mean-sd).min()))
    ax.set_ylim(low-.02*top,top);ax.set_xlim(0,limit)
    ts=np.linspace(0,limit,6 if limit>=10000 else 5,dtype=int)
    ax.set_xticks(ts,['0' if k==0 else f'{k//1000}k' if k>=1000 else str(k) for k in ts]);ax.get_xticklabels()[-1].set_ha('right')
    ax.yaxis.set_major_locator(MaxNLocator(6))
    fmt=ScalarFormatter(useOffset=False);fmt.set_powerlimits((0,0));ax.yaxis.set_major_formatter(fmt);ax.yaxis.get_offset_text().set_fontsize(4.5)
    ax.set_xlabel('Transaction Index',fontsize=6.705,labelpad=4)
    ax.set_ylabel('Broker profit (Gwei)',fontsize=6.5,labelpad=1)
    ticks(ax);legend(ax)
def export(fig,path):
    if SOFTWARE_CHECK:fig.text(.5,.995,'SMOKE / SOFTWARE CHECK',ha='center',va='top',fontsize=4,color='#888888')
    fig.canvas.draw();renderer=fig.canvas.get_renderer();clipped=[]
    for t in fig.findobj(Text):
        if t.get_visible() and t.get_text():
            r=t.get_window_extent(renderer)
            if not(fig.bbox.contains(r.x0,r.y0) and fig.bbox.contains(r.x1,r.y1)):clipped.append(t.get_text())
    if clipped:raise ValueError('Clipped figure text: '+repr(clipped))
    fig.savefig(path,facecolor='white');fig.savefig(path.with_suffix('.png'),dpi=300,facecolor='white');plt.close(fig)
def main():
    global SOFTWARE_CHECK
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);args=p.parse_args()
    run=args.run.resolve();m=json.loads((run/'manifest.json').read_text());c=m['config'];s=c['study']
    SOFTWARE_CHECK=m['smoke']
    paired=[];curves={'plain':[],'topup':[]};bar=[];points=[];selected=[]
    for r in range(1,s['rounds']+1):
        ts=[t for t in m['tasks'] if t['round']==r]
        if len(ts)!=2 or not all(t['status']=='complete' for t in ts):continue
        paired.append(r)
        for t in ts:
            a=run/t['attempts'][-1]['path'];doc=json.loads((a/'result.json').read_text())
            for rel,h in doc['artifacts'].items():
                if sha(a/rel)!=h:raise ValueError('Artifact changed: '+str(a/rel))
            rows=read(a/'curve.csv');curves[t['arm']].append(rows)
            selected.append(dict(round=r,seed=t['route_seed'],arm=t['arm'],assigned=doc['assigned'],served=doc['served'],tdr_moves=doc['broker_tdr_moves'],income_gwei=doc['income_wei']/1e9,cost_gwei=doc['cost_wei']/1e9,profit_gwei=doc['profit_wei']/1e9))
            if t['arm']=='plain':bar.append(read(a/'bins.csv'))
    if not paired:raise ValueError('No complete paired round; finish a pair first')
    n=len(paired);count=len(curves['plain'][0]);x=np.arange(count)
    for arm in curves:
        assert all([int(r['transaction']) for r in row]==list(x) for row in curves[arm])
    arr=lambda arm,key:np.array([[int(r[key]) for r in row] for row in curves[arm]],dtype=np.int64)
    off=arr('plain','profit_wei')/1e9;on=arr('topup','profit_wei')/1e9
    ideal=np.maximum(arr('plain','upper_wei'),arr('topup','upper_wei'))/1e9
    out=run/'figs';out.mkdir(exist_ok=True)
    binrows=[]
    for j in range(len(bar[0])):
        d=dict(start=int(bar[0][j]['start']),end=int(bar[0][j]['end']),n=n)
        for key in ['broker','relay','total']:
            v=[int(b[j][key]) for b in bar];d[key+'_mean']=float(np.mean(v));d[key+'_sd']=float(np.std(v,ddof=1)) if n>1 else 0
        binrows.append(d)
    def bars(ax):
        relay=np.array([r['relay_mean'] for r in binrows]);broker=np.array([r['broker_mean'] for r in binrows]);pos=np.arange(len(binrows))
        ax.bar(pos,relay,width=.92,color=ORANGE,edgecolor='black',linewidth=.35,label='# of CTXs served by\nrelay mechanism [1]',zorder=2)
        ax.bar(pos,broker,bottom=relay,width=.92,color=BLUE,edgecolor='black',linewidth=.35,label='# of CTXs served by\nbroker mechanism [2]',zorder=2)
        if n>1:
            for y,key in [(relay,'relay'),(relay+broker,'total')]:ax.errorbar(pos,y,yerr=[r[key+'_sd'] for r in binrows],fmt='none',ecolor='black',elinewidth=.45,capsize=1.5,capthick=.45,zorder=5)
        highest=max(r['total_mean']+r['total_sd'] for r in binrows)
        ax.set_xlim(-.55,len(pos)-.45);ax.set_ylim(0,max(1,float(highest))*1.8)
        ax.set_xticks(pos,[str(r['end']) for r in binrows]);ax.yaxis.set_major_locator(MaxNLocator(5))
        fmt=ScalarFormatter(useOffset=False)
        if max(relay+broker)>=1000:fmt.set_powerlimits((3,3))
        else:fmt.set_scientific(False)
        ax.yaxis.set_major_formatter(fmt);ax.yaxis.get_offset_text().set_fontsize(5)
        ax.set_xlabel('Block heights',fontsize=6.705,labelpad=4);ax.set_ylabel('# of CTXs',fontsize=6.5,labelpad=1)
        ticks(ax);legend(ax)
    limit=min(s['display_transactions'],count-1)
    f=plt.figure(figsize=(251.2/72,134.7/72),dpi=300);a=frame(f);b=frame(f,left=151.1);bars(a);profit(b,x,off,on,ideal,limit,False);export(f,out/'fig1.pdf')
    f=plt.figure(figsize=(125.6/72,134.7/72),dpi=300);bars(frame(f));export(f,out/'fig1a.pdf')
    f=plt.figure(figsize=(125.6/72,134.7/72),dpi=300);profit(frame(f),x,off,on,ideal,limit,False);export(f,out/'fig1b.pdf')
    f=plt.figure(figsize=(125.6/72,134.7/72),dpi=300);profit(frame(f),x,off,on,ideal,limit,True);export(f,out/'fig5a.pdf')
    f=plt.figure(figsize=(125.6/72,134.7/72),dpi=300);profit(frame(f),x,off,on,ideal,count-1,True);export(f,out/'fig5a_full.pdf')
    write(out/'bar_statistics.csv',binrows);write(out/'round_results.csv',selected)
    cached={key:moments(v) for key,v in [('off_profit',off),('ewma_profit',on),('ideal',ideal)]}
    for i in range(0,len(x),max(1,(len(x)-1)//500)):
        d=dict(transaction=int(x[i]),n=n)
        for key,(mean,sd) in cached.items():
            d[key+'_mean_gwei']=float(mean[i]);d[key+'_sd_gwei']=float(sd[i])
        points.append(d)
    write(out/'curve_statistics.csv',points)
    caption=f'n={n} complete paired routing-seed repetitions on one fixed workload. Mean +/- sample SD (ddof=1), not a confidence interval. Profit = retained user fee income minus the configured relay fee for each confirmed TDR burn; burn and mint are not double charged. Transaction Index is the globally recorded completed-USER-transaction count, not original arrival position. TDR charges at index N include tail burns after the final user completion. Both original arrival_pos and observed accounting_index are saved. The gray ideal is the per-pair maximum of assigned-user-count fee ceilings for EWMA and Plain, assuming zero rebalancing cost; it is an empirical ceiling, not a proven achievable optimum. Fig1a displays only the first {s["fig1_end_block"]} submission-block bins; Fig1b/Fig5a main display the first {limit} completed user transactions of {count-1}. Full curves are retained. Costs are modeled; testbed gasPrice=0. Only paired completed rounds are included.'
    if n<2:caption=caption.replace('Mean +/- sample SD (ddof=1), not a confidence interval.','Single-run trajectory only: sample SD is unavailable; no estimated uncertainty band.')
    (out/'caption.txt').write_text(caption,encoding='utf-8')
    save(out/'plot_metadata.json',dict(n=n,paired_rounds=paired,smoke=m['smoke'],broker_idx=s['broker_idx'],axis='global completed-user-transaction accounting index',cost_position_is_projected=False,shading='mean +/- sample SD; no band for one sample',display_transactions=limit,final_mean_ewma_profit_gwei=float(on[:,-1].mean()),final_mean_off_profit_gwei=float(off[:,-1].mean()),caption=caption))
    with zipfile.ZipFile(out/'overleaf_fig1_fig5a.zip','w',zipfile.ZIP_DEFLATED) as z:
        z.write(out/'fig1.pdf','figs/fig1.pdf');z.write(out/'fig5a.pdf','figs/fig5a.pdf');z.write(out/'fig5a_full.pdf','figs/fig5a_full.pdf');z.write(out/'caption.txt','caption.txt')
    print('FIGURES_DIRECTORY='+str(out),flush=True)
if __name__=='__main__':main()
