# Tool-call paired streaming replay

## Protocol

| Variant | Requests | Pass | Failure | E2E median ms | E2E P95 ms | First output median ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| dtb | 40 | 40 | 0 | 12201.095 | 47507.573 | 645.18 |
| vanilla | 40 | 40 | 0 | 19780.855 | 46737.517 | 540.394 |

## Paired latency

- All pairs: 40
- Protocol-clean pairs: 40
- Median DTB minus Vanilla: 567.793 ms
- Median DTB speedup: -1.772%
- DTB faster clean pairs: 17/40
- Latency ranking valid: True

Latency ranking is valid only when every pair is free of protocol anomalies.

## Server audit status

The archived `analysis.json` was generated before the 2026-08-28 analyzer
compatibility fix and must not be used for lifecycle acceptance. Its 40 DTB
requests all record one source terminal, one final-terminal passthrough, no
synthetic terminal, and no terminal-passthrough violation, but the old analyzer
incorrectly required `terminal_flush=1` from historical schema 1.

The raw `server.log` is not included in this directory, so the corrected
analyzer cannot recheck every production-schema delta from the uploaded
artifacts alone. Rerun `tools/dtb/analyze_tool_call_run.py` against the original
server log before replacing `analysis.json` or declaring 40/40 server-audit
acceptance.
