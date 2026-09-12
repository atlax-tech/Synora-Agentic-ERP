# Invalid local baseline evaluation batch

These local task-evaluation artifacts were generated on 2026-09-12 before the
baseline record identifier included the random/initial seed. The records for
the three seeds therefore collided across files and cannot satisfy the
stage-wide unique experiment-id contract. They are preserved as invalid
evidence and are excluded from the active `output/phase12/` directory.

They must not be used as Phase 12 completion evidence. The corrected commands
were rerun into the active directory after adding the seed to each identifier.
