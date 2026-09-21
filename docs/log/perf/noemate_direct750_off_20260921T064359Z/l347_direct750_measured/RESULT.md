# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 4 | 4 | 0 | 2 | 2 | 12309.45 | 16797.32 | 553.514 |
| vanilla | 4 | 4 | 0 | 1 | 1 | 8389.866 | 13284.415 | 552.591 |

## Paired latency

- All pairs: 4
- Protocol-clean pairs: 4
- Fully clean pairs: 2
- Median DTB minus Vanilla: 2008.322 ms
- Median DTB speedup: -18.005%
- DTB faster fully clean pairs: 0/2
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
