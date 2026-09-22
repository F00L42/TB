"""Standard scientific plots from Session B tables. No inference or guessed timing.

uv run --no-project --with matplotlib python sources/plot_evidence.py
"""
from pathlib import Path
import csv
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

BASE=Path(__file__).resolve().parents[1]
E=BASE/'evidence'
F=E/'figures'
F.mkdir(exist_ok=True)
font=next((x for x in ['Microsoft YaHei','Noto Sans CJK SC','SimHei'] if x in {f.name for f in font_manager.fontManager.ttflist}),None)
if not font:raise RuntimeError('Install a CJK font before plotting')
plt.rcParams.update({'font.family':font,'axes.unicode_minus':False,'svg.fonttype':'none',
                     'font.size':12,'axes.spines.top':False,'axes.spines.right':False,
                     'axes.labelcolor':'#536B7C','text.color':'#142C3D','axes.edgecolor':'#CDD8DE',
                     'xtick.color':'#536B7C','ytick.color':'#536B7C','pdf.fonttype':42})
C={'gray':'#AEBFC8','gold':'#B16A16','teal':'#087F7C','blue':'#496C8A','red':'#B34740'}


def read(name):return json.loads((E/name).read_text(encoding='utf-8'))
def save(fig,name):
    for suffix in ['png','svg','pdf']:fig.savefig(F/f'{name}.{suffix}',dpi=180,bbox_inches='tight')
    plt.close(fig)


d=read('distribution.json');rows=d['requests'];s=d['summary']
x=np.array([r['hours_from_start'] for r in rows]);y=np.array([r['reasoning_tokens'] for r in rows]);high=y>750
fig,ax=plt.subplots(figsize=(15,6.7))
fig.subplots_adjust(left=.07,right=.985,top=.82,bottom=.23)
ax.scatter(x[~high],y[~high],s=7,facecolors='none',edgecolors=C['gray'],linewidths=.4)
ax.scatter(x[high],y[high],s=15,color=C['gold'],zorder=3)
ax.axhline(750,color=C['gold'],lw=1.5)
ax.set(xlim=(0,12.15),ylim=(0,2000),xticks=np.arange(0,13,2),yticks=[0,250,500,750,1000,1500,2000],
       xlabel='实际到达时间：距首请求的小时数（首请求为北京时间 09/17 20:37:51）',ylabel='可见思考长度 / token')
ax.grid(axis='y',color='#EDF1F4');ax.set_axisbelow(True)
fig.text(.07,.93,'核心网运维 Agent 的 Vanilla 思考长度分布',fontsize=24,weight='bold')
fig.text(.07,.865,'有效请求 8,277 条；每点一条请求，金色点为 >750，金线为750 token介入参考线',fontsize=14,color='#536B7C')
fig.text(.07,.11,f"7,862 条（94.99%）≤750     415 条（5.01%）>750     P50 / P95 / P99 = 207 / 753 / 1,002",fontsize=16,color=C['teal'],weight='bold')
fig.text(.07,.04,'采集 baseline 标记，服务端原生开关未独立确认。主请求排除29条，辅助3,908条另列。多数请求总输出上限2048。\n阈值只标记候选介入范围，不代表实际旁路率、可安全删除量或实测收益。',fontsize=10,color='#536B7C')
save(fig,'vanilla_arrival')

# Missing reasoning stays missing. Auxiliary calls are arrival ticks, never zero-token samples.
allrows=list(csv.DictReader((E/'tables/traffic_all.csv').open(encoding='utf-8-sig')))
fig,axes=plt.subplots(3,1,figsize=(15,6.5),sharex=True,gridspec_kw={'height_ratios':[4,1,1]},layout='constrained')
axes[0].scatter(x[~high],y[~high],s=4,color=C['gray']);axes[0].scatter(x[high],y[high],s=10,color=C['gold'])
axes[0].axhline(750,color=C['gold']);axes[0].set(ylabel='主Agent / token',ylim=(0,2000),title='按实际到达时间分层，辅助与排除请求不填零')
for ax,role,label in [(axes[1],'recommendation','推荐问题：3,897'),(axes[2],'compaction','对话压缩：11')]:
    xx=[float(r['hours_from_start']) for r in allrows if r['role']==role]
    ax.plot(xx,[1]*len(xx),'|',color=C['blue'],ms=11);ax.set(yticks=[],ylim=(.7,1.5))
    ax.text(.01,.91,label,transform=ax.transAxes,fontsize=11)
