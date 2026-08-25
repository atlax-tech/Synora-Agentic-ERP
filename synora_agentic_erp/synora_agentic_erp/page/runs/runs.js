frappe.pages["runs"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Runs"),
		single_column: true,
	});
	page.set_title(__("智能体运行列表"));

	// SPEC §8.1 批准的状态文案（DESIGN 术语表）
	const STATE_COPY = {
		CREATED: __("已创建"),
		ANALYZING: __("分析中"),
		PROPOSED: __("已形成提议"),
		AWAITING_APPROVAL: __("等待审批"),
		EXECUTING: __("执行中"),
		SUCCEEDED: __("已成功"),
		FAILED: __("已失败"),
		CANCELLED: __("已取消"),
		RECONCILIATION_REQUIRED: __("需要对账"),
		DECLINED: __("已拒绝"),
		EXPIRED: __("已过期"),
	};

	const container = $('<div style="padding: 8px;"></div>');
	page.main.append(container);

	const current_user = frappe.session.user;

	function esc(value) {
		return frappe.utils.escape_html(value === null || value === undefined ? "" : value);
	}

	function refresh() {
		container.html('<div class="text-muted text-center py-5"><span class="spinner-border spinner-border-sm"></span> ' + __("加载中…") + "</div>");
		frappe.call({
			method: "synora_agentic_erp.api.list_runs",
			type: "GET",
			callback: function (r) {
				render((r.message && r.message.runs) || []);
			},
			error: function () {
				container.html('<div class="text-danger text-center py-5">' + __("加载运行列表失败，请刷新重试。") + "</div>");
			},
		});
	}

	function render(runs) {
		if (!runs.length) {
			container.html(
				'<div class="text-muted text-center py-5">' +
					__("尚无采购分析。前往 New Run 输入交付或补货目标开始。") +
					'<br><a class="btn btn-primary btn-sm mt-2" href="#new-run">' +
					__("新建运行") +
					"</a></div>"
			);
			return;
		}
	const rows = runs
			.map(function (run) {
				const raw_goal = typeof run.goal === "string" ? run.goal : "";
				const goal = raw_goal.length > 80 ? raw_goal.slice(0, 80) + "…" : raw_goal;
				const cancellable = (run.run_state === "CREATED" || run.run_state === "ANALYZING") && run.initiator === current_user;
				const cancel_btn = cancellable
					? '<button class="btn btn-secondary btn-xs cancel-run" data-run="' +
					  esc(run.run_id) +
					  '">' +
					  __("取消") +
					  "</button>"
					: "";
				const scope = run.company_scope + (run.warehouse_scope ? " / " + run.warehouse_scope : __(" / 全部仓库"));
				return (
					'<tr data-run="' +
					esc(run.run_id) +
					'">' +
					"<td class=\"small text-muted\">" +
					esc(run.run_id.slice(0, 8)) +
					"</td>" +
					'<td title="' +
					esc(raw_goal) +
					'">' +
					esc(goal) +
					"</td>" +
					"<td>" +
					(STATE_COPY[run.run_state] || esc(run.run_state)) +
					"</td>" +
					"<td class=\"small text-muted\">" +
					esc(scope) +
					"</td>" +
					"<td class=\"small text-muted\">" +
					esc((run.created_at || "").replace("T", " ").slice(0, 19)) +
					"</td>" +
					"<td>" +
					cancel_btn +
					"</td>" +
					"</tr>"
				);
			})
			.join("");

		container.html(
			'<table class="table table-hover table-sm">' +
				"<thead><tr>" +
				"<th>" +
				__("Run") +
				"</th>" +
				"<th>" +
				__("目标") +
				"</th>" +
				"<th>" +
				__("状态") +
				"</th>" +
				"<th>" +
				__("范围") +
				"</th>" +
				"<th>" +
				__("创建时间") +
				"</th>" +
				"<th></th>" +
				"</tr></thead>" +
				"<tbody>" +
				rows +
				"</tbody></table>"
		);

		container.find(".cancel-run").on("click", function () {
			const run_id = $(this).data("run");
			cancel_run(run_id, $(this));
		});
	}

	function cancel_run(run_id, button) {
		const original = button.html();
		button.attr("disabled", true).html('<span class="spinner-border spinner-border-sm"></span>');
		frappe.call({
			method: "synora_agentic_erp.api.cancel_run",
			args: {
				run_id: run_id,
				correlation_id: crypto.randomUUID(),
			},
			callback: function (r) {
				if (r.message && r.message.ok) {
					refresh();
				} else {
					button.attr("disabled", false).html(original);
					frappe.msgprint(__("取消失败：") + (r.message && r.message.error ? r.message.error.message : ""));
				}
			},
			error: function (xhr) {
				button.attr("disabled", false).html(original);
				const message = xhr && xhr.responseJSON && xhr.responseJSON.error ? xhr.responseJSON.error.message : __("取消失败");
				frappe.msgprint(message);
			},
		});
	}

	// New Run 创建成功后跳转到此页面；列表最新在前即可看到新 run
	refresh();
};
