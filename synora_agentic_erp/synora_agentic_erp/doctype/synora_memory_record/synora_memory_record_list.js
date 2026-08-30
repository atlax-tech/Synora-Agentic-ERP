frappe.listview_settings["Synora Memory Record"] = {
	add_fields: ["kind", "state", "company_scope", "warehouse_scope", "source_revision", "expires_at"],
	get_indicator: function (doc) {
		const labels = {
			PENDING: [__("Pending"), "orange"],
			APPROVED: [__("Approved"), "green"],
			REJECTED: [__("Rejected"), "red"],
			SUPERSEDED: [__("Superseded"), "darkgrey"],
			EXPIRED: [__("Expired"), "grey"],
			DELETED: [__("Deleted"), "grey"],
		};
		const indicator = labels[doc.state] || [__("Unavailable"), "grey"];
		return [indicator[0], indicator[1], "state,=," + doc.state];
	},
};
