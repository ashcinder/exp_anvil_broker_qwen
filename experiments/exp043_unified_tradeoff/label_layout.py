"""Verified exp033 2-D tradeoff plots. No synthetic coordinates or algorithm edits."""
import argparse, csv, importlib.util, json, math, statistics, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODE = Path(r'D:/BaiduNetdiskDownload/exp_anvil_broker_qwen/exp_anvil_broker_qwen')
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
    used = [t.get_window_extent(renderer).padded(3) for t in ax.texts]
    if ax.get_legend() is not None: used.append(ax.get_legend().get_window_extent(renderer).padded(3))
    leaders = []
    bounds = ax.get_window_extent(renderer).padded(-3)
    marker_boxes = []
    from matplotlib.transforms import Bbox
    for x,y in points.values():
        px,py=ax.transData.transform((x,y))
        marker_boxes.append(Bbox.from_bounds(px-5,py-5,10,10))
    for arm,(x,y) in sorted(points.items(), key=lambda z: (z[0] != "topup", -z[1][1])):
        candidates=[]
        for step in (0,12,26,42,60,80,105,135,165):
            for horizontal in (8,30,60,100,150):
                candidates += [(horizontal,8+step,"left"), (horizontal,-15-step,"left"),
                               (-horizontal,8+step,"right"), (-horizontal,-15-step,"right")]
        best=None
        for dx,dy,ha in candidates:
            label=ax.annotate(NAMES.get(arm,arm),(x,y),xytext=(dx,dy),textcoords="offset points",
                ha=ha,fontsize=10,weight="bold" if "EWMA" in NAMES.get(arm,arm) else "normal",
                color="#c53616" if arm=="topup" else "#7b3eb1" if arm in ("prop_hard","prop_ewma","topup_hard","topup_e0") else "#202020",zorder=5)
            box=label.get_window_extent(renderer).padded(2)
            outside = max(0,bounds.x0-box.x0)+max(0,box.x1-bounds.x1)+max(0,bounds.y0-box.y0)+max(0,box.y1-bounds.y1)
            px,py=ax.transData.transform((x,y))
            tx=min(max(px,box.x0),box.x1);ty=min(max(py,box.y0),box.y1)
            segment=[(px+(tx-px)*j/50,py+(ty-py)*j/50) for j in range(5,46)]
            crossing=sum(any(b.contains(xx,yy) for xx,yy in segment) for b in used)
            crossing+=sum(any(box.contains(xx,yy) for xx,yy in line) for line in leaders)
            score=outside*10000+sum(box.overlaps(b) for b in used)*10000+sum(box.overlaps(b) for b in marker_boxes)*1000+crossing*3000+abs(dy)+abs(dx)*.4
            if best is None or score<best[0]:best=(score,dx,dy,ha,box)
            label.remove()
        _,dx,dy,ha,box=best
        ax.annotate(NAMES.get(arm,arm),(x,y),xytext=(dx,dy),textcoords="offset points",ha=ha,
            fontsize=10,weight="bold" if "EWMA" in NAMES.get(arm,arm) else "normal",
            color="#c53616" if arm=="topup" else "#7b3eb1" if arm in ("prop_hard","prop_ewma","topup_hard","topup_e0") else "#202020",zorder=5,
            bbox=dict(facecolor="white",edgecolor="none",pad=.15,alpha=.95),
            arrowprops=dict(arrowstyle="-",color="0.55",alpha=.6,lw=.55,shrinkA=3,shrinkB=6) if abs(dy)>20 or abs(dx)>15 else None)
        used.append(box)
        if abs(dy)>20 or abs(dx)>15:
            px,py=ax.transData.transform((x,y));tx=min(max(px,box.x0),box.x1);ty=min(max(py,box.y0),box.y1)
            leaders.append([(px+(tx-px)*j/50,py+(ty-py)*j/50) for j in range(5,46)])
    return int(sum(a.overlaps(b) for i,a in enumerate(used) for b in used[i+1:]))
