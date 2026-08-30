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
from agent_runtime.memory.state import MemoryStateError, transition_state, validate_transition

__all__ = [
    "MemoryRecord",
    "MemoryScope",
    "MemoryStateError",
    "is_recallable",
    "scope_matches",
    "transition_state",
    "validate_transition",
]
