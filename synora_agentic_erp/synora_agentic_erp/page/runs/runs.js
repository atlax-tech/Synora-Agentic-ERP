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
	// P3.3 确定性风险判定文案
	const RISK_COPY = {
		SHORTAGE: __("缺货"),
		ADEQUATE: __("供应充足"),
		DUPLICATE_RISK: __("重复采购风险"),
		NO_DEMAND: __("无需求"),
		NEEDS_INPUT: __("输入不足"),
		UNKNOWN: __("未知"),
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
			const mine = run.initiator === current_user;
			const cancellable = (run.run_state === "CREATED" || run.run_state === "ANALYZING") && mine;
			const analyzable = run.run_state === "CREATED" && mine;
			const plannable = run.run_state === "PROPOSED" && mine;
			const analyze_btn = analyzable
				? '<button class="btn btn-primary btn-xs analyze-run" data-run="' +
				  esc(run.run_id) +
				  '">' +
				  __("开始分析") +
				  "</button> "
				: "";
			const plan_btn = plannable
				? '<button class="btn btn-success btn-xs plan-run" data-run="' +
				  esc(run.run_id) +
				  '">' +
				  __("生成计划") +
				  "</button> "
				: "";
			const cancel_btn = cancellable
				? '<button class="btn btn-secondary btn-xs cancel-run" data-run="' +
				  esc(run.run_id) +
				  '">' +
				  __("取消") +
				  "</button>"
				: "";
				const detail_btn = '<button class="btn btn-light btn-xs show-detail" data-run="' + esc(run.run_id) + '">' + __("详情") + "</button>";
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
					analyze_btn +
					plan_btn +
					cancel_btn +
					detail_btn +
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

		container.find(".cancel-run").on("click", function (event) {
			event.stopPropagation();
			const run_id = $(this).data("run");
			cancel_run(run_id, $(this));
		});
		container.find(".analyze-run").on("click", function (event) {
			event.stopPropagation();
			const run_id = $(this).data("run");
			start_analysis(run_id, $(this));
		});
		container.find(".plan-run").on("click", function (event) {
			event.stopPropagation();
			const run_id = $(this).data("run");
			start_planning(run_id, $(this));
		});
		container.find(".show-detail").on("click", function (event) {
			event.stopPropagation();
			const run_id = $(this).data("run");
			show_detail(run_id);
		});
	}

	function start_analysis(run_id, button) {
		const original = button.html();
		button.attr("disabled", true).html('<span class="spinner-border spinner-border-sm"></span> ' + __("分析中…"));
		frappe.call({
			method: "synora_agentic_erp.api.analyze_run",
			args: {
				run_id: run_id,
				correlation_id: crypto.randomUUID(),
			},
			callback: function (r) {
				if (r.message && r.message.ok) {
					refresh();
					show_detail(run_id);
				} else {
					button.attr("disabled", false).html(original);
					frappe.msgprint(__("分析失败：") + (r.message && r.message.error ? r.message.error.message : ""));
				}
			},
			error: function (xhr) {
				button.attr("disabled", false).html(original);
				const message = xhr && xhr.responseJSON && xhr.responseJSON.error ? xhr.responseJSON.error.message : __("分析失败");
				frappe.msgprint(message);
			},
		});
	}

	function start_planning(run_id, button) {
		const original = button.html();
		button.attr("disabled", true).html('<span class="spinner-border spinner-border-sm"></span> ' + __("生成中…"));
		frappe.call({
			method: "synora_agentic_erp.api.plan_run",
			args: {
				run_id: run_id,
				correlation_id: crypto.randomUUID(),
			},
			callback: function (r) {
				if (r.message && r.message.ok) {
					refresh();
					show_detail(run_id);
				} else {
					button.attr("disabled", false).html(original);
					frappe.msgprint(__("生成计划失败：") + (r.message && r.message.error ? r.message.error.message : ""));
				}
			},
			error: function (xhr) {
				button.attr("disabled", false).html(original);
				const message = xhr && xhr.responseJSON && xhr.responseJSON.error ? xhr.responseJSON.error.message : __("生成计划失败");
				frappe.msgprint(message);
			},
		});
	}

	function show_detail(run_id) {
		frappe.call({
			method: "synora_agentic_erp.api.get_run",
			args: { run_id: run_id },
			type: "GET",
			callback: function (r) {
				if (!r.message || !r.message.ok) {
					frappe.msgprint(__("无法读取运行详情。"));
					return;
				}
				const data = r.message;
				const run = data.run;
				const analyses = data.analyses || [];
				const plan = data.plan;
				let rows_html = "";
				if (plan && plan.findings) {
					// 可解释计划: 摘要 + 逐项建议 + 来源
					rows_html = '<div class="mb-2"><b>' + __("计划摘要") + ":</b> " + esc(plan.summary) + "</div>";
					rows_html += "<table class=\"table table-sm table-striped\"><thead><tr>" +
						"<th>" + __("物料") + "</th>" +
						"<th>" + __("风险") + "</th>" +
						"<th>" + __("建议") + "</th>" +
						"<th>" + __("来源") + "</th>" +
						"</tr></thead><tbody>";
					plan.findings.forEach(function (f) {
						const goal_tag = f.matched_goal ? ' <span class="badge badge-primary">' + __("目标提及") + "</span>" : "";
						rows_html +=
							"<tr>" +
							"<td>" + esc(f.item_code) + goal_tag + "</td>" +
							"<td><b>" + (RISK_COPY[f.risk] || esc(f.risk)) + "</b></td>" +
							"<td class=\"small\">" + esc(f.recommendation) + "</td>" +
							"<td class=\"small text-muted\">" + (f.evidence || []).map(esc).join("<br>") + "</td>" +
							"</tr>";
					});
					rows_html += "</tbody></table>";
				} else if (analyses.length) {
					rows_html =
						"<table class=\"table table-sm table-striped\"><thead><tr>" +
						"<th>" + __("物料") + "</th>" +
						"<th>" + __("风险") + "</th>" +
						"<th>" + __("库存") + "</th>" +
						"<th>" + __("窗口需求") + "</th>" +
						"<th>" + __("在途") + "</th>" +
						"<th>" + __("净位置") + "</th>" +
						"<th>" + __("缺货量") + "</th>" +
						"</tr></thead><tbody>";
					analyses.forEach(function (a) {
						const unknown = a.unknowns ? " (" + esc(a.unknowns) + ")" : "";
						rows_html +=
							"<tr>" +
							"<td>" + esc(a.item_code) + "</td>" +
							"<td><b>" + (RISK_COPY[a.risk] || esc(a.risk)) + "</b>" + unknown + "</td>" +
							"<td>" + esc(a.actual_qty) + "</td>" +
							"<td>" + esc(a.demand_qty) + "</td>" +
							"<td>" + esc(a.incoming_qty) + "</td>" +
							"<td>" + esc(a.net_position) + "</td>" +
							"<td>" + esc(a.shortage_qty) + "</td>" +
							"</tr>";
					});
					rows_html += "</tbody></table>";
				} else {
					rows_html = '<div class="text-muted">' + __("尚无分析结果。") + "</div>";
				}
				const dialog = new frappe.ui.Dialog({
					title: __("运行详情") + " — " + esc(run.run_id.slice(0, 8)),
					fields: [
						{ fieldtype: "HTML", fieldname: "content" },
					],
					primary_action_label: __("关闭"),
					primary_action: function () {
						dialog.hide();
					},
				});
				const scope = esc(run.company_scope + (run.warehouse_scope ? " / " + run.warehouse_scope : __(" / 全部仓库")));
				dialog.fields_dict.content.$wrapper.html(
					"<div class=\"mb-2\"><b>" + __("目标") + ":</b> " + esc(run.goal) + "</div>" +
					"<div class=\"mb-2\"><b>" + __("状态") + ":</b> " + (STATE_COPY[run.run_state] || esc(run.run_state)) +
					" &nbsp; <b>" + __("范围") + ":</b> " + scope +
					" &nbsp; <b>" + __("时间窗口") + ":</b> " + esc(run.time_window_days) + " " + __("天") + "</div>" +
					rows_html
				);
				dialog.show();
			},
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
