# Expression scan configs

Run configs for `cli.py grid expr` — each names its hypothesis
(`specs/hyp-*.yaml`) so the run lands in the ledger. These are scan
*configurations*, not hypothesis specs; the hypothesis contract lives in
`specs/`.

```bash
python src/cli.py grid expr --spec scan-specs/momentum_eth.yaml \
    --start 2025-07-01 --end 2026-06-30 --split 2026-03-01
```
