# 16_calibration — scan ↔ Himalaya fidelity

**Stage 16 of the loop** (see `doc/research-pipeline.md`). Cheap if the mechanism is certified.

Before promoting a survivor, confirm the **fast offline scan** reproduces the **Himalaya
backtest** (the ground truth that runs the live codebase). Fidelity lives in the *mechanism*
(entry style + return calc + execution), not the formula (`doc/calibration.md`) — so all
blends using an already-certified mechanism inherit the trust; a full scan↔Himalaya run is
only needed for a new mechanism.

Package: `kilimanjaro/calibration/` (command: `cli.py calibrate`, factor + trade layers).
