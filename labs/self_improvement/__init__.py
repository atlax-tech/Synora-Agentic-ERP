"""Bounded, offline-only Phase 12 learning experiments.

Nothing in this package is imported by the business Runtime.  The public
surface is intentionally small and file based so every experiment can be
reviewed, reproduced, and discarded without changing ERP state.
"""

import sys
from pathlib import Path

from .contracts import (
    CandidateVersion,
    DatasetCase,
    DatasetManifest,
    ExperimentRecord,
    LabSelection,
    ReviewedCase,
    TrainingArtifact,
)

_RUNTIME_SRC = Path(__file__).resolve().parents[2] / "services" / "agent_runtime" / "src"
if _RUNTIME_SRC.is_dir() and str(_RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_SRC))

__all__ = [
    "CandidateVersion",
    "DatasetCase",
    "DatasetManifest",
    "ExperimentRecord",
    "LabSelection",
    "ReviewedCase",
    "TrainingArtifact",
]
