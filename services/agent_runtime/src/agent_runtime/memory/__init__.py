"""Governed memory contracts for Phase 8.

Memory is durable application data only after a later Frappe-governed review
flow.  Content represented here is always untrusted data: it cannot grant ERP
or model capabilities, change policy, or authorize a tool call.
"""

from agent_runtime.memory.contracts import (
    MemoryRecord,
    MemoryScope,
    is_recallable,
    scope_matches,
)
from agent_runtime.memory.decisions import approve_candidate, approve_correction, reject_candidate
from agent_runtime.memory.persistence import (
    AtomicCorrectionCommand,
    CandidateInsertCommand,
    MemoryPersistenceError,
    MemoryPersistenceErrorCode,
    MemoryPersistencePort,
    SingleRecordCasCommand,
)
from agent_runtime.memory.sqlite_store import (
    MEMORY_DB_PATH_ENV,
    MEMORY_SCHEMA_VERSION,
    SQLiteMemoryStore,
)
from agent_runtime.memory.state import MemoryStateError, transition_state, validate_transition

__all__ = [
    "MEMORY_DB_PATH_ENV",
    "MEMORY_SCHEMA_VERSION",
    "AtomicCorrectionCommand",
    "CandidateInsertCommand",
    "MemoryPersistenceError",
    "MemoryPersistenceErrorCode",
    "MemoryPersistencePort",
    "MemoryRecord",
    "MemoryScope",
    "MemoryStateError",
    "SQLiteMemoryStore",
    "SingleRecordCasCommand",
    "approve_candidate",
    "approve_correction",
    "is_recallable",
    "reject_candidate",
    "scope_matches",
    "transition_state",
    "validate_transition",
]
