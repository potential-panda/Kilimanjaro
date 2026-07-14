# Expression scan configs

Run configs for `tools/grid_scan_expr.py` — each names its hypothesis
(`specs/hyp-*.yaml`) so the run lands in the ledger. These are scan
*configurations*, not hypothesis specs; the hypothesis contract lives in
`specs/`.

```bash
python tools/grid_scan_expr.py --spec scan-specs/momentum_eth.yaml \
    --start 2025-07-01 --end 2026-06-30 --split 2026-03-01
```
