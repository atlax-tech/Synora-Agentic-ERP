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
	const COACH_STATUS_COPY = {
		ANSWERED: __("已回答"),
		CONFLICT: __("存在冲突，需要人工确认"),
		UNKNOWN: __("无法形成有依据的答案"),
		REFUSED: __("已拒绝回答"),
	};
	const COACH_CLAIM_TYPE_COPY = {
		ERP_FACT: __("ERP 事实"),
		RETRIEVED_KNOWLEDGE: __("检索知识"),
		RECOMMENDATION: __("建议"),
	};
	const GOVERNANCE_ACTION_COPY = {
		CREATE_MR_DRAFT: __("创建 Material Request 草稿"),
		CREATE_PO_DRAFT: __("创建 Purchase Order 草稿"),
		SUBMIT_PO: __("提交 Purchase Order"),
		CANCEL_PO: __("取消 Purchase Order"),
		CREATE_PR_DRAFT: __("创建 Purchase Receipt 草稿"),
		SUBMIT_PR: __("提交 Purchase Receipt"),
		CANCEL_PR: __("取消 Purchase Receipt"),
		CREATE_PI_DRAFT: __("创建 Purchase Invoice 草稿"),
		SUBMIT_PI: __("提交 Purchase Invoice"),
		CANCEL_PI: __("取消 Purchase Invoice"),
		CREATE_PAYMENT_ENTRY_DRAFT: __("创建 Payment Entry 草稿"),
		SUBMIT_PAYMENT_ENTRY: __("提交 Payment Entry"),
		CANCEL_PAYMENT_ENTRY: __("取消 Payment Entry"),
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
		const P2P_CHAIN_STATUS_COPY = {
			PLANNED: __("等待编排"),
			IN_PROGRESS: __("跨单据处理中"),
			WAITING_GOAL_CONFIRMATION: __("等待确认业务目标"),
			WAITING_BUSINESS_FACTS: __("等待业务事实完成"),
			WAITING_APPROVAL: __("等待独立审批"),
		WAITING_DEPENDENCY: __("等待前置单据"),
		EXECUTING: __("正在执行 ERP 动作"),
		BLOCKED: __("前置动作阻塞"),
		RECONCILIATION_REQUIRED: __("需要对账"),
		REINVESTIGATION_REQUIRED: __("需要重新调查"),
		FAILED: __("链路失败"),
		SUCCEEDED: __("链路已完成"),
		CANCELLED: __("编排已取消"),
		EXPIRED: __("编排已过期"),
	};
	const P2P_STEP_STATE_COPY = {
		PLANNED: __("待安排"),
		WAITING_APPROVAL: __("等待审批"),
		WAITING_DEPENDENCY: __("等待前置步骤"),
		READY: __("就绪"),
		EXECUTING: __("执行中"),
		SUCCEEDED: __("已完成"),
		FAILED: __("失败"),
		BLOCKED: __("阻塞"),
		RECONCILIATION_REQUIRED: __("需要对账"),
		REINVESTIGATION_REQUIRED: __("需要重新调查"),
		CANCELLED: __("已取消"),
		EXPIRED: __("已过期"),
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
		CONTEXT_INVALID: __("上下文无效，已安全回退"),
		CONTEXT_BUDGET: __("上下文预算已到"),
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
		"skill.loaded": __("加载版本化 Skill"),
		"context.assembled": __("组装上下文"),
		"context.compressed": __("压缩上下文"),
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
		const context = trace.context || {};
		const context_bits = [];
		if (context.prompt_profile_id) {
			context_bits.push(__("Prompt Profile") + ": " + esc(context.prompt_profile_id));
		}
		if (context.prompt_profile_hash) {
			context_bits.push(__("Profile Hash") + ": " + esc(context.prompt_profile_hash));
		}
		if (context.context_builder_version) {
			context_bits.push(__("ContextBuilder") + ": " + esc(context.context_builder_version));
		}
		if (typeof context.actual_prompt_tokens === "number") {
			context_bits.push(__("实际输入 Token") + ": " + esc(context.actual_prompt_tokens));
		}
		if (typeof context.estimated_input_units_before === "number") {
			context_bits.push(
				__("估算输入") +
				": " +
				esc(context.estimated_input_units_before) +
				" → " +
				esc(context.estimated_input_units_after) +
				" / " +
				esc(context.input_budget)
			);
		}
		if (context.compression_reasons && context.compression_reasons.length) {
			context_bits.push(
				__("压缩原因") + ": " + context.compression_reasons.map(esc).join(", ")
			);
		}
		if (context.dropped_fragment_ids && context.dropped_fragment_ids.length) {
			context_bits.push(
				__("丢弃片段") + ": " + context.dropped_fragment_ids.map(esc).join(", ")
			);
		}
		if (context.skill_refs && context.skill_refs.length) {
			context_bits.push(__("Skills") + ": " + context.skill_refs.map(esc).join(", "));
		}
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
			"ms</div>" +
			(context_bits.length
				? '<div class="small text-muted mb-2">' + context_bits.join(" · ") + "</div>"
				: "");
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

	function governance_financial_summary(calculation) {
		if (!calculation || typeof calculation !== "object") {
			return "";
		}
		const bits = [];
		if (calculation.status) {
			bits.push(__("状态") + ": " + esc(calculation.status));
		}
		if (calculation.tax_amount !== undefined) {
			bits.push(__("税额") + ": " + esc(calculation.tax_amount));
		}
		if (calculation.grand_total !== undefined) {
			bits.push(__("合计") + ": " + esc(calculation.grand_total));
		}
		if (calculation.outstanding_amount !== undefined) {
			bits.push(__("应付余额") + ": " + esc(calculation.outstanding_amount));
		}
		if (calculation.accounting_state) {
			bits.push(__("会计") + ": " + esc(calculation.accounting_state));
		}
		return bits.length
			? '<div class="small text-muted mt-2" aria-live="polite"><b>' +
				  __("财务预览") +
				  "</b> · " +
				  bits.join(" · ") +
				  "</div>"
			: "";
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
			"<thead><tr><th scope=\"col\">" + __("来源行") + "</th><th scope=\"col\">" + __("物料") + "</th><th scope=\"col\">" + __("数量 / UOM") + "</th><th scope=\"col\">" + __("单价") + "</th><th scope=\"col\">" + __("金额") + "</th><th scope=\"col\">" + __("仓库 / 交期") + "</th></tr></thead><tbody>" +
			items.map(function (item, index) {
				const rate = item.rate === undefined ? "—" : item.rate;
				const amount = amounts[index] === undefined ? "—" : amounts[index];
				return "<tr>" +
					"<td><code>" + esc(item.source_row || "—") + "</code></td>" +
					"<td>" + esc(item.item_code) + "</td>" +
					"<td>" + esc(item.qty) + " / " + esc(item.uom || "—") + "</td>" +
					"<td>" + esc(rate) + "</td>" +
					"<td>" + esc(amount) + (currency ? " " + esc(currency) : "") + "</td>" +
					"<td>" + esc(item.warehouse) + " / " + governance_time(item.schedule_date) + "</td>" +
					"</tr>";
			}).join("") + "</tbody><tfoot><tr><th colspan=\"4\" scope=\"row\">" + __("合计") + "</th><td>" + esc(total) + (currency ? " " + esc(currency) : "") + "</td><td></td></tr></tfoot></table></div>" +
			governance_financial_summary(calculation);
	}

	function is_p2p_action(action_type) {
		return action_type && action_type !== "CREATE_MR_DRAFT" && action_type !== "CREATE_PO_DRAFT";
	}

	function governance_execute_copy(action) {
		if (action.action_type === "SUBMIT_PO") {
			return __("提交 ERP Purchase Order");
		}
		if (action.action_type.indexOf("CANCEL_") === 0) {
			return __("取消 ERP 单据");
		}
		return is_p2p_action(action.action_type) ? __("执行 ERP P2P 动作") : __("创建 ERP 草稿");
	}

	function governance_consequence(action, reservation) {
		if (action.action_type === "SUBMIT_PO") {
			return __("执行会提交来源 Purchase Order；成功必须经过 ERP 状态读回，失败或不确定不会自动重试。");
		}
		if (action.action_type.indexOf("CANCEL_") === 0) {
			return __("取消会经过 ERP 原生依赖校验，并留下独立取消回执；不会级联撤销下游单据。");
		}
		if (reservation && reservation.status === "RECONCILIATION_REQUIRED") {
			return __("对账只读取 ERP，不会再次创建、提交或取消业务单据。");
		}
		return is_p2p_action(action.action_type)
			? __("执行会使用已批准的来源和数量；ERP 负责业务校验，结果以回执为准。")
			: __("执行只会创建 Draft，成功必须经过 ERP 读回；失败或不确定不会自动重试。");
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
		const finance = [];
		["status", "total_taxes_and_charges", "grand_total", "outstanding_amount"].forEach(function (key) {
			if (verified[key] !== undefined) {
				const labels = {
					status: __("状态"),
					total_taxes_and_charges: __("税额"),
					grand_total: __("合计"),
					outstanding_amount: __("应付余额"),
				};
				finance.push(labels[key] + ": " + esc(verified[key]));
			}
		});
			return esc(receipt.final_state || "—") + " · " + esc(receipt.response_category || "—") +
				(receipt.target_name ? " · " + esc(receipt.target_doctype || "ERP") + ": " + esc(receipt.target_name) : "") +
				(amount ? " · " + __("首行金额") + ": " + esc(amount) : "") +
				(finance.length ? " · " + finance.join(" · ") : "") +
				(receipt.failure_category ? "<br><span class=\"text-danger\">" + esc(receipt.failure_category) + "</span>" : "") +
				(receipt.reconciliation_evidence && receipt.reconciliation_evidence.recovery_action
					? "<br><span class=\"text-warning\">" + __("恢复建议") + ": " + esc(receipt.reconciliation_evidence.recovery_action) + "</span>"
					: "");
	}

	function p2p_chain_status_copy(state) {
		return P2P_CHAIN_STATUS_COPY[String(state || "")] || esc(state || "—");
	}

	function p2p_step_state_copy(state) {
		return P2P_STEP_STATE_COPY[String(state || "")] || esc(state || "—");
	}

	function build_p2p_chain_panel(chain, run) {
		if (!chain || !Array.isArray(chain.steps)) {
			return "";
		}
		const goal = chain.goal || {};
		const target = goal.target || {};
		const progress = chain.business_progress || {};
		const has_goal = String(goal.state || run.p2p_goal_state || "MISSING") !== "MISSING";
		const is_p2p = run.purpose === "P2P_EXECUTION" || chain.steps.length || has_goal;
		if (!is_p2p) {
			return "";
		}
		const panel_id = "p2p-chain-panel-" + String(run.run_id).replace(/[^a-zA-Z0-9_-]/g, "");
		const terminal = ["SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"].indexOf(String(run.run_state)) >= 0;
		const owner = String(run.initiator || "") === String(current_user || "");
		const candidate = chain.current_candidate || null;
		const recovery = chain.recovery || {};
		const goal_state = String(goal.state || run.p2p_goal_state || "MISSING");
		const goal_ready = goal_state === "CONFIRMED";
		const can_resume = owner && !terminal && goal_ready;
		const can_finalize = owner && !terminal && Boolean(chain.completion_ready);
		const can_cancel = owner && !terminal && !Boolean(chain.completion_ready);
		const can_confirm_goal = owner && !terminal && (!has_goal || ["STALE", "INVALID"].indexOf(goal_state) >= 0);
		const can_investigate = owner && !terminal && goal_ready && !recovery.manual_takeover;
		const target_rows = Array.isArray(target.source_rows) ? target.source_rows : [];
		const goal_summary = target.source_name
			? __("目标") + ": " + esc(target.source_doctype || "Purchase Order") + " / " + esc(target.source_name) +
				" · " + __("目标版本") + ": " + esc(goal.version || 0) +
				" · " + __("目标状态") + ": " + esc(goal.state || "MISSING")
			: __("尚未确认来源 Purchase Order 和目标行；确认前不能收口。");
		const progress_summary = progress.target_qty !== undefined
			? __("收货数量") + ": " + esc(progress.received_qty || "0") + " / " + esc(progress.target_qty || "0") +
				" · " + __("剩余数量") + ": " + esc(progress.remaining_qty || "0") +
				" · " + __("已开票金额") + ": " + esc(progress.billed_amount || "0") + " / " + esc(progress.target_amount || "0") +
				" · " + __("未付余额") + ": " + esc(progress.outstanding_amount || "0")
			: "";
		const target_rows_summary = target_rows.length
			? '<div class="small text-muted mb-2">' + __("目标行") + ": " +
				target_rows.map(function (row) {
					return esc(row.source_row) + " / " + esc(row.item_code) + " · " + __("数量") + ": " + esc(row.target_qty);
				}).join("；") + "</div>"
			: "";
		const candidate_action = candidate && candidate.action ? candidate.action : null;
		const candidate_summary = candidate_action
			? '<div class="small border rounded p-2 mb-2 bg-light"><b>' + __("当前候选") + "</b> · " +
				esc(governance_action_copy(candidate_action)) +
				" · " + __("候选状态") + ": " + esc(candidate.state || "—") +
				" · " + __("目标版本") + ": " + esc(candidate_action.goal_version || goal.version || 0) +
				" · " + __("运行版本") + ": " + esc(candidate_action.run_version || chain.run_version || 0) +
				(candidate.requires_approval ? " · " + __("等待独立审批") : "") +
				(candidate.recovery_required ? " · " + __("需要人工接管") : "") +
				"</div>"
			: "";
		const recovery_summary = recovery.manual_takeover
			? '<div class="small text-warning mb-2"><b>' + __("已暂停自动推进") + "</b> · " +
				__("最近一次 ERP 副作用结果不确定；请在治理动作卡片中先只读对账或人工确认，系统不会自动重试。") +
				"</div>"
			: "";
		const reasons = (chain.blocked_reasons || []).filter(Boolean).map(function (reason) {
			return "<li>" + esc(reason) + "</li>";
		}).join("");
		const rows = chain.steps.map(function (step) {
			const timeline = (chain.timeline || []).find(function (item) {
				return String(item.step_id) === String(step.step_id);
			}) || {};
			const depends = Array.isArray(step.depends_on) && step.depends_on.length
				? step.depends_on.map(function (id) { return esc(String(id).slice(0, 12)) + "…"; }).join(", ")
				: '<span class="text-muted">' + __("无") + "</span>";
			const receipt = timeline.receipt_state || "—";
			return "<tr>" +
				"<td>" + esc(step.order) + "</td>" +
				"<td>" + governance_action_copy({ action_type: timeline.action_type || "" }) +
					(timeline.target_name ? "<br><span class=\"small text-muted\">" + esc(timeline.target_doctype || "ERP") + ": " + esc(timeline.target_name) + "</span>" : "") + "</td>" +
				"<td><span class=\"badge badge-light\">" + p2p_step_state_copy(step.state) + "</span>" +
					(step.blocked_reason ? "<br><span class=\"small text-danger\">" + esc(step.blocked_reason) + "</span>" : "") + "</td>" +
				"<td class=\"small\"><code>" + depends + "</code></td>" +
				"<td class=\"small\">" + esc(receipt) + "</td>" +
				"</tr>";
		}).join("");
		const actions = (can_confirm_goal ? '<button type="button" class="btn btn-outline-primary btn-sm p2p-goal-confirm" data-run="' + esc(run.run_id) + '">' + (has_goal ? __("补充 / 更新业务目标") : __("确认 P2P 业务目标")) + "</button>" : "") +
			(can_investigate ? '<button type="button" class="btn btn-outline-primary btn-sm p2p-run-investigate" data-run="' + esc(run.run_id) + '">' + __("调查 / 生成下一候选") + "</button>" : "") +
			(can_resume ? '<button type="button" class="btn btn-outline-secondary btn-sm p2p-run-resume" data-run="' + esc(run.run_id) + '">' + __("重新读取事实") + "</button>" : "") +
			(can_finalize ? '<button type="button" class="btn btn-primary btn-sm p2p-run-finalize" data-run="' + esc(run.run_id) + '">' + __("确认链路完成") + "</button>" : "") +
			(can_cancel ? '<button type="button" class="btn btn-outline-danger btn-sm p2p-run-cancel" data-run="' + esc(run.run_id) + '">' + __("停止后续调度") + "</button>" : "");
			return '<section class="p2p-chain-panel mt-3" aria-labelledby="' + panel_id + '-label" data-p2p-run="' + esc(run.run_id) + '">' +
				'<h5 id="' + panel_id + '-label">' + __("P2P 跨单据编排") + "</h5>" +
				'<div class="small text-muted mb-2 p2p-chain-status" role="status" aria-live="polite">' +
					__("链路状态") + ": <b>" + p2p_chain_status_copy(chain.status) + "</b> · " +
					__("完成条件") + ": " + (chain.completion_ready ? __("目标范围已收货、已开票、余额为零且每个步骤都有成功 Receipt") : __("目标范围、ERP 当前事实和成功 Receipt 尚未全部满足")) +
					(chain.next_step_id ? " · " + __("下一步") + ": " + esc(chain.next_step_id) : "") + "</div>" +
				'<div class="small mb-2"><b>' + goal_summary + "</b>" + (progress_summary ? "<br>" + progress_summary : "") + "</div>" +
				target_rows_summary +
				candidate_summary +
				recovery_summary +
				'<div class="small mb-2">' + __("每个 ERP 副作用都单独审批、执行和读回；前一步失败或未知时，后续步骤会停在这里。") + "</div>" +
			(reasons ? '<div class="small text-danger mb-2"><b>' + __("待处理原因") + "</b><ul class=\"mb-0\">" + reasons + "</ul></div>" : "") +
			'<div class="table-responsive"><table class="table table-sm table-striped"><caption class="sr-only">' + __("P2P 步骤与回执") + "</caption><thead><tr>" +
				"<th scope=\"col\">" + __("序号") + "</th><th scope=\"col\">" + __("动作 / 单据") + "</th><th scope=\"col\">" + __("状态") + "</th><th scope=\"col\">" + __("前置步骤") + "</th><th scope=\"col\">Receipt</th>" +
				"</tr></thead><tbody>" + rows + "</tbody></table></div>" +
			(actions ? '<div class="p2p-chain-actions btn-group btn-group-sm" role="group" aria-label="' + esc(__('P2P 编排操作')) + '">' + actions + "</div>" : "") +
			(!owner && !terminal ? '<div class="small text-muted mt-2">' + __("只有 Run 发起人可以恢复、完成或停止这条编排；审批仍按每个 Action 的独立权限执行。") + "</div>" : "") +
			"</section>";
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
			const can_execute = state === "APPROVED" && !reservation &&
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
				? '<button type="button" class="btn btn-primary btn-sm governance-execute" data-action="' + esc(action_id) + '" data-digest="' + esc(digest) + '" data-key="' + esc(action.idempotency_key || "") + '" data-type="' + esc(action.action_type || "") + '" data-run="' + esc(run.run_id) + '" aria-describedby="' + card_id + '-consequence">' + governance_execute_copy(action) + "</button>"
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
					(payload.source_doctype && payload.source_name ? "<br>" + __("来源单据") + ": " + esc(payload.source_doctype) + " / " + esc(payload.source_name) : "") +
					(payload.reason ? "<br>" + __("原因") + ": " + esc(payload.reason) : "") +
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
					(state === "AWAITING_APPROVAL" ? __("确认会消耗当前批准并允许执行这一个已绑定来源的 ERP 动作；拒绝或请求修改不会写入 ERP。") : "") +
					(state === "APPROVED" ? governance_consequence(action, reservation) : "") +
					(reservation && reservation.status === "RECONCILIATION_REQUIRED" ? governance_consequence(action, reservation) : "") +
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
					if (dialog) {
						dialog.hide();
					}
					refresh();
					if (run_id) {
						show_detail(run_id);
					}
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

	function p2p_run_call(button, method, busy_copy, run_id, dialog) {
		const original = button.html();
		const panel = button.closest(".p2p-chain-panel");
		const status_area = panel.find("[aria-live]").first();
		const correlation_id = crypto.randomUUID();
		button.attr("disabled", true).html('<span class="spinner-border spinner-border-sm"></span> ' + esc(busy_copy));
		status_area.removeClass("text-danger").text(busy_copy + "…");
		frappe.call({
			method: method,
			args: { run_id: run_id, correlation_id: correlation_id },
			callback: function (r) {
				if (r.message && r.message.ok) {
					if (dialog) {
						dialog.hide();
					}
					refresh();
					show_detail(run_id);
					return;
				}
				button.attr("disabled", false).html(original).trigger("focus");
				status_area.addClass("text-danger").text(api_failure_copy(busy_copy, r.message, __("请求被拒绝。"), correlation_id));
			},
			error: function (xhr) {
				button.attr("disabled", false).html(original).trigger("focus");
				status_area.addClass("text-danger").text(api_failure_copy(busy_copy, xhr, __("请求失败，请刷新后重试。"), correlation_id));
			},
		});
	}

	function open_p2p_goal_dialog(run_id, trigger, detail_dialog) {
		const original = trigger.html();
		trigger.attr("disabled", true).html('<span class="spinner-border spinner-border-sm"></span> ' + esc(__("读取 Purchase Order…")));
		frappe.call({
			method: "synora_agentic_erp.api.get_p2p_goal_options",
			type: "GET",
			args: { run_id: run_id },
			callback: function (r) {
				trigger.attr("disabled", false).html(original);
				if (!r.message || !r.message.ok) {
					frappe.msgprint(api_failure_copy(__("读取 P2P 来源失败"), r.message, __("当前可用 Purchase Order 不可读取。"), ""));
					return;
				}
				const orders = Array.isArray(r.message.options) ? r.message.options : [];
				if (!orders.length) {
					frappe.msgprint(__("当前公司范围内没有可用于 P2P 目标确认的 Purchase Order。"));
					return;
				}
				const order_by_name = {};
				orders.forEach(function (order) {
					order_by_name[String(order.source_name)] = order;
				});
				const dialog = new frappe.ui.Dialog({
					title: __("确认 P2P 业务目标"),
					fields: [
						{
							fieldtype: "Select",
							fieldname: "source_name",
							label: __("来源 Purchase Order"),
							options: orders.map(function (order) { return String(order.source_name); }).join("\n"),
							reqd: 1,
						},
						{
							fieldtype: "Select",
							fieldname: "source_row",
							label: __("来源行"),
							options: "",
							reqd: 1,
						},
						{
							fieldtype: "Float",
							fieldname: "target_qty",
							label: __("目标数量"),
							reqd: 1,
							description: __("只能确认不超过来源行订购数量的目标；已收货数量仍以 ERP 回读为准。"),
						},
						{ fieldtype: "HTML", fieldname: "facts" },
					],
					primary_action_label: __("保存目标并继续调查"),
					primary_action: function () {
						const order = order_by_name[String(dialog.get_value("source_name") || "")];
						const row_name = String(dialog.get_value("source_row") || "");
						const row = order && Array.isArray(order.rows)
							? order.rows.find(function (item) { return String(item.source_row) === row_name; })
							: null;
						const target_qty = Number(dialog.get_value("target_qty"));
						if (!row || !Number.isFinite(target_qty) || target_qty <= 0 || target_qty > Number(row.qty)) {
							frappe.msgprint(__("请确认来源行和目标数量；目标数量必须大于 0 且不超过订购数量。"));
							return;
						}
						const action_button = dialog.get_primary_btn();
						action_button.attr("disabled", true).html('<span class="spinner-border spinner-border-sm"></span> ' + esc(__("保存中")));
						const correlation_id = crypto.randomUUID();
						frappe.call({
							method: "synora_agentic_erp.api.confirm_p2p_goal",
							type: "POST",
							args: {
								run_id: run_id,
								goal: JSON.stringify({
									schema_version: "1",
									source_doctype: "Purchase Order",
									source_name: String(order.source_name),
									source_rows: [{
										source_row: String(row.source_row),
										item_code: String(row.item_code),
										target_qty: String(target_qty),
									}],
									settlement_endpoint: "RECEIVED_BILLED_PAID",
								}),
								correlation_id: correlation_id,
							},
							callback: function (response) {
								if (response.message && response.message.ok) {
									dialog.hide();
									if (detail_dialog) { detail_dialog.hide(); }
									refresh();
									show_detail(run_id);
									return;
								}
								action_button.attr("disabled", false).html(__("保存目标并继续调查"));
								frappe.msgprint(api_failure_copy(__("保存 P2P 目标失败"), response.message, __("目标未保存。"), correlation_id));
							},
							error: function (xhr) {
								action_button.attr("disabled", false).html(__("保存目标并继续调查"));
								frappe.msgprint(api_failure_copy(__("保存 P2P 目标失败"), xhr, __("目标未保存。"), correlation_id));
							},
						});
					},
				});

				function refresh_rows() {
					const order = order_by_name[String(dialog.get_value("source_name") || "")];
					const rows = order && Array.isArray(order.rows) ? order.rows : [];
					dialog.set_df_property("source_row", "options", rows.map(function (row) { return String(row.source_row); }).join("\n"));
					dialog.fields_dict.source_row.refresh();
					if (rows.length) {
						dialog.set_value("source_row", String(rows[0].source_row));
						dialog.set_value("target_qty", Number(rows[0].qty));
						dialog.fields_dict.facts.$wrapper.html(
							'<div class="small text-muted">' +
								__("物料") + ": " + esc(rows[0].item_code) + " · " +
								__("订购") + ": " + esc(rows[0].qty) + " · " +
								__("已收货") + ": " + esc(rows[0].received_qty) + " · " +
								__("已开票金额") + ": " + esc(rows[0].billed_amount) +
							'</div>'
						);
					} else {
						dialog.set_value("source_row", "");
						dialog.fields_dict.facts.$wrapper.empty();
					}
				}
				dialog.fields_dict.source_name.$input.on("change", refresh_rows);
				dialog.show();
				refresh_rows();
			},
			error: function (xhr) {
				trigger.attr("disabled", false).html(original);
				frappe.msgprint(api_failure_copy(__("读取 P2P 来源失败"), xhr, __("当前可用 Purchase Order 不可读取。"), ""));
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
				is_p2p_action(type)
					? "synora_agentic_erp.api.execute_p2p_action"
					: type === "CREATE_PO_DRAFT" ? "synora_agentic_erp.api.execute_purchase_order" : "synora_agentic_erp.api.execute_material_request",
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
				is_p2p_action(type)
					? "synora_agentic_erp.api.reconcile_p2p_action"
					: type === "CREATE_PO_DRAFT" ? "synora_agentic_erp.api.reconcile_purchase_order" : "synora_agentic_erp.api.reconcile_material_request",
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

	function bind_p2p_chain_actions(wrapper, run_id, dialog) {
		wrapper.find(".p2p-goal-confirm").on("click", function () {
			open_p2p_goal_dialog(run_id, $(this), dialog);
		});
		wrapper.find(".p2p-run-investigate").on("click", function () {
			p2p_run_call($(this), "synora_agentic_erp.api.investigate_p2p_run", __("调查中"), run_id, dialog);
		});
		wrapper.find(".p2p-run-resume").on("click", function () {
			p2p_run_call($(this), "synora_agentic_erp.api.resume_p2p_run", __("重新调查中"), run_id, dialog);
		});
		wrapper.find(".p2p-run-finalize").on("click", function () {
			p2p_run_call($(this), "synora_agentic_erp.api.finalize_p2p_run", __("确认完成中"), run_id, dialog);
		});
		wrapper.find(".p2p-run-cancel").on("click", function () {
			p2p_run_call($(this), "synora_agentic_erp.api.cancel_p2p_run", __("停止调度中"), run_id, dialog);
		});
	}

	function render_approval_queue(approvals) {
		if (!Array.isArray(approvals) || !approvals.length) {
			return "";
		}
		const cards = approvals.map(function (action, index) {
			const payload = action.payload || {};
			const calculation = action.calculation || null;
			const id = "approval-queue-" + index;
			const state = String(action.state || "AWAITING_APPROVAL");
			const controls = state === "APPROVED"
				? '<button type="button" class="btn btn-primary approval-queue-execute" data-action="' + esc(action.action_id) + '" data-digest="' + esc(action.proposal_digest) + '" data-key="' + esc(action.idempotency_key || "") + '" data-type="' + esc(action.action_type || "") + '">' + governance_execute_copy(action) + '</button>'
				: '<button type="button" class="btn btn-success approval-queue-decide" data-action="' + esc(action.action_id) + '" data-decision="ALLOW" data-digest="' + esc(action.proposal_digest) + '">' + __("批准") + '</button>' +
					'<button type="button" class="btn btn-outline-danger approval-queue-decide" data-action="' + esc(action.action_id) + '" data-decision="DECLINE" data-digest="' + esc(action.proposal_digest) + '">' + __("拒绝") + '</button>' +
					'<button type="button" class="btn btn-outline-secondary approval-queue-decide" data-action="' + esc(action.action_id) + '" data-decision="CHANGES_REQUESTED" data-digest="' + esc(action.proposal_digest) + '">' + __("请求修改") + '</button>';
			const state_copy = state === "APPROVED"
				? __("已批准：当前审批人可以执行这一项 ERP 动作。")
				: __("独立审批：审批人与发起人必须是不同用户；批准只绑定这一份 digest。");
			return '<article class="border rounded p-3 mb-2" aria-labelledby="' + id + '-label">' +
				'<h6 id="' + id + '-label">' + governance_action_copy(action) + '</h6>' +
				'<div class="small mb-2">' +
					(payload.company ? __("公司") + ": " + esc(payload.company) + " · " : "") +
					(payload.source_doctype && payload.source_name ? __("来源单据") + ": " + esc(payload.source_doctype) + " / " + esc(payload.source_name) : "") +
					"<br>" + __("风险") + ": " + esc(action.risk_class || "—") +
				'</div>' + governance_financial_summary(calculation) + '<div class="small text-muted mb-2" aria-live="polite">' + state_copy +
				'</div><div class="btn-group btn-group-sm" role="group" aria-label="' + esc(__("独立审批操作")) + '">' + controls +
				'</div></article>';
		}).join("");
		return '<section class="approval-queue mt-3" aria-labelledby="approval-queue-label">' +
			'<h5 id="approval-queue-label">' + __("独立审批队列") + '</h5>' +
			'<div class="small text-muted mb-2">' + __("这里只显示当前用户有权审批的动作，不开放发起人的 Run 详情。") + '</div>' + cards + '</section>';
	}

	function bind_approval_actions(wrapper) {
		wrapper.find(".approval-queue-decide").on("click", function () {
			const button = $(this);
			const decision = String(button.data("decision"));
			governance_call(
				button,
				"synora_agentic_erp.api.decide_action",
				{
					action_id: button.data("action"),
					decision: decision,
					proposal_digest: button.data("digest"),
					reason: decision === "ALLOW" ? __("通过独立审批队列确认") : decision === "DECLINE" ? __("通过独立审批队列拒绝") : __("通过独立审批队列请求修改"),
					correlation_id: crypto.randomUUID(),
				},
				__("提交审批中"),
				null,
				null
			);
		});
		wrapper.find(".approval-queue-execute").on("click", function () {
			const button = $(this);
			governance_call(
				button,
				"synora_agentic_erp.api.execute_p2p_action",
				{
					action_id: button.data("action"),
					expected_proposal_digest: button.data("digest"),
					idempotency_key: button.data("key"),
					correlation_id: crypto.randomUUID(),
				},
				__("执行中"),
				null,
				null
			);
		});
	}

	function refresh() {
		container.html('<div class="text-muted text-center py-5"><span class="spinner-border spinner-border-sm"></span> ' + __("加载中…") + "</div>");
		frappe.call({
			method: "synora_agentic_erp.api.list_runs",
			type: "GET",
			callback: function (r) {
				const runs = (r.message && r.message.runs) || [];
				frappe.call({
					method: "synora_agentic_erp.api.list_pending_approvals",
					type: "GET",
					callback: function (approval_response) {
						render(runs, (approval_response.message && approval_response.message.approvals) || []);
					},
					error: function () {
						render(runs, []);
					},
				});
			},
			error: function () {
				container.html('<div class="text-danger text-center py-5">' + __("加载运行列表失败，请刷新重试。") + "</div>");
			},
		});
	}

	function render(runs, approvals) {
		const approval_queue = render_approval_queue(approvals);
		if (!runs.length) {
			container.html(
				approval_queue + '<div class="text-muted text-center py-5">' +
					__("尚无采购分析。前往 New Run 输入交付或补货目标开始。") +
					'<br><a class="btn btn-primary btn-sm mt-2" href="#new-run">' +
					__("新建运行") +
					"</a></div>"
			);
			bind_approval_actions(container);
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
			approval_queue + '<table class="table table-hover table-sm" aria-describedby="runs-table-caption">' +
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
		bind_approval_actions(container);

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

	function coach_citation(citation) {
		if (!citation || typeof citation !== "object") {
			return '<div class="small text-muted">' + __("来源不可用。") + "</div>";
		}
		if (citation.citation_type === "LIVE_ERP") {
			return '<div class="border-top pt-2 mt-2 small text-muted">' +
				"<b>" + __("实时 ERP") + "</b> · " +
				__("来源") + ": " + esc(citation.document_doctype) + " / " + esc(citation.document_name) +
				" · " + __("ERP 修改时间") + ": " + esc(citation.source_modified_at || "—") +
				" · " + __("读取时间") + ": " + esc(citation.captured_at || "—") +
				" · " + __("版本") + ": " + esc(citation.frappe_revision || citation.erpnext_revision || "—") +
				"</div>";
		}
		if (citation.citation_type === "RETRIEVAL") {
			return '<div class="border-top pt-2 mt-2 small text-muted">' +
				"<b>" + __("检索资料") + "</b> · " +
				__("来源类型") + ": " + esc(citation.source_type) +
				" · " + __("修订版本") + ": " + esc(citation.revision) +
				" · " + __("ERP 版本") + ": " + esc(citation.erp_version) +
				"</div>";
		}
		return '<div class="small text-muted">' + __("来源类型不可用。") + "</div>";
	}

	function render_coach_result(result, index) {
		const status = typeof result.answer_status === "string" ? result.answer_status : "";
		const status_copy = COACH_STATUS_COPY[status] || esc(status || __("状态不可用"));
		const status_class = status === "CONFLICT" ? "border-warning" : "border";
		let html = '<article class="coach-result ' + status_class + ' rounded p-3 mb-3" data-coach-result="' + esc(index) + '">';
		html += '<div class="d-flex justify-content-between align-items-start flex-wrap"><h6>' + __("回答状态") + ": " + status_copy + '</h6>' +
			'<span class="small text-muted">' + esc(result.created_at || "—") + "</span></div>";
		if (result.current_document) {
			html += '<div class="small text-muted mb-2">' + __("上下文") + ": " +
				esc(result.current_document.doctype) + " / " + esc(result.current_document.name) + "</div>";
		}
		if (status === "CONFLICT") {
			html += '<div class="alert alert-warning py-2" role="alert">' + __("当前证据存在冲突，请以 ERP 当前状态和人工核对为准。") + "</div>";
		}
		if (status === "UNKNOWN" || status === "REFUSED") {
			html += '<div class="alert alert-secondary py-2" role="status"><b>' + __("未形成答案") + "</b>: " +
				esc(result.refusal_reason || __("没有可展示的原因。")) + "</div>";
		} else if (status === "ANSWERED" || status === "CONFLICT") {
			html += '<section class="mb-2"><h6>' + __("有依据的回答") + '</h6><p style="white-space: pre-wrap; overflow-wrap:anywhere;">' +
				esc(result.answer) + "</p></section>";
		}
		const citations = Array.isArray(result.citations) ? result.citations : [];
		const claims = Array.isArray(result.claims) ? result.claims : [];
		if (claims.length) {
			html += '<section class="mb-2"><h6>' + __("逐条 Claim 与来源") + "</h6>";
			claims.forEach(function (claim) {
				const refs = Array.isArray(claim.citation_refs) ? claim.citation_refs : [];
				html += '<article class="border rounded p-2 mb-2"><div><b>#' + esc(claim.ordinal) + " · " +
					esc(COACH_CLAIM_TYPE_COPY[claim.claim_type] || claim.claim_type || __("未知类型")) +
					'</b></div><p class="mb-1" style="white-space: pre-wrap; overflow-wrap:anywhere;">' + esc(claim.text) + "</p>";
				refs.forEach(function (reference) {
					const citation = citations.find(function (item) {
						return item && item.citation_id === reference;
					});
					html += coach_citation(citation);
				});
				html += "</article>";
			});
			html += "</section>";
		}
		const trace = result.trace && typeof result.trace === "object" ? result.trace : {};
		const trace_id = "coach-trace-" + String(index).replace(/[^a-zA-Z0-9_-]/g, "");
		html += '<details class="mt-2"><summary>' + __("Trace 与运行元数据") + "</summary>" +
			'<div id="' + trace_id + '" class="small text-muted mt-2" style="overflow-wrap:anywhere;">' +
			__("Run") + ": " + esc(result.run_id) + " · " + __("Correlation") + ": " + esc(result.correlation_id) +
			" · " + __("耗时") + ": " + esc(result.latency_ms) + "ms<br>" + render_trace_payload(trace) + "</div></details>";
		return html + "</article>";
	}

	function render_coach_results(wrapper, results) {
		if (!Array.isArray(results) || !results.length) {
			wrapper.text(__("当前运行尚无 ERP Coach 结果。"));
			return;
		}
		wrapper.html(results.map(render_coach_result).join(""));
	}

	function load_coach_detail(run_id, wrapper) {
		wrapper.text(__("加载 ERP Coach 结果…"));
		frappe.call({
			method: "synora_agentic_erp.api.coach_run_detail",
			args: { run_id: run_id },
			type: "GET",
			callback: function (r) {
				if (!r.message || !r.message.ok) {
					wrapper.text(__("Coach 历史结果不可用。"));
					return;
				}
				render_coach_results(wrapper, r.message.results);
			},
			error: function () {
				wrapper.text(__("Coach 历史结果读取失败，请刷新重试。"));
			},
		});
	}

	function build_coach_panel() {
		return '<section class="coach-history mt-3" aria-labelledby="coach-history-label">' +
			'<h5 id="coach-history-label">' + __("ERP Coach 历史") + "</h5>" +
			'<div class="coach-detail-content" aria-live="polite" role="status"></div></section>';
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
				const p2p_chain_panel = build_p2p_chain_panel(data.p2p_chain || null, run);
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
					const orchestration = ev.orchestration || {};
					if (orchestration.mode) { ev_bits.push(__("模式") + ": " + esc(orchestration.mode)); }
					if (typeof orchestration.model_calls === "number") {
						ev_bits.push(__("模型调用") + ": " + esc(orchestration.model_calls));
					}
					if (typeof orchestration.handoff_count === "number" || typeof orchestration.revision_count === "number") {
						ev_bits.push(__("handoff/revision") + ": " + esc(orchestration.handoff_count || 0) + "/" + esc(orchestration.revision_count || 0));
					}
					if (orchestration.stop_reason) { ev_bits.push(__("停止") + ": " + esc(orchestration.stop_reason)); }
					const context_evidence = ev.context_evidence || {};
					if (context_evidence.prompt_profile_id) {
						ev_bits.push(__("Prompt Profile") + ": " + esc(context_evidence.prompt_profile_id));
					}
					if (context_evidence.context_builder_version) {
						ev_bits.push(__("ContextBuilder") + ": " + esc(context_evidence.context_builder_version));
					}
					if (typeof context_evidence.actual_prompt_tokens === "number") {
						ev_bits.push(__("实际输入 Token") + ": " + esc(context_evidence.actual_prompt_tokens));
					}
					if (typeof context_evidence.estimated_input_units_before === "number") {
						ev_bits.push(
							__("估算输入") +
							": " +
							esc(context_evidence.estimated_input_units_before) +
							" → " +
							esc(context_evidence.estimated_input_units_after) +
							" / " +
							esc(context_evidence.input_budget)
						);
					}
					if (context_evidence.compression_reasons && context_evidence.compression_reasons.length) {
						ev_bits.push(__("压缩原因") + ": " + context_evidence.compression_reasons.map(esc).join(", "));
					}
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
					const coach_panel = build_coach_panel();
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
						coach_panel +
					p2p_chain_panel +
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
					bind_p2p_chain_actions(content_wrapper, run.run_id, dialog);
					dialog.show();
					load_coach_detail(run.run_id, content_wrapper.find(".coach-detail-content"));
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
