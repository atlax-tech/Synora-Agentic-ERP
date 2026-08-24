#!/usr/bin/env python3
"""Relocatable entry point for the canonical Harness Armor detector."""

from pathlib import Path
import sys
sys.dont_write_bytecode = True

HERE = Path(__file__).resolve()
CANDIDATES = (HERE.parents[3] / "shared" / "scripts", HERE.parents[2] / ".harness-armor" / "scripts")
for candidate in CANDIDATES:
    if (candidate / "harness_armor").is_dir():
        sys.path.insert(0, str(candidate))
        break
else:
    raise SystemExit("Harness Armor runtime not found; run `npx harness-armor doctor`.")

from harness_armor.cli import main_detect_state  # noqa: E402

raise SystemExit(main_detect_state())
