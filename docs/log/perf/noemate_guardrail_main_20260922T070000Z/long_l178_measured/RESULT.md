# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 10 | 10 | 0 | 0 | 0 | 13444.189 | 22107.322 | 723.639 |
| vanilla | 10 | 10 | 0 | 0 | 0 | 25128.109 | 25846.183 | 722.315 |

## Paired latency

- All pairs: 10
- Protocol-clean pairs: 10
- Fully clean pairs: 10
- Median DTB minus Vanilla: -7862.359 ms
- Median DTB speedup: 37.06%
- DTB faster fully clean pairs: 10/10
- Latency ranking valid: True

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
