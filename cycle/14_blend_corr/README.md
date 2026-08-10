# 14_blend_corr — weighted-blend correlation screen

**Stage 14 of the loop** (see `doc/research-pipeline.md`). Medium cost.

Constructs **weighted combinations** of the factors screened in 13 and ranks them by the
**blend's IC**. The composite's edge comes from combining weak, weakly-correlated signals —
so this stage owns factor **inclusion + weighting** (baseline: IC-weighted + decorrelation).
A strong standalone factor competes here as a **blend of one**.

Only the **top blends** pass to `15_return_eval`. Module: `driver/blend_corr.py`
(command: `cli.py blend`).
