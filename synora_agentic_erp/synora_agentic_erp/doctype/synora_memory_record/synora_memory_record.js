frappe.ui.form.on("Synora Memory Record", {
	refresh: function (frm) {
		frm.disable_save();
		frm.set_intro(__("Content is UNTRUSTED. Review it as data before approving."), "orange");
		if (frm.doc.state !== "PENDING") {
			return;
		}

		function review(decision, predecessorStateVersion) {
			var args = {
				memory_id: frm.doc.name,
				decision: decision,
				expected_state_version: frm.doc.state_version,
				reason: null,
			};
			if (frm.doc.supersedes_memory) {
				if (typeof predecessorStateVersion !== "number") {
					frappe.msgprint(__("The predecessor version is unavailable; retry the review."));
					return;
				}
				args.expected_predecessor_state_version = predecessorStateVersion;
			}
			frappe.call({
				method: "synora_agentic_erp.api.review_memory_candidate",
				type: "POST",
				args: args,
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

		function addReviewButtons(predecessorStateVersion) {
			frm.add_custom_button(__("Approve"), function () {
				review("APPROVE", predecessorStateVersion);
			}, __("Review"));
			frm.add_custom_button(__("Reject"), function () {
				review("REJECT", predecessorStateVersion);
			}, __("Review"));
		}

		if (!frm.doc.supersedes_memory) {
			addReviewButtons(null);
			return;
		}

		frappe.call({
			method: "synora_agentic_erp.api.get_memory_review_candidate",
			type: "GET",
			args: { memory_id: frm.doc.name },
			freeze: true,
		}).then(function (response) {
			var message = response && response.message;
			var memory = message && message.memory;
			if (!memory || typeof memory.predecessor_state_version !== "number") {
				frappe.msgprint(__("The predecessor version is unavailable; retry the review."));
				return;
			}
			addReviewButtons(memory.predecessor_state_version);
		});
	},
});
