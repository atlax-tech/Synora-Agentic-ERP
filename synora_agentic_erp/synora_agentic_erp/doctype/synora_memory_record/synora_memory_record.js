frappe.ui.form.on("Synora Memory Record", {
	refresh: function (frm) {
		frm.disable_save();
		frm.set_intro(__("Content is UNTRUSTED. Review it as data before approving."), "orange");
		if (frm.doc.state !== "PENDING") {
			return;
		}

		function review(decision) {
			frappe.call({
				method: "synora_agentic_erp.api.review_memory_candidate",
				type: "POST",
				args: {
					memory_id: frm.doc.name,
					decision: decision,
					expected_state_version: frm.doc.state_version,
					reason: null,
				},
				freeze: true,
			}).then(function (response) {
				const message = response && response.message;
				if (!message || !message.ok) {
					frappe.msgprint(__("Memory review is unavailable."));
					return;
				}
				frappe.show_alert({ message: __("Review saved."), indicator: "green" });
				frm.reload_doc();
			});
		}

		frm.add_custom_button(__("Approve"), function () {
			review("APPROVE");
		}, __("Review"));
		frm.add_custom_button(__("Reject"), function () {
			review("REJECT");
		}, __("Review"));
	},
});