excluded=[float(r['hours_from_start']) for r in allrows if r['role']=='main_agent' and r['eligible']=='False']
axes[0].plot(excluded,[-60]*len(excluded),'x',color=C['red'],clip_on=False,ms=5)
axes[0].text(.01,.92,'主Agent 8,306次，分布有效8,277次，排除29次（红叉仅定位到达，不编码长度）',transform=axes[0].transAxes,fontsize=11)
axes[-1].set(xlabel='距首请求小时数；辅助行仅表示请求到达，无可比思考长度',xlim=(0,12.15))
save(fig,'traffic_by_role')

fig,axes=plt.subplots(1,2,figsize=(13,5),layout='constrained')
bins=np.arange(.5,2001,50);count,edges=np.histogram(y,bins=bins)
axes[0].bar(edges[:-1],count,width=48,align='edge',color=[C['gold'] if a>750 else C['gray'] for a in edges[:-1]])
axes[0].axvline(750,color=C['gold'],ls='--');axes[0].set(xlabel='可见思考长度 / token',ylabel='请求数',title='直方图：每箱50 token')
sy=np.sort(y);axes[1].step(sy,np.arange(1,len(sy)+1)/len(sy)*100,where='post',color=C['teal'])
axes[1].axvline(750,color=C['gold'],ls='--');axes[1].axhline((~high).mean()*100,color=C['gray'],ls=':')
axes[1].text(850,87,'94.99% ≤750\n5.01% >750',color=C['gold'])
axes[1].set(xlabel='可见思考长度 / token',ylabel='累计请求占比 / %',title='ECDF：分母8,277条',ylim=(0,102))
fig.suptitle('主 Agent 完整可观测子集；输出受限，不代表无上限分布')
save(fig,'length_ecdf_hist')

p=read('paired.json');pairs=p['pairs'];cases=p['cases'];long=[q for q in pairs if q['group']=='long']
fig,axes=plt.subplots(2,5,figsize=(17,7),layout='constrained')
for col,n in enumerate([178,259,319,353,399]):
    pp=[q for q in long if q['line']==n]
    for row,field,unit in [(0,'ms','E2E / 秒'),(1,'reasoning','可见思考 / token')]:
        ax=axes[row,col];scale=1000 if field=='ms' else 1
        for q in pp:
            ax.plot([q['attempt']-.13,q['attempt']+.13],[q['v_'+field]/scale,q['d_'+field]/scale],color='#CBD5DB',lw=1)
        ax.scatter([q['attempt']-.13 for q in pp],[q['v_'+field]/scale for q in pp],color=C['blue'],s=22,label='Vanilla')
        ax.scatter([q['attempt']+.13 for q in pp],[q['d_'+field]/scale for q in pp],color=C['gold'],s=22,label='DTB')
        ax.set(xticks=[1,3,5,7,10],xlabel='配对 attempt',ylabel=unit if col==0 else '')
        if row==0:
            m=cases[f'long_l{n}_measured'];ax.set_title(f"line {n}，更快{m['faster']}/10\n合计E2E缩短{m['ms']['gain_pct']:.2f}%")
        else:ax.axhline(750,color=C['gold'],ls='--',lw=.8)
        ax.grid(axis='y',alpha=.2)
axes[0,0].legend(frameon=False,fontsize=10)
fig.suptitle('冻结长集50对，off同轮对照，所有测量对均保留；长度/时延不构成语义正确性认证',fontsize=15)
save(fig,'frozen_long_pairs')

