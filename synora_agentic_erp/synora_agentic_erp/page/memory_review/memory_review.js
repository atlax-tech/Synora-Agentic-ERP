frappe.pages["memory-review"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Memory Review"),
		single_column: true,
	});

	const root = document.createElement("section");
	root.className = "memory-review p-4";
	root.setAttribute("aria-labelledby", "memory-review-title");
	const heading = document.createElement("h2");
	heading.id = "memory-review-title";
	heading.textContent = __("Memory review");
	root.appendChild(heading);
	const status = document.createElement("div");
	status.className = "text-muted mb-3";
	status.setAttribute("role", "status");
	status.setAttribute("aria-live", "polite");
	root.appendChild(status);
	const queue = document.createElement("div");
	queue.className = "row";
	root.appendChild(queue);
	page.main[0].appendChild(root);

	let selected = null;
	let in_flight = false;
	let queue_items = [];

	function element(tag, text) {
		const node = document.createElement(tag);
		if (text !== undefined) {
			node.textContent = text;
		}
		return node;
	}

	function set_status(message, class_name) {
		status.className = class_name || "text-muted mb-3";
		status.textContent = message;
	}

	function error_message(response) {
		const error = response && response.message && response.message.error;
		return (error && error.message) || __("Memory review is unavailable.");
	}

	function call(method, args) {
		return frappe.call({
			method: method,
			type: method.indexOf("review_memory_candidate") >= 0 ? "POST" : "GET",
			args: args,
		});
	}

	function render_detail(memory) {
		const detail = element("article");
		detail.className = "col-md-8 border rounded p-3";
		const title = element("h3", __("Candidate details"));
		detail.appendChild(title);
		const metadata = element("dl", "");
		metadata.className = "row small";
		[
			[__("Kind"), memory.kind],
			[__("State"), memory.state],
			[__("Company"), memory.company_scope],
			[__("Warehouse"), memory.warehouse_scope || __("None")],
			[__("Source revision"), memory.source_revision],
			[__("Expiry"), memory.expires_at || __("None")],
			[__("State version"), String(memory.state_version)],
		].forEach(function (pair) {
			const label = element("dt", pair[0]);
			label.className = "col-sm-4";
			const value = element("dd", pair[1]);
			value.className = "col-sm-8";
			metadata.appendChild(label);
			metadata.appendChild(value);
		});
		detail.appendChild(metadata);
		const warning = element("p", __("UNTRUSTED content — review before approving."));
		warning.className = "alert alert-warning";
		detail.appendChild(warning);
		const content = element("pre", memory.content);
		content.className = "small text-wrap border rounded p-2";
		detail.appendChild(content);
		const reason = document.createElement("textarea");
		reason.className = "form-control mb-2";
		reason.rows = 3;
		reason.maxLength = 2000;
		reason.placeholder = __("Optional review reason");
		detail.appendChild(reason);
		const actions = element("div");
		const approve = element("button", __("Approve"));
		approve.className = "btn btn-primary mr-2";
		const reject = element("button", __("Reject"));
		reject.className = "btn btn-secondary";
		function review(decision) {
			if (in_flight || !selected) {
				return;
			}
			in_flight = true;
			approve.disabled = true;
			reject.disabled = true;
			set_status(__("Saving review…"));
			call("synora_agentic_erp.api.review_memory_candidate", {
				memory_id: selected.name,
				decision: decision,
				expected_state_version: selected.state_version,
				reason: reason.value || null,
			})
				.then(function (response) {
					if (!response.message || !response.message.ok) {
						throw new Error(error_message(response));
					}
					selected = null;
					render_queue();
				})
				.catch(function (error) {
					set_status(error.message || __("Review failed."), "text-danger mb-3");
					approve.disabled = false;
					reject.disabled = false;
				})
				.then(function () {
					in_flight = false;
				}, function () {
					in_flight = false;
				});
		}
		approve.addEventListener("click", function () {
			review("APPROVE");
		});
		reject.addEventListener("click", function () {
			review("REJECT");
		});
		actions.appendChild(approve);
		actions.appendChild(reject);
		detail.appendChild(actions);
		return detail;
	}

	function load_detail(name) {
		set_status(__("Loading candidate…"));
		call("synora_agentic_erp.api.get_memory_review_candidate", { memory_id: name })
			.then(function (response) {
				if (!response.message || !response.message.ok) {
					throw new Error(error_message(response));
				}
				selected = response.message.memory;
				queue.replaceChildren(render_queue_list(queue_items), render_detail(selected));
				set_status(__("Candidate loaded."));
			})
			.catch(function (error) {
				set_status(error.message || __("Candidate is not available."), "text-danger mb-3");
			});
	}

	function render_queue_list(items) {
		const list = element("aside");
		list.className = "col-md-4 mb-3";
		const heading_node = element("h3", __("Candidates"));
		list.appendChild(heading_node);
		items = Array.isArray(items) ? items : [];
		if (!items.length) {
			list.appendChild(element("p", __("No visible candidates.")));
			return list;
		}
		items.forEach(function (item) {
			const button = element("button", item.source_revision + " · " + item.kind);
			button.className = "btn btn-light btn-block text-left mb-1";
			button.addEventListener("click", function () {
				load_detail(item.name);
			});
			list.appendChild(button);
		});
		return list;
	}

	function render_queue() {
		set_status(__("Loading candidates…"));
		call("synora_agentic_erp.api.list_memory_review_queue", { limit: 50, offset: 0 })
			.then(function (response) {
				if (!response.message || !response.message.ok) {
					throw new Error(error_message(response));
				}
				queue_items = response.message.items || [];
				queue.replaceChildren(render_queue_list(queue_items));
				if (!queue_items.length) {
					set_status(__("No visible candidates."));
					return;
				}
				set_status(__("Select a candidate to review."));
			})
			.catch(function (error) {
				queue.replaceChildren();
				set_status(error.message || __("Memory review is unavailable."), "text-danger mb-3");
			});
	}

	render_queue();
};
