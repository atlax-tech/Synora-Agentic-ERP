# Phase 12 invalidated replay and training archive

These replay records, selection receipts, active pointer, and training weights
were generated before the `ReplayState` case-label isolation and candidate digest
binding fixes. They remain available for audit history but are not current Phase
12 evidence. New artifacts must be generated from the post-fix code version in
the active `output/phase12/` directory.

The live baseline JSONL remains active as legacy provider evidence; its records
predate persisted reservation keys and are not used to reconcile new batches.
