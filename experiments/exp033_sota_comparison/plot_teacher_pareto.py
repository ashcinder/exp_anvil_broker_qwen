"""Verified exp033 2-D tradeoff plots. No synthetic coordinates or algorithm edits."""
import argparse, csv, importlib.util, json, math, statistics, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODE = ROOT if (ROOT / "brokerlab/sota_native.py").exists() else ROOT / "output/exp033_main_audit"
NAMES = {"plain":"No rebalancing", "topup":"TDR", "topup_e0":r"TDR ($\epsilon=0$)",
         "flexichain":"Flexichain", "ucrb":"UCRB", "pnps":"PnP-static",
         "spider":"Spider-rebalance", "loopmax":"Loopmax", "autoloop":"Autoloop",
         "overpass_u":"Overpass-uniform", "overpass_d":"Overpass-demand"}
METRICS = {"transfers":"Rebalancing CTXs", "moved_eth":"Rebalanced volume (ETH)",
           "share":"Rebalancing CTX share (%)"}

def nondominated(points):
    return [a for a, (x,y) in points.items() if not any(
        xx <= x and yy <= y and (xx < x or yy < y)
        for b,(xx,yy) in points.items() if b != a)]

def labels_without_overlap(fig, ax, points):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    used = []
    bounds = ax.get_window_extent(renderer).padded(-3)
    marker_boxes = []
    from matplotlib.transforms import Bbox
    for x,y in points.values():
        px,py=ax.transData.transform((x,y))
        marker_boxes.append(Bbox.from_bounds(px-5,py-5,10,10))
    for arm,(x,y) in sorted(points.items(), key=lambda z: z[0] != "topup"):
        candidates=[]
        for step in (0,12,26,42,60):
            candidates += [(8,8+step,"left"), (8,-15-step,"left"),
                           (-8,8+step,"right"), (-8,-15-step,"right")]
        best=None
        for dx,dy,ha in candidates:
            label=ax.annotate(NAMES.get(arm,arm),(x,y),xytext=(dx,dy),textcoords="offset points",
                ha=ha,fontsize=10,weight="bold" if arm=="topup" else "normal",
                color="#c53616" if arm=="topup" else "#202020",zorder=5)
            box=label.get_window_extent(renderer).padded(2)
            outside = max(0,bounds.x0-box.x0)+max(0,box.x1-bounds.x1)+max(0,bounds.y0-box.y0)+max(0,box.y1-bounds.y1)
            score=outside*10000+sum(box.overlaps(b) for b in used)*10000+sum(box.overlaps(b) for b in marker_boxes)*1000+abs(dy)
            if best is None or score<best[0]:best=(score,dx,dy,ha,box)
            label.remove()
        _,dx,dy,ha,box=best
        ax.annotate(NAMES.get(arm,arm),(x,y),xytext=(dx,dy),textcoords="offset points",ha=ha,
            fontsize=10,weight="bold" if arm=="topup" else "normal",
            color="#c53616" if arm=="topup" else "#202020",zorder=5,
            arrowprops=dict(arrowstyle="-",color="0.55",lw=.65,shrinkA=3,shrinkB=6) if abs(dy)>20 else None)
        used.append(box)
    return int(sum(a.overlaps(b) for i,a in enumerate(used) for b in used[i+1:]))

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--experiment-source",nargs="+",required=True,type=Path)
    ap.add_argument("--output",required=True,type=Path)
    ap.add_argument("--code-root",type=Path,default=DEFAULT_CODE)
    ap.add_argument("--require-formal",action="store_true")
    ap.add_argument("--heatmap-end-s",type=float,default=684.0,help=argparse.SUPPRESS)
    ap.add_argument("--scale",choices=["linear","symlog","auto"],default="auto")
    args=ap.parse_args()
    script=args.code_root/"experiments/exp033_sota_comparison/plot_pareto.py"
    sys.path.insert(0,str(args.code_root))
    spec=importlib.util.spec_from_file_location("verified_exp033",script)
    loader=importlib.util.module_from_spec(spec);spec.loader.exec_module(loader)
    rows=loader.load([str(p.resolve()) for p in args.experiment_source])
    # Also verify moved amount, which the legacy plot loader did not compare.
    for r in rows:
        root=Path(r["source"])
        manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
        task=next(t for t in manifest["tasks"] if t["round"]==r["round"] and t["token"]==r["token"])
        doc=json.loads((root/task["summary"]).read_text(encoding="utf-8"))
        moved=float(doc["arms"][0]["tdr"]["moved_wei"])/1e18
        if not math.isclose(moved,r["moved_eth"],rel_tol=1e-9,abs_tol=1e-8):
            raise ValueError("CSV/task moved amount mismatch")
        r["share"]=100*r["transfers"]/(r["n"]+r["transfers"])
    agg=loader.aggregate(rows)
    formal=set(loader.ORDER)<=set(agg) and all(s["formal"] for s in agg.values())
    if args.require_formal and not formal:
        raise ValueError("Requires all 11 repaired arms, 3 paired rounds, 40,000 CTX/arm and complete formal reports")
    grouped={a:[r for r in rows if r["arm"]==a] for a in agg}
    args.output.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":10,
        "axes.labelsize":11,"axes.labelweight":"bold","xtick.labelsize":10,"ytick.labelsize":10,
        "pdf.fonttype":42,"axes.linewidth":.8})
    table=[];qa={}
    for metric,ylabel in METRICS.items():
        points={a:(statistics.median(r["relay"] for r in g),statistics.median(r[metric] for r in g)) for a,g in grouped.items()}
        front=nondominated(points)
        fig,ax=plt.subplots(figsize=(177.942341/25.4,4.4))
        fig.subplots_adjust(left=.145,right=.98,top=.95,bottom=.27)
        maxx=max(x for x,y in points.values());maxy=max(y for x,y in points.values())
        auto_x=maxx/max(1,min(x for x,y in points.values() if x>0))>100 if maxx else False
        auto_y=maxy/max(.001,min(y for x,y in points.values() if y>0))>100 if maxy else False
        logx=args.scale=="symlog" or args.scale=="auto" and auto_x
        logy=args.scale=="symlog" or args.scale=="auto" and auto_y
        if logx:ax.set_xscale("symlog",linthresh=100,linscale=1)
        if logy:ax.set_yscale("symlog",linthresh=100 if metric=="transfers" else 1000 if metric=="moved_eth" else .1)
        ax.set_xlim(-max(1,maxx*.02) if not logx else -8,max(1,maxx)*1.45)
        ax.set_ylim(-max(.1,maxy*.04) if not logy else -(.02 if metric=="share" else 8),max(.1,maxy)*1.4)
        # A thin line joins only nondominated measured medians, not a fitted curve.
        sorted_front=sorted((points[a] for a in front))
        if len(sorted_front)>1:ax.plot(*zip(*sorted_front),ls="--",lw=.9,color="0.55",zorder=1)
        for arm,g in grouped.items():
            x,y=points[arm]
            color="#c53616" if arm=="topup" else "#777777" if arm=="plain" else "#22a7e8"
            marker="*" if arm=="topup" else "s" if arm=="plain" else "D" if arm=="topup_e0" else "o"
            if len(g)>1:
                ax.errorbar(x,y,xerr=[[x-min(r["relay"] for r in g)],[max(r["relay"] for r in g)-x]],
                    yerr=[[y-min(r[metric] for r in g)],[max(r[metric] for r in g)-y]],
                    fmt="none",ecolor=color,elinewidth=.8,capsize=2,zorder=2)
            ax.scatter(x,y,s=150 if arm=="topup" else 55,marker=marker,color=color,edgecolor="black",linewidth=.6,zorder=4)
            table.append(dict(metric=metric,arm=arm,relay_median=x,y_median=y,pareto_nondominated=arm in front,
                rounds=len(g),formal=formal,relay_min=min(r["relay"] for r in g),relay_max=max(r["relay"] for r in g),
                y_min=min(r[metric] for r in g),y_max=max(r[metric] for r in g)))
        ax.set_xlabel("Relay CTXs (lower is better)")
        ax.set_ylabel(ylabel)
        formatter=FuncFormatter(lambda v,p: f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}".rstrip("0").rstrip("."))
        ax.xaxis.set_major_formatter(formatter);ax.yaxis.set_major_formatter(formatter)
        ax.grid(color="0.9",lw=.6,zorder=0)
        overlaps=labels_without_overlap(fig,ax,points)
        status="FORMAL: 11 arms, 3 paired rounds" if formal else f"SMOKE / ANALYSIS ONLY: {len(agg)} arms; not a paper result"
        fig.text(.145,.105,status,fontsize=9,weight="bold",color="black" if formal else "#a22a15")
        note="Lower-left is better. Dashed line: Pareto front; bars: min-max."
        if logx or logy:note+=" Zero-preserving symlog axes."
        fig.text(.145,.045,note,fontsize=8)
        stem=args.output/f"sota_relay_vs_{metric}"
        fig.savefig(str(stem)+".pdf");fig.savefig(str(stem)+".png",dpi=300)
        plt.close(fig)
        with fitz.open(str(stem)+".pdf") as pdf:
            spans=[s for b in pdf[0].get_text("dict")["blocks"] for l in b.get("lines",[]) for s in l["spans"]]
            clipped=[s["text"] for s in spans if not pdf[0].rect.contains(fitz.Rect(s["bbox"]))]
        qa[metric]=dict(label_overlaps=overlaps,clipped_text=clipped,front=front,x_scale="symlog" if logx else "linear",y_scale="symlog" if logy else "linear")
        if overlaps or clipped:raise ValueError(f"Plot QA failed: {metric}: {qa[metric]}")
    with (args.output/"plotted_values.csv").open("w",encoding="utf-8-sig",newline="") as out:
        writer=csv.DictWriter(out,fieldnames=list(table[0]));writer.writeheader();writer.writerows(table)
    (args.output/"plot_manifest.json").write_text(json.dumps(dict(formal=formal,revision="exp033-faithful-v2",
        sources=[str(p.resolve()) for p in args.experiment_source],metric_share_definition="100*A/(N+A)",
        missing_arms=sorted(set(loader.ORDER)-set(agg)),qa=qa),indent=2),encoding="utf-8")
    print(json.dumps(dict(formal=formal,arms=len(agg),output=str(args.output.resolve()),qa=qa),indent=2))
if __name__=="__main__":main()
