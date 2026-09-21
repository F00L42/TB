# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 822 | 822 | 0 | 32 | 32 | 16755.772 | 26590.228 | 640.409 |
| vanilla | 822 | 821 | 1 | 35 | 36 | 17862.9 | 29626.821 | 1002.446 |

## Paired latency

- All pairs: 822
- Protocol-clean pairs: 821
- Fully clean pairs: 777
- Median DTB minus Vanilla: -968.124 ms
- Median DTB speedup: 5.591%
- DTB faster fully clean pairs: 471/777
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
