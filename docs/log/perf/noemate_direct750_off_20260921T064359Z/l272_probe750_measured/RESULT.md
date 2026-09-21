# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 0 | 0 | 22273.333 | 23113.753 | 656.152 |
| vanilla | 4 | 4 | 0 | 0 | 0 | 22598.929 | 26238.793 | 652.846 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 4
- Fully clean pairs: 4
- Median DTB minus Vanilla: -934.297 ms
- Median DTB speedup: 3.348%
- DTB faster fully clean pairs: 2/4
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
