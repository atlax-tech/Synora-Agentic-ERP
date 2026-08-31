app_name = "synora_agentic_erp"
app_title = "Synora Agentic ERP"
app_publisher = "Atlax-Tech"
app_description = "Governed Agentic Enterprise Operations for ERPNext"
app_email = ""
app_license = "MIT"

doctype_js = {
    "Material Request": "public/js/contextual_coach.js",
    "Purchase Order": "public/js/contextual_coach.js",
}

permission_query_conditions = {
    "Synora Memory Record": (
        "synora_agentic_erp.synora_agentic_erp.doctype.synora_memory_record."
        "synora_memory_record.get_permission_query_conditions"
    ),
}

has_permission = {
    "Synora Memory Record": (
        "synora_agentic_erp.synora_agentic_erp.doctype.synora_memory_record."
        "synora_memory_record.has_permission"
    ),
    "Synora Coach Result": (
        "synora_agentic_erp.synora_agentic_erp.doctype.synora_coach_result."
        "synora_coach_result.has_permission"
    ),
}
