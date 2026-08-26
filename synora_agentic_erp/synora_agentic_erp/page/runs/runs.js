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
	const EXECUTION_MODE_COPY = {
		DETERMINISTIC: __("确定性分析"),
		AGENT: __("Agent 动态分析"),
	};
	const AGENT_STATUS_COPY = {
		NOT_STARTED: __("未开始"),
		SUCCEEDED: __("探索完成"),
		FALLBACK: __("已回退到确定性分析"),
		FAILED: __("探索失败"),
		UNAVAILABLE: __("Trace 不可用"),
	};
	const TRACE_STOP_COPY = {
		FINAL_ANSWER: __("Agent 完成探索"),
		MODEL_ERROR: __("模型错误"),
		REPEATED_CALL: __("重复调用"),
		NO_PROGRESS: __("观察结果无进展"),
		TOKEN_BUDGET: __("Token 预算已到"),
		COST_BUDGET: __("成本预算已到"),
		WALL_TIME_BUDGET: __("时间预算已到"),
		MAX_STEPS: __("步骤预算已到"),
		TOOL_NOT_ALLOWED: __("工具不在允许范围"),
		TOOL_FREQUENCY: __("工具调用频率超限"),
		INVALID_TOOL_ARGS: __("工具参数无效"),
		UNSUPPORTED_FINAL_ANSWER: __("最终答案缺少证据"),
		TOOL_ERROR: __("只读工具失败"),
		CANCELLED: __("运行已取消"),
		TRACE_INVALID: __("Trace 数据不可用"),
	};
	const TRACE_EVENT_COPY = {
		"run.started": __("运行开始"),
		"model.requested": __("请求模型"),
		"action.proposed": __("模型提出动作"),
		"action.validated": __("动作通过校验"),
		"action.rejected": __("动作被拒绝"),
		"tool.started": __("调用只读工具"),
		"tool.observed": __("收到观察结果"),
		"tool.failed": __("只读工具失败"),
		"guard.checked": __("守卫检查"),
		"final.proposed": __("提出最终答案"),
		"final.validated": __("最终答案通过校验"),
		"final.rejected": __("最终答案被拒绝"),
		"run.stopped": __("运行停止"),
	};
	const TRACE_SENSITIVE_KEY = /(?:secret|password|passwd|token|capability|api[_-]?key|authorization|cookie|prompt)/i;
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

	function safe_payload_text(value) {
		if (value === null || value === undefined) {
			return "";
		}
		if (typeof value === "string") {
			return value;
		}
		try {
			return JSON.stringify(value);
		} catch (_error) {
			return "[unavailable]";
		}
	}

	function render_trace_payload(payload) {
		if (!payload || typeof payload !== "object") {
			return '<span class="text-muted">' + __("无附加信息") + "</span>";
		}
		const fields = Object.keys(payload)
			.filter(function (key) {
				return !TRACE_SENSITIVE_KEY.test(key);
			})
			.map(function (key) {
				return esc(key) + ": " + esc(safe_payload_text(payload[key]));
			})
			.filter(Boolean);
		return fields.length
			? fields.join(" · ")
			: '<span class="text-muted">' + __("无附加信息") + "</span>";
	}

	function render_trace_events(events) {
		if (!events || !events.length) {
			return '<div class="text-muted py-2">' + __("暂无 Trace 事件。") + "</div>";
		}
		return events
			.map(function (event) {
				const event_type = typeof event.event_type === "string" ? event.event_type : "unknown";
				const label = TRACE_EVENT_COPY[event_type] || esc(event_type);
				return (
					'<article class="border rounded p-2 mb-2" style="overflow-wrap:anywhere;">' +
					'<div><b>' +
					label +
					"</b> <span class=\"text-muted small\">#" +
					esc(event.sequence) +
					" · " +
					esc(event.timestamp) +
					"</span></div>" +
					'<div class="small text-muted mt-1">' +
					render_trace_payload(event.payload) +
					"</div></article>"
				);
			})
			.join("");
	}

	function render_trace_content(wrapper, trace) {
		if (!trace) {
			wrapper.html('<div class="text-muted py-2">' + __("暂无 Agent Trace。") + "</div>");
			return;
		}
		const stop_reason = trace.stop_reason || {};
		const stop_code = stop_reason.code || "TRACE_INVALID";
		const usage = trace.usage || {};
		const summary =
			'<div class="small mb-2" role="status">' +
			"<b>" +
			__("停止原因") +
			":</b> " +
			esc(TRACE_STOP_COPY[stop_code] || stop_code) +
			(stop_reason.detail ? " — " + esc(stop_reason.detail) : "") +
			"</div>" +
			'<div class="small text-muted mb-2">' +
			__("Provider") +
			": " +
			esc(trace.provider) +
			" · " +
			__("Model") +
			": " +
			esc(trace.model || __("未配置")) +
			" · " +
			__("步骤") +
			": " +
			esc(trace.events_count) +
			" · " +
			__("Token") +
			": " +
			esc(usage.prompt_tokens || 0) +
			" / " +
			esc(usage.completion_tokens || 0) +
			" / " +
			esc(usage.reasoning_tokens || 0) +
			" · " +
			__("成本") +
			": " +
			esc(usage.cost_microusd || 0) +
			" micro-USD · " +
			esc(trace.elapsed_ms || 0) +
			"ms</div>";
		wrapper.html(summary + render_trace_events(trace.events || []));
	}

	function load_trace(run_id, wrapper, button) {
		wrapper.html('<div class="text-muted py-2">' + __("加载 Trace…") + "</div>");
		button.attr("disabled", true);
		frappe.call({
			method: "synora_agentic_erp.api.get_run_trace",
			args: { run_id: run_id, limit: 200, offset: 0 },
			type: "GET",
			callback: function (r) {
				button.attr("disabled", false).data("loaded", true);
				if (!r.message || !r.message.ok) {
					wrapper.html('<div class="text-danger py-2" role="status">' + __("Trace 读取失败，请稍后重试。") + "</div>");
					return;
				}
				render_trace_content(wrapper, r.message.trace);
			},
			error: function () {
				button.attr("disabled", false);
				wrapper.html('<div class="text-danger py-2" role="status">' + __("Trace 读取失败，请稍后重试。") + "</div>");
			},
		});
	}

	function build_trace_panel(run) {
		if (run.execution_mode !== "AGENT") {
			return "";
		}
		const trace_id = "agent-trace-" + String(run.run_id).replace(/[^a-zA-Z0-9_-]/g, "");
		const status = run.agent_status || "NOT_STARTED";
		const status_copy = AGENT_STATUS_COPY[status] || status;
		return (
			'<section class="agent-trace mt-3" aria-labelledby="' +
			trace_id +
			'-label">' +
			'<h5 id="' +
			trace_id +
			'-label">' +
			__("Agent Trace") +
			"</h5>" +
			'<div class="small text-muted mb-2" role="status">' +
			__("Agent 结果") +
			": " +
			esc(status_copy) +
			"</div>" +
			'<button type="button" class="btn btn-light btn-sm trace-toggle" aria-expanded="false" aria-controls="' +
			trace_id +
			'-content" data-run="' +
			esc(run.run_id) +
			'">' +
			__("展开 Agent Trace") +
			"</button>" +
			'<div id="' +
			trace_id +
			'-content" class="mt-2" hidden></div></section>'
		);
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
				const mode = run.execution_mode || "DETERMINISTIC";
				const agent_status = run.agent_status || "NOT_STARTED";
				const mine = run.initiator === current_user;
				const cancellable =
					(run.run_state === "CREATED" || run.run_state === "ANALYZING") && mine;
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
				const detail_btn =
					'<button class="btn btn-light btn-xs show-detail" data-run="' +
					esc(run.run_id) +
					'">' +
					__("详情") +
					"</button>";
				const scope =
					run.company_scope +
					(run.warehouse_scope ? " / " + run.warehouse_scope : __(" / 全部仓库"));
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
					"<td>" +
					( EXECUTION_MODE_COPY[mode] || esc(mode)) +
					"</td>" +
					"<td class=\"small\">" +
					(mode === "AGENT"
						? AGENT_STATUS_COPY[agent_status] || esc(agent_status)
						: esc("—")) +
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
			'<table class="table table-hover table-sm" aria-describedby="runs-table-caption">' +
				'<caption id="runs-table-caption" class="sr-only">' + __("运行历史列表") + "</caption>" +
				"<thead><tr>" +
				"<th scope=\"col\">" +
				__("Run") +
				"</th>" +
				"<th scope=\"col\">" +
				__("目标") +
				"</th>" +
				"<th scope=\"col\">" +
				__("状态") +
				"</th>" +
				"<th scope=\"col\">" +
				__("分析模式") +
				"</th>" +
				"<th scope=\"col\">" +
				__("Agent 结果") +
				"</th>" +
				"<th scope=\"col\">" +
				__("范围") +
				"</th>" +
				"<th scope=\"col\">" +
				__("创建时间") +
				"</th>" +
				"<th scope=\"col\"></th>" +
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
					// 可解释计划: 模型增强解释 (若通过校验) + 确定性摘要 + 逐项建议 + 来源 + 证据
					const enhanced = plan.enhanced_text && plan.enhanced_text !== plan.summary
						? '<div class="mb-2"><b>' + __("智能解释") + ":</b> " + esc(plan.enhanced_text) + "</div>"
						: "";
					rows_html = '<div class="mb-2"><b>' + __("计划摘要") + ":</b> " + esc(plan.summary) + "</div>" + enhanced;
					rows_html += "<table class=\"table table-sm table-striped\"><caption class=\"sr-only\">" + __("计划明细") + "</caption><thead><tr>" +
						"<th scope=\"col\">" + __("物料") + "</th>" +
						"<th scope=\"col\">" + __("风险") + "</th>" +
						"<th scope=\"col\">" + __("建议") + "</th>" +
						"<th scope=\"col\">" + __("来源") + "</th>" +
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
					const ev = plan.evidence || {};
					const ev_bits = [];
					if (ev.provider) { ev_bits.push(__("Provider") + ": " + esc(ev.provider)); }
					if (typeof ev.prompt_tokens === "number") { ev_bits.push("in:" + ev.prompt_tokens + " out:" + ev.completion_tokens + " reasoning:" + (ev.reasoning_tokens || 0)); }
					if (typeof ev.elapsed_ms === "number") { ev_bits.push(ev.elapsed_ms + "ms"); }
					if (ev.fallback_reason) { ev_bits.push('<span class="text-danger">' + __("已回退") + ": " + esc(ev.fallback_reason) + "</span>"); }
					if (ev_bits.length) {
						rows_html += '<div class="small text-muted mt-1">' + ev_bits.join(" · ") + "</div>";
					}
				} else if (analyses.length) {
					rows_html =
						"<table class=\"table table-sm table-striped\"><caption class=\"sr-only\">" + __("分析明细") + "</caption><thead><tr>" +
						"<th scope=\"col\">" + __("物料") + "</th>" +
						"<th scope=\"col\">" + __("风险") + "</th>" +
						"<th scope=\"col\">" + __("库存") + "</th>" +
						"<th scope=\"col\">" + __("窗口需求") + "</th>" +
						"<th scope=\"col\">" + __("在途") + "</th>" +
						"<th scope=\"col\">" + __("净位置") + "</th>" +
						"<th scope=\"col\">" + __("缺货量") + "</th>" +
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
				const trace_panel = build_trace_panel(run);
				const content_wrapper = dialog.fields_dict.content.$wrapper;
				content_wrapper.html(
					"<div class=\"mb-2\"><b>" + __("目标") + ":</b> " + esc(run.goal) + "</div>" +
					"<div class=\"mb-2\"><b>" + __("状态") + ":</b> " + (STATE_COPY[run.run_state] || esc(run.run_state)) +
					" &nbsp; <b>" + __("模式") + ":</b> " +
					(EXECUTION_MODE_COPY[run.execution_mode] || esc(run.execution_mode)) +
					" &nbsp; <b>" + __("范围") + ":</b> " + scope +
					" &nbsp; <b>" + __("时间窗口") + ":</b> " + esc(run.time_window_days) + " " + __("天") + "</div>" +
					rows_html +
					trace_panel
				);
				content_wrapper.find(".trace-toggle").on("click", function () {
					const button = $(this);
					const expanded = button.attr("aria-expanded") === "true";
					const trace_content = content_wrapper.find("#" + button.attr("aria-controls"));
					button.attr("aria-expanded", expanded ? "false" : "true");
					button.text(expanded ? __("展开 Agent Trace") : __("收起 Agent Trace"));
					trace_content.prop("hidden", expanded);
					if (!expanded && !button.data("loaded")) {
						load_trace(run.run_id, trace_content, button);
					}
				});
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
