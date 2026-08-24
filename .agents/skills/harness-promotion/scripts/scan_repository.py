#!/usr/bin/env python3
from pathlib import Path
import sys
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve()
for candidate in (HERE.parents[3] / "shared" / "scripts", HERE.parents[2] / ".harness-armor" / "scripts"):
    if (candidate / "harness_armor").is_dir(): sys.path.insert(0, str(candidate)); break
else: raise SystemExit("Harness Armor runtime not found; run `npx harness-armor doctor`.")
from harness_armor.cli import main_scan  # noqa: E402
raise SystemExit(main_scan())
