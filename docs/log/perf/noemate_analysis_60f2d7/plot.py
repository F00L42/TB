"""Plot audited observed reasoning; no model inference or raw capture mutation."""
from pathlib import Path
import csv
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

out=Path(__file__).resolve().parent
rows=[json.loads(line) for line in (out/'request_features.jsonl').read_text(encoding='utf-8').splitlines()]
valid=[r for r in rows if not r['exclusions']]
x=np.array(sorted(r['reasoning_tokens_local'] for r in valid))
scans=list(csv.DictReader((out/'threshold_scan.csv').open(encoding='utf-8')))
fig,axes=plt.subplots(1,3,figsize=(15,4.6),layout='constrained')
color='#176b87'
axes[0].hist(x,bins=np.arange(0,2001,50),color=color,edgecolor='white')
axes[0].set(xlabel='Visible reasoning tokens',ylabel='Requests',title='Observed complete main-agent calls (N=8,277)')
for h,c in [(512,'#ac5c20'),(750,'#784e96'),(850,'#c43137')]:
    axes[0].axvline(h,color=c,linestyle='--',linewidth=1,label=str(h))
axes[0].legend(title='Candidate H',frameon=False)
v=np.unique(x); tail=np.array([(x>h).mean()*100 for h in v])
axes[1].plot(v[tail>0],tail[tail>0],color=color,linewidth=2)
axes[1].set(yscale='log',xlabel='Threshold H (tokens)',ylabel='Calls with reasoning > H (%)',title='Tail probability (observed subset)')
axes[1].grid(alpha=.2,which='both')
h=[int(r['threshold']) for r in scans if int(r['threshold'])<=1500]
s=[r for r in scans if int(r['threshold'])<=1500]
for field,label,c in [('tail_fraction','Calls above H','#176b87'),('tail_token_share','All reasoning in tail calls','#ac5c20'),('excess_fraction','Text strictly above H','#c43137')]:
    axes[2].plot(h,[float(r[field])*100 for r in s],marker='o',label=label,color=c)
axes[2].set(xlabel='Threshold H (tokens)',ylabel='Share (%)',title='Tail mass is not removable-token mass')
axes[2].legend(frameon=False,fontsize=8)
axes[2].grid(alpha=.2)
fig.suptitle('Noemate / Qwen3.6-27B: output-capped observation; no compression benefit measured',fontsize=13)
for ax in axes:
    ax.spines[['top','right']].set_visible(False)
fig.savefig(out/'reasoning_distribution.png',dpi=180)
fig.savefig(out/'reasoning_distribution.svg')
plt.close(fig)