fig,axes=plt.subplots(1,3,figsize=(17,6),layout='constrained')
names=[f'{g}_l{n}_measured' for g in ['short_greedy','short_sampling'] for n in [21,321,365,394]]
labels=[name.replace('short_greedy_l','greedy / ').replace('short_sampling_l','sampling / ').replace('_measured','') for name in names]
for i,name in enumerate(names):
    m=cases[name]
    for ax,field,upper in [(axes[0],'ms','e2e_delta_one_sided_95_upper_ms'),(axes[1],'first','first_delta_one_sided_95_upper_ms')]:
        mean=m[field]['delta_mean'];hi=m['block_bootstrap'][upper]
        ax.plot([mean,hi],[i,i],color=C['blue'],lw=1.5);ax.scatter([mean],[i],color=C['blue'],s=30);ax.plot(hi,i,'|',color=C['gold'],ms=12)
for ax,title in zip(axes[:2],['全样本E2E差','首输出差']):
    ax.axvline(0,color='#AEBFC8',lw=1);ax.set(yticks=range(8),yticklabels=labels,xlabel='DTB − Vanilla / ms',title=title);ax.invert_yaxis();ax.grid(axis='x',alpha=.2)
sets=[('greedy 全80对',[q for q in pairs if q['group']=='short_greedy']),
      ('greedy 同输出14对',[q for q in pairs if q['group']=='short_greedy' and q['same_output']]),
      ('sampling 全80对',[q for q in pairs if q['group']=='short_sampling']),
      ('sampling 同输出23对',[q for q in pairs if q['group']=='short_sampling' and q['same_output']]),
      ('同输出合计37对',[q for q in pairs if q['group'] in ['short_sampling','short_greedy'] and q['same_output']])]
for i,(label,pp) in enumerate(sets):
    delta=np.mean([q['d_ms']-q['v_ms'] for q in pp]);axes[2].scatter(delta,i,color=C['teal'] if '同输出' in label else C['blue']);axes[2].annotate(f'{delta:+.2f}',(delta,i),xytext=(7,5),textcoords='offset points',fontsize=10)
axes[2].set(yticks=range(len(sets)),yticklabels=[s[0] for s in sets],title='事后同输出敏感性',xlabel='平均E2E差 / ms');axes[2].invert_yaxis();axes[2].axvline(0,color=C['gray']);axes[2].margins(x=.4)
fig.suptitle('每组20对；线段从均值到探索性单侧95%上界，非双侧置信区间。正值为变慢。',fontsize=14)
save(fig,'short_sensitivity')

ph=read('phase.json');target=ph['selected'];events=[r['event'] for r in ph['events'] if r['event_type']=='DTB_PHASE']
ev={r['phase']:r for r in events};inter=target['intervention']
fig,axes=plt.subplots(1,2,figsize=(15,5.8),gridspec_kw={'width_ratios':[2,1]},layout='constrained')
names=['native_passthrough','decision','final_answer'];vals=[ev[n]['wall_ms']/1000 for n in names]
axes[0].barh(range(3),vals,color=[C['blue'],C['gray'],C['gold']]);axes[0].set(yticks=range(3),yticklabels=['原生观察：753 token','预算决策：0 token','直接final：168 token'],xlabel='各phase wall / 秒（独立耗时，不重建时间轴）',xlim=(0,11.8));axes[0].invert_yaxis()
for i,v in enumerate(vals):axes[0].text(v+.15,i,f'{v:.6f}s',va='center')
small=[inter['abort_wall_ms'],inter['abort_drain_wall_ms'],inter['handoff_wall_ms']]
axes[1].barh(range(3),small,color=C['teal']);axes[1].set(yticks=range(3),yticklabels=['abort','drain','handoff'],xlabel='ms（诊断字段可能嵌套，不求和）',xlim=(0,.36));axes[1].invert_yaxis()
for i,v in enumerate(small):axes[1].text(v+.005,i,f'{v:.3f}',va='center',fontsize=11)
fig.suptitle('真实phase：399/a2在753 token触发，abort一次，drain尾部0，直接final，无probe',fontsize=15)
fig.supxlabel(f"仅机制例：phase run 031321Z，E2E {target['e2e_ms']/1000:.3f}s；off成功卡为另一请求。model_work_complete=false",fontsize=11)
save(fig,'phase_mechanism')
print('Exported 6 scientific figures as PNG / SVG / PDF')
