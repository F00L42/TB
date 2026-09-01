# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 300 | 299 | 1 | 26 | 26 | 10627.631 | 28358.822 | 663.716 |
| vanilla | 300 | 298 | 2 | 32 | 34 | 9869.376 | 28343.337 | 578.016 |

## Paired latency

- All pairs: 300
- Protocol-clean pairs: 297
- Fully clean pairs: 255
- Median DTB minus Vanilla: 552.505 ms
- Median DTB speedup: -8.216%
- DTB faster fully clean pairs: 99/255
- Latency ranking valid: False

Latency ranking is valid only when every latency-eligible pair is free of protocol anomalies; run quality probes separately from ranking.
