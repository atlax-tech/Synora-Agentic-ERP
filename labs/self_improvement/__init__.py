"""Bounded, offline-only Phase 12 learning experiments.

Nothing in this package is imported by the business Runtime.  The public
surface is intentionally small and file based so every experiment can be
reviewed, reproduced, and discarded without changing ERP state.
"""

from .contracts import (
    CandidateVersion,
    DatasetCase,
    DatasetManifest,
    ExperimentRecord,
    LabSelection,
    ReviewedCase,
    TrainingArtifact,
)

__all__ = [
    "CandidateVersion",
    "DatasetCase",
    "DatasetManifest",
    "ExperimentRecord",
    "LabSelection",
    "ReviewedCase",
    "TrainingArtifact",
]
