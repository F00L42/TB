"""Render measured request-order latency, without attributing server phases."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parent
metrics = json.loads((root / 'pool_h750_audit.json').read_text(encoding='utf-8'))['metrics']
a, b = metrics['attempt1_V_first'], metrics['attempt2_D_first']
first = [a['v_first_mean_ms'], a['d_first_mean_ms'], b['d_first_mean_ms'], b['v_first_mean_ms']]
remaining = [a['v_after_first_mean_ms'], a['d_after_first_mean_ms'], b['d_after_first_mean_ms'], b['v_after_first_mean_ms']]
first = [x / 1000 for x in first]; remaining = [x / 1000 for x in remaining]
fig, ax = plt.subplots(figsize=(10, 5))
x = range(4)
ax.bar(x, first, color='#d88432', width=.58, label='Before first semantic output')
ax.bar(x, remaining, bottom=first, color='#42677d', width=.58, label='First output to completion')
for i, (f, r) in enumerate(zip(first, remaining)):
    ax.text(i, f + r + .35, f'{f+r:.3f}s', ha='center', fontsize=12)
    ax.text(i, f + r / 2, f'{r:.3f}s', ha='center', va='center', color='white', fontsize=11)
    ax.text(i, f / 2, f'{f:.3f}s', ha='center', va='center', color='black', fontsize=10)
ax.set_xticks(list(x), ['V1\nfirst request', 'D1\nsecond request', 'D2\nthird request', 'V2\nfourth request'])
ax.set_ylabel('Mean request latency (seconds)')
ax.set_ylim(0, 24)
ax.set_title('H750 replay: first-output latency depends on execution position', loc='left', pad=18)
ax.legend(loc='upper right', frameon=False)
ax.spines[['top', 'right']].set_visible(False)
fig.text(.08, .025, '411 cases per bar. APC enabled (user confirmed). These intervals are not server prefill/decode phases.', fontsize=9)
fig.tight_layout(rect=(0, .065, 1, 1))
fig.savefig(root / 'order_effect.png', dpi=170)
fig.savefig(root / 'order_effect.svg')
