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
	const GOVERNANCE_ACTION_COPY = {
		CREATE_MR_DRAFT: __("创建 Material Request 草稿"),
		CREATE_PO_DRAFT: __("创建 Purchase Order 草稿"),
	};
	const GOVERNANCE_STATUS_COPY = {
		DRAFT: __("待评估"),
		AWAITING_APPROVAL: __("等待审批"),
		APPROVED: __("已批准，待执行"),
		DECLINED: __("审批拒绝"),
		CHANGES_REQUESTED: __("已请求修改"),
		EXECUTED: __("已执行并读回"),
		POLICY_REJECTED: __("策略拒绝"),
		EXPIRED: __("已过期"),
		RECONCILIATION_REQUIRED: __("需要只读对账"),
		RECONCILED_SUCCESS: __("对账成功"),
		RECONCILED_FAILURE: __("对账失败"),
		MANUAL_INTERVENTION: __("需要人工处理"),
		SUCCEEDED: __("执行成功"),
		FAILED: __("执行失败"),
	};
	const EXECUTION_MODE_COPY = {
		DETERMINISTIC: __("确定性分析"),
		AGENT: __("Agent 动态分析"),
		PLAN_EXECUTE: __("持久工作流（只读）"),
	};
	const WORKFLOW_STATUS_COPY = {
		READY: __("待开始"),
		RUNNING: __("执行中"),
		INTERRUPTED: __("等待澄清"),
		SUCCEEDED: __("工作流完成"),
		FAILED: __("工作流失败"),
		CANCELLED: __("工作流已取消"),
		EXPIRED: __("工作流已过期"),
	};
	const WORKFLOW_STEP_STATUS_COPY = {
		PENDING: __("待执行"),
		READY: __("就绪"),
		RUNNING: __("执行中"),
		WAITING: __("等待输入"),
		SUCCEEDED: __("已完成"),
		FAILED: __("失败"),
		SKIPPED: __("已跳过"),
		CANCELLED: __("已取消"),
	};
	const WORKFLOW_STEP_TYPE_COPY = {
		TOOL: __("只读工具"),
		CLARIFICATION: __("澄清"),
		FINALIZE: __("收口"),
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

	function workflow_error_copy(code) {
		const copies = {
			UNAVAILABLE: __("Runtime 当前不可用，工作流状态未被伪造成完成。"),
			CHECKPOINT_INCOMPATIBLE: __("工作流 checkpoint 版本不兼容，需要人工检查。"),
			CONFLICT: __("工作流版本已变化，请刷新后使用最新状态。"),
			PERMISSION_DENIED: __("你没有查看或恢复该工作流的权限。"),
			RUN_REJECTED: __("该运行不可用，可能已被删除或过期。"),
		};
		return copies[code] || __("工作流状态读取失败，请稍后重试。 ");
	}

	function api_error_payload(source) {
		const body = source && source.responseJSON ? source.responseJSON : source || {};
		const wrapped = body && body.message && typeof body.message === "object" ? body.message : body;
		const error = wrapped && wrapped.error && typeof wrapped.error === "object" ? wrapped.error : {};
		return {
			code: typeof error.code === "string" ? error.code : "",
			correlation_id: typeof wrapped.correlation_id === "string" ? wrapped.correlation_id : "",
		};
	}

	function api_failure_copy(prefix, source, fallback, correlation_id) {
		const parsed = api_error_payload(source);
		const code = parsed.code || "UNAVAILABLE";
		const correlation = parsed.correlation_id || correlation_id || "";
		let message = prefix + "（" + code + "）：" + (workflow_error_copy(code) || fallback);
		if (correlation) {
			message += " " + __("关联标识") + ": " + correlation;
		}
		return message;
	}

	function workflow_time(value) {
		if (!value) {
			return "—";
		}
		return String(value).replace("T", " ").slice(0, 19);
	}

	function build_workflow_panel(run) {
		if (run.execution_mode !== "PLAN_EXECUTE") {
			return "";
		}
		const panel_id = "workflow-panel-" + String(run.run_id).replace(/[^a-zA-Z0-9_-]/g, "");
		return (
			'<section class="workflow-panel mt-3" aria-labelledby="' + panel_id + '-label" data-workflow-run="' + esc(run.run_id) + '">' +
			'<h5 id="' + panel_id + '-label">' + __("工作流计划（只读）") + "</h5>" +
			'<div class="workflow-content" aria-live="polite"><div class="text-muted py-2"><span class="spinner-border spinner-border-sm"></span> ' + __("加载工作流状态…") + "</div></div>" +
			'</section>'
		);
	}

	function render_workflow_content(wrapper, run_id, workflow) {
		if (!workflow || typeof workflow !== "object") {
			wrapper.html('<div class="text-muted py-2" role="status">' + __("暂无工作流 checkpoint。") + "</div>");
			return;
		}
		const status = workflow.status || "READY";
		const steps = Array.isArray(workflow.steps) ? workflow.steps : [];
		const status_copy = WORKFLOW_STATUS_COPY[status] || status;
		let html =
			'<div class="small mb-2" role="status"><b>' + __("工作流状态") + ":</b> " + esc(status_copy) +
			" · <b>" + __("版本") + ":</b> " + esc(workflow.plan_version) +
			" · <b>" + __("Revision") + ":</b> " + esc(workflow.revision) +
			" · <b>" + __("图版本") + ":</b> " + esc(workflow.graph_version) + "</div>";
		html += '<div class="small text-muted mb-2">' + __("工作流到期") + ": " + esc(workflow_time(workflow.deadline)) +
			(workflow.trace_id ? " · Trace: " + esc(workflow.trace_id) : "") +
			(workflow.crash_recovered ? " · " + __("已从崩溃安全点恢复") : "") +
			(workflow.replan_reason ? " · " + __("重规划原因") + ": " + esc(workflow.replan_reason) : "") + "</div>";
		if (workflow.stop_reason) {
			html += '<div class="small mb-2 text-muted"><b>' + __("停止原因") + ":</b> " + esc(workflow.stop_reason) + "</div>";
		}
		if (!steps.length) {
			html += '<div class="text-muted py-2">' + __("暂无步骤。") + "</div>";
		} else {
			html += '<div class="table-responsive"><table class="table table-sm table-striped" aria-label="' + esc(__("工作流步骤")) + '"><thead><tr>' +
				"<th scope=\"col\">" + __("顺序") + "</th>" +
				"<th scope=\"col\">" + __("步骤") + "</th>" +
				"<th scope=\"col\">" + __("类型") + "</th>" +
				"<th scope=\"col\">" + __("依赖") + "</th>" +
				"<th scope=\"col\">" + __("状态") + "</th>" +
				"<th scope=\"col\">" + __("观察摘要 / 完成时间") + "</th>" +
				"</tr></thead><tbody>";
			steps.forEach(function (step) {
				const dependencies = Array.isArray(step.depends_on) ? step.depends_on.join(", ") : "—";
				const digest = step.observation_digest ? __("digest") + ": " + step.observation_digest : "—";
				const error = step.error ? '<br><span class="text-danger">' + esc(step.error) + "</span>" : "";
				html += "<tr>" +
					"<td>" + esc(step.order) + "</td>" +
					"<td><code>" + esc(step.step_id) + "</code>" + (step.tool_name ? "<br><span class=\"small text-muted\">" + esc(step.tool_name) + "</span>" : "") + "</td>" +
					"<td>" + esc(WORKFLOW_STEP_TYPE_COPY[step.type] || step.type) + "</td>" +
					"<td class=\"small\">" + esc(dependencies) + "</td>" +
					"<td>" + esc(WORKFLOW_STEP_STATUS_COPY[step.status] || step.status) + "</td>" +
					"<td class=\"small text-muted\">" + esc(digest) + "<br>" + esc(workflow_time(step.completed_at)) + error + "</td>" +
					"</tr>";
			});
			html += "</tbody></table></div>";
		}
		if (Array.isArray(workflow.observations) && workflow.observations.length) {
			html += '<div class="small text-muted mb-2"><b>' + __("观察摘要") + ":</b> " + workflow.observations.map(esc).join(" · ") + "</div>";
		}
		const clarification = workflow.clarification;
		if (status === "INTERRUPTED" && clarification) {
			const answer_id = "workflow-answer-" + String(run_id).replace(/[^a-zA-Z0-9_-]/g, "");
			html += '<div class="border rounded p-3 mt-2" data-clarification="1">' +
				'<div class="mb-2"><b>' + __("需要你的澄清") + ":</b> " + esc(clarification.question) + "</div>" +
				'<label class="sr-only" for="' + answer_id + '">' + __("澄清答案") + "</label>" +
				'<input id="' + answer_id + '" class="form-control form-control-sm workflow-answer" maxlength="' + esc(clarification.answer_max_length || 500) + '" aria-describedby="' + answer_id + '-help" />' +
				'<div id="' + answer_id + '-help" class="small text-muted mt-1">' + __("答案只用于本次只读工作流，旧 revision 不能重复消费。") + "</div>" +
				'<button type="button" class="btn btn-primary btn-sm mt-2 workflow-resume" data-run="' + esc(run_id) + '">' + __("提交并恢复") + "</button>" +
				'<div class="workflow-resume-status small mt-2" aria-live="polite"></div></div>';
		}
		wrapper.html(html);
		wrapper.find(".workflow-resume").on("click", function () {
			const button = $(this);
			const answer = wrapper.find(".workflow-answer").val() || "";
			const status_area = wrapper.find(".workflow-resume-status");
			if (!String(answer).trim()) {
				status_area.addClass("text-danger").text(__("请输入澄清答案。"));
				wrapper.find(".workflow-answer").trigger("focus");
				return;
			}
			button.attr("disabled", true);
			status_area.removeClass("text-danger").text(__("正在恢复工作流…"));
			frappe.call({
				method: "synora_agentic_erp.api.resume_run",
				args: {
					run_id: run_id,
					correlation_id: crypto.randomUUID(),
					workflow_revision: workflow.revision,
					interrupt_id: clarification.interrupt_id,
					answer: String(answer),
				},
				callback: function (r) {
					if (r.message && r.message.ok) {
						const next = r.message.analysis && r.message.analysis.workflow;
						if (next) {
							render_workflow_content(wrapper, run_id, next);
						} else {
							load_workflow(run_id, wrapper);
						}
						refresh();
						return;
					}
					button.attr("disabled", false);
					status_area.addClass("text-danger").text(__("恢复失败，请刷新后重试。"));
				},
				error: function (xhr) {
					button.attr("disabled", false);
					const error = xhr && xhr.responseJSON && xhr.responseJSON.error;
					status_area.addClass("text-danger").text(workflow_error_copy(error && error.code));
				},
			});
		});
	}

	function load_workflow(run_id, wrapper) {
		wrapper.html('<div class="text-muted py-2"><span class="spinner-border spinner-border-sm"></span> ' + __("加载工作流状态…") + "</div>");
		frappe.call({
			method: "synora_agentic_erp.api.get_run_workflow",
			args: { run_id: run_id },
			type: "GET",
			callback: function (r) {
				if (!r.message || !r.message.ok) {
					const error = r.message && r.message.error;
					wrapper.html('<div class="text-danger py-2" role="status">' + esc(workflow_error_copy(error && error.code)) + "</div>");
					return;
				}
				render_workflow_content(wrapper, run_id, r.message.workflow);
			},
			error: function (xhr) {
				const error = xhr && xhr.responseJSON && xhr.responseJSON.error;
				wrapper.html('<div class="text-danger py-2" role="status">' + esc(workflow_error_copy(error && error.code)) + "</div>");
			},
		});
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

	function governance_time(value) {
		return value ? esc(String(value).replace("T", " ").slice(0, 19)) : esc("—");
	}

	function governance_refs(refs) {
		if (!Array.isArray(refs) || !refs.length) {
			return esc("—");
		}
		const visible = refs.slice(0, 20).map(function (ref) { return esc(ref); }).join(" · ");
		return visible + (refs.length > 20 ? " · …" : "");
	}

	function governance_action_copy(action) {
		return GOVERNANCE_ACTION_COPY[action.action_type] || esc(action.action_type || __("未知动作"));
	}

	function governance_status_copy(value) {
		return GOVERNANCE_STATUS_COPY[value] || esc(value || __("未知"));
	}

	function governance_items(payload, calculation) {
		const items = payload && Array.isArray(payload.items) ? payload.items : [];
		if (!items.length) {
			return '<div class="text-muted small">' + __("没有可展示的物料行。") + "</div>";
		}
		const amounts = calculation && Array.isArray(calculation.line_amounts) ? calculation.line_amounts : [];
		const total = calculation && calculation.total_amount ? calculation.total_amount : "—";
		const currency = calculation && calculation.currency ? calculation.currency : (payload.currency || "");
		return '<div class="table-responsive"><table class="table table-sm table-striped" aria-label="' + esc(__("批准物料行")) + '">' +
			"<thead><tr><th scope=\"col\">" + __("物料") + "</th><th scope=\"col\">" + __("数量 / UOM") + "</th><th scope=\"col\">" + __("单价") + "</th><th scope=\"col\">" + __("金额") + "</th><th scope=\"col\">" + __("仓库 / 交期") + "</th></tr></thead><tbody>" +
			items.map(function (item, index) {
				const rate = item.rate === undefined ? "—" : item.rate;
				const amount = amounts[index] === undefined ? "—" : amounts[index];
				return "<tr>" +
					"<td>" + esc(item.item_code) + "</td>" +
					"<td>" + esc(item.qty) + " / " + esc(item.uom || "—") + "</td>" +
					"<td>" + esc(rate) + "</td>" +
					"<td>" + esc(amount) + (currency ? " " + esc(currency) : "") + "</td>" +
					"<td>" + esc(item.warehouse) + " / " + governance_time(item.schedule_date) + "</td>" +
					"</tr>";
			}).join("") + "</tbody><tfoot><tr><th colspan=\"3\" scope=\"row\">" + __("合计") + "</th><td>" + esc(total) + (currency ? " " + esc(currency) : "") + "</td><td></td></tr></tfoot></table></div>";
	}

	function governance_policy_summary(policy) {
		if (!policy) {
			return '<span class="text-muted">' + __("尚无策略决定") + "</span>";
		}
		const checks = policy.checks || {};
		const check_text = ["identity", "scope", "permission", "deterministic", "workflow_policy"]
			.map(function (name) { return name + ": " + (checks[name] || "—"); })
			.join(" · ");
		return esc(policy.outcome || "—") + " · " + esc(check_text) +
			(policy.reason ? "<br>" + esc(policy.reason) : "");
	}

	function governance_receipt_summary(receipt) {
		if (!receipt) {
			return '<span class="text-muted">' + __("尚无执行 Receipt") + "</span>";
		}
		const verified = receipt.verified_fields || {};
		const amount = verified["item_0.amount"];
		return esc(receipt.final_state || "—") + " · " + esc(receipt.response_category || "—") +
			(receipt.target_name ? " · " + esc(receipt.target_doctype || "ERP") + ": " + esc(receipt.target_name) : "") +
			(amount ? " · " + __("首行金额") + ": " + esc(amount) : "") +
			(receipt.failure_category ? "<br><span class=\"text-danger\">" + esc(receipt.failure_category) + "</span>" : "");
	}

	function build_governance_panel(governance, run) {
		const panel_id = "governance-panel-" + String(run.run_id).replace(/[^a-zA-Z0-9_-]/g, "");
		if (!Array.isArray(governance) || !governance.length) {
			return '<section class="governance-panel mt-3" aria-labelledby="' + panel_id + '-label">' +
				'<h5 id="' + panel_id + '-label">' + __("治理动作") + "</h5>" +
				'<div class="text-muted py-2" role="status">' + __("当前运行尚无已保存的治理动作。") + "</div></section>";
		}
		const cards = governance.map(function (entry, index) {
			const action = entry && entry.action ? entry.action : {};
			const payload = action.payload || {};
			const policy = entry.policy;
			const approval = entry.approval;
			const reservation = entry.reservation;
			const receipt = entry.receipt;
			const calculation = action.calculation || null;
			const card_id = panel_id + "-action-" + index;
			const state = action.state || "DRAFT";
			const action_id = String(action.action_id || "");
			const digest = String(action.proposal_digest || "");
			const approval_actor = approval && approval.actor ? String(approval.actor) : "";
			const can_approve = state === "AWAITING_APPROVAL" &&
				(action.approval_class === "INITIATOR_CONFIRMATION"
					? action.initiator === current_user
					: approval_actor === current_user);
			const can_execute = state === "APPROVED" &&
				(action.approval_class === "INITIATOR_CONFIRMATION"
					? action.initiator === current_user
					: approval_actor === current_user);
			const can_reconcile = reservation &&
				reservation.status === "RECONCILIATION_REQUIRED" &&
				String(reservation.executor || "") === current_user;
			const approval_summary = approval
				? esc(approval.decision) + " · " + esc(approval.actor) + " · " + esc(approval.reason || "")
				: '<span class="text-muted">' + __("尚未审批") + "</span>";
			const reservation_summary = reservation
				? esc(reservation.status) + " · " + __("尝试") + ": " + esc(reservation.attempt) + " · " + __("租约到期") + ": " + governance_time(reservation.lease_expires_at)
				: '<span class="text-muted">' + __("尚未执行") + "</span>";
			const approval_buttons = can_approve
				? '<div class="btn-group btn-group-sm mr-2" role="group" aria-label="' + esc(__("审批操作")) + '">' +
				  '<button type="button" class="btn btn-success governance-decide" data-action="' + esc(action_id) + '" data-decision="ALLOW" data-digest="' + esc(digest) + '" data-run="' + esc(run.run_id) + '" aria-describedby="' + card_id + '-consequence">' + __("确认执行") + "</button>" +
				  '<button type="button" class="btn btn-outline-danger governance-decide" data-action="' + esc(action_id) + '" data-decision="DECLINE" data-digest="' + esc(digest) + '" data-run="' + esc(run.run_id) + '" aria-describedby="' + card_id + '-consequence">' + __("拒绝") + "</button>" +
				  '<button type="button" class="btn btn-outline-secondary governance-decide" data-action="' + esc(action_id) + '" data-decision="CHANGES_REQUESTED" data-digest="' + esc(digest) + '" data-run="' + esc(run.run_id) + '" aria-describedby="' + card_id + '-consequence">' + __("请求修改") + "</button></div>"
				: "";
			const execute_button = can_execute
				? '<button type="button" class="btn btn-primary btn-sm governance-execute" data-action="' + esc(action_id) + '" data-digest="' + esc(digest) + '" data-key="' + esc(action.idempotency_key || "") + '" data-type="' + esc(action.action_type || "") + '" data-run="' + esc(run.run_id) + '" aria-describedby="' + card_id + '-consequence">' + __("创建 ERP 草稿") + "</button>"
				: "";
			const reconcile_button = can_reconcile
				? '<button type="button" class="btn btn-warning btn-sm governance-reconcile" data-action="' + esc(action_id) + '" data-digest="' + esc(digest) + '" data-key="' + esc(action.idempotency_key || "") + '" data-type="' + esc(action.action_type || "") + '" data-run="' + esc(run.run_id) + '" aria-describedby="' + card_id + '-consequence">' + __("只读对账") + "</button>"
				: "";
			const buttons = approval_buttons + execute_button + reconcile_button;
			return '<article class="border rounded p-3 mb-3" aria-labelledby="' + card_id + '-label">' +
				'<div class="d-flex justify-content-between align-items-start flex-wrap"><h6 id="' + card_id + '-label">' + governance_action_copy(action) + "</h6>" +
				'<span class="badge badge-light">' + governance_status_copy(state) + "</span></div>" +
				'<div class="small text-muted mb-2">' +
					__("Action") + ": <code>" + esc(action_id.slice(0, 12)) + "…</code> · " +
					__("风险") + ": " + esc(action.risk_class || "—") + " · " +
					__("审批类型") + ": " + esc(action.approval_class || "—") + "</div>" +
				'<div class="small mb-2"><b>' + __("批准提议") + "</b> · " +
					(payload.supplier ? __("供应商") + ": " + esc(payload.supplier) + " · " : "") +
					(payload.currency ? __("币种") + ": " + esc(payload.currency) + " · " : "") +
					(payload.buying_price_list ? __("采购价目表") + ": " + esc(payload.buying_price_list) + " · " : "") +
					(payload.company ? __("公司") + ": " + esc(payload.company) : "") +
					"<br>" + __("交易日") + ": " + governance_time(payload.transaction_date) +
					" · " + __("交期") + ": " + governance_time(payload.schedule_date) +
					"<br>" + governance_items(payload, calculation) + "</div>" +
				'<div class="small text-muted mb-2"><b>' + __("证据与计算") + "</b> · digest: <code>" + esc(digest.slice(0, 16)) + "…</code> · snapshot: " + esc(action.snapshot_ref || "—") + " · expiry: " + governance_time(action.expires_at) +
					"<br>" + __("来源") + ": " + governance_refs(action.evidence_refs) + " · " + __("计算") + ": " + governance_refs(action.calculation_refs) +
					(calculation && calculation.basis ? " · " + __("金额依据") + ": " + esc(calculation.basis) : "") + "</div>" +
				'<div class="small mb-2"><b>' + __("策略") + "</b> · " + governance_policy_summary(policy) + "</div>" +
				'<div class="small mb-2"><b>' + __("审批") + "</b> · " + approval_summary + "</div>" +
				'<div class="small mb-2"><b>' + __("执行 Reservation") + "</b> · " + reservation_summary + "</div>" +
				'<div class="small mb-2"><b>' + __("Receipt") + "</b> · " + governance_receipt_summary(receipt) + "</div>" +
				'<div id="' + card_id + '-consequence" class="small text-muted mb-2" aria-live="polite">' +
					(state === "AWAITING_APPROVAL" ? __("确认会消耗当前批准并允许创建一张 Draft；拒绝或请求修改不会创建 ERP 单据。") : "") +
					(state === "APPROVED" ? __("执行只会创建 Draft，成功必须经过 ERP 读回；失败或不确定不会自动重试。") : "") +
					(reservation && reservation.status === "RECONCILIATION_REQUIRED" ? __("对账只读取 ERP，不会再次创建或提交 Purchase Order。") : "") +
				"</div>" +
				'<div class="governance-actions">' + buttons + '</div></article>';
		}).join("");
		return '<section class="governance-panel mt-3" aria-labelledby="' + panel_id + '-label" data-governance-run="' + esc(run.run_id) + '">' +
			'<h5 id="' + panel_id + '-label">' + __("治理动作与执行证据") + "</h5>" +
			'<div class="small text-muted mb-2" role="status" aria-live="polite">' + __("以下状态来自服务器已保存的 Action、Policy、Approval、Reservation 和 Receipt；界面不会伪造成功。") + "</div>" +
			cards + "</section>";
	}

	function governance_call(button, method, args, busy_copy, run_id, dialog) {
		const original = button.html();
		const status_area = button.closest("article").find("[aria-live]").last();
		button.attr("disabled", true).html('<span class="spinner-border spinner-border-sm"></span> ' + esc(busy_copy));
		status_area.removeClass("text-danger").text(busy_copy + "…");
		frappe.call({
			method: method,
			args: args,
			callback: function (r) {
				if (r.message && r.message.ok) {
					dialog.hide();
					refresh();
					show_detail(run_id);
					return;
				}
				button.attr("disabled", false).html(original).trigger("focus");
				status_area.addClass("text-danger").text(api_failure_copy(busy_copy, r.message, __("请求被拒绝。"), args.correlation_id));
			},
			error: function (xhr) {
				button.attr("disabled", false).html(original).trigger("focus");
				status_area.addClass("text-danger").text(api_failure_copy(busy_copy, xhr, __("请求失败，请刷新后重试。"), args.correlation_id));
			},
		});
	}

	function bind_governance_actions(wrapper, run_id, dialog) {
		wrapper.find(".governance-decide").on("click", function () {
			const button = $(this);
			const decision = String(button.data("decision"));
			governance_call(
				button,
				"synora_agentic_erp.api.decide_action",
				{
					action_id: button.data("action"),
					decision: decision,
					proposal_digest: button.data("digest"),
					reason: decision === "ALLOW" ? __("通过 Runs 详情确认") : decision === "DECLINE" ? __("通过 Runs 详情拒绝") : __("通过 Runs 详情请求修改"),
					correlation_id: crypto.randomUUID(),
				},
				decision === "ALLOW" ? __("确认中") : decision === "DECLINE" ? __("拒绝中") : __("提交修改请求中"),
				run_id,
				dialog
			);
		});
		wrapper.find(".governance-execute").on("click", function () {
			const button = $(this);
			const type = String(button.data("type"));
			governance_call(
				button,
				type === "CREATE_PO_DRAFT" ? "synora_agentic_erp.api.execute_purchase_order" : "synora_agentic_erp.api.execute_material_request",
				{
					action_id: button.data("action"),
					expected_proposal_digest: button.data("digest"),
					idempotency_key: button.data("key"),
					correlation_id: crypto.randomUUID(),
				},
				__("执行中"),
				run_id,
				dialog
			);
		});
		wrapper.find(".governance-reconcile").on("click", function () {
			const button = $(this);
			const type = String(button.data("type"));
			governance_call(
				button,
				type === "CREATE_PO_DRAFT" ? "synora_agentic_erp.api.reconcile_purchase_order" : "synora_agentic_erp.api.reconcile_material_request",
				{
					action_id: button.data("action"),
					expected_proposal_digest: button.data("digest"),
					idempotency_key: button.data("key"),
					correlation_id: crypto.randomUUID(),
				},
				__("对账中"),
				run_id,
				dialog
			);
		});
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
		const correlation_id = crypto.randomUUID();
		button.attr("disabled", true).html('<span class="spinner-border spinner-border-sm"></span> ' + __("分析中…"));
		frappe.call({
			method: "synora_agentic_erp.api.analyze_run",
			args: {
				run_id: run_id,
				correlation_id: correlation_id,
			},
			callback: function (r) {
				if (r.message && r.message.ok) {
					refresh();
					show_detail(run_id);
				} else {
					button.attr("disabled", false).html(original);
					frappe.msgprint(api_failure_copy(__("分析失败"), r.message, __("分析请求被拒绝。"), correlation_id));
				}
			},
			error: function (xhr) {
				button.attr("disabled", false).html(original);
				frappe.msgprint(api_failure_copy(__("分析失败"), xhr, __("分析请求被拒绝。"), correlation_id));
			},
		});
	}

	function start_planning(run_id, button) {
		const original = button.html();
		const correlation_id = crypto.randomUUID();
		button.attr("disabled", true).html('<span class="spinner-border spinner-border-sm"></span> ' + __("生成中…"));
		frappe.call({
			method: "synora_agentic_erp.api.plan_run",
			args: {
				run_id: run_id,
				correlation_id: correlation_id,
			},
			callback: function (r) {
				if (r.message && r.message.ok) {
					refresh();
					show_detail(run_id);
				} else {
					button.attr("disabled", false).html(original);
					frappe.msgprint(api_failure_copy(__("生成计划失败"), r.message, __("生成计划请求被拒绝。"), correlation_id));
				}
			},
			error: function (xhr) {
				button.attr("disabled", false).html(original);
				frappe.msgprint(api_failure_copy(__("生成计划失败"), xhr, __("生成计划请求失败，请刷新后重试。"), correlation_id));
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
				const governance_panel = build_governance_panel(data.governance || [], run);
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
				const workflow_panel = build_workflow_panel(run);
				const content_wrapper = dialog.fields_dict.content.$wrapper;
				content_wrapper.html(
					"<div class=\"mb-2\"><b>" + __("目标") + ":</b> " + esc(run.goal) + "</div>" +
					"<div class=\"mb-2\"><b>" + __("状态") + ":</b> " + (STATE_COPY[run.run_state] || esc(run.run_state)) +
					" &nbsp; <b>" + __("模式") + ":</b> " +
					(EXECUTION_MODE_COPY[run.execution_mode] || esc(run.execution_mode)) +
					" &nbsp; <b>" + __("范围") + ":</b> " + scope +
					" &nbsp; <b>" + __("时间窗口") + ":</b> " + esc(run.time_window_days) + " " + __("天") +
					(run.workflow_expires_at ? " &nbsp; <b>" + __("工作流到期") + ":</b> " + esc(workflow_time(run.workflow_expires_at)) : "") +
					"</div>" +
					rows_html +
					governance_panel +
					trace_panel +
					workflow_panel
				);
				if (workflow_panel) {
					load_workflow(run.run_id, content_wrapper.find(".workflow-content"));
				}
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
				bind_governance_actions(content_wrapper, run.run_id, dialog);
				dialog.show();
			},
		});
	}

	function cancel_run(run_id, button) {
		const original = button.html();
		const correlation_id = crypto.randomUUID();
		button.attr("disabled", true).html('<span class="spinner-border spinner-border-sm"></span>');
		frappe.call({
			method: "synora_agentic_erp.api.cancel_run",
			args: {
				run_id: run_id,
				correlation_id: correlation_id,
			},
			callback: function (r) {
				if (r.message && r.message.ok) {
					refresh();
				} else {
					button.attr("disabled", false).html(original);
					frappe.msgprint(api_failure_copy(__("取消失败"), r.message, __("取消请求被拒绝。"), correlation_id));
				}
			},
			error: function (xhr) {
				button.attr("disabled", false).html(original);
				frappe.msgprint(api_failure_copy(__("取消失败"), xhr, __("取消请求失败，请刷新后重试。"), correlation_id));
			},
		});
	}

	// New Run 创建成功后跳转到此页面；列表最新在前即可看到新 run
	refresh();
};
