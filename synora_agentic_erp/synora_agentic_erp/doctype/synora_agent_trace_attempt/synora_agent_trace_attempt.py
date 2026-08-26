import frappe
from frappe.model.document import Document

IMMUTABLE_FIELDS = {
    "run",
    "attempt",
    "mode",
    "provider",
    "model",
    "prompt_schema_version",
    "tool_schema_version",
    "events_json",
    "events_count",
    "stop_reason",
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "cost_microusd",
    "elapsed_ms",
    "status",
    "correlation_id",
}


class SynoraAgentTraceAttempt(Document):  # type: ignore[misc]
    def validate(self) -> None:
        if self.is_new():
            return
        if any(self.has_value_changed(field) for field in IMMUTABLE_FIELDS):
            frappe.throw("Synora Agent Trace Attempt is immutable")
