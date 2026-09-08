# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 480 | 480 | 0 | 39 | 39 | 10409.678 | 33690.613 | 555.982 |
| vanilla | 480 | 478 | 2 | 46 | 48 | 10019.292 | 33726.629 | 553.828 |

## Paired latency

- All pairs: 480
- Protocol-clean pairs: 298
- Fully clean pairs: 242
- Median DTB minus Vanilla: 60.889 ms
- Median DTB speedup: -1.126%
- DTB faster fully clean pairs: 117/242
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
