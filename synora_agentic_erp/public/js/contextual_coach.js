(function () {
	"use strict";

	const CLAIM_TYPE_COPY = {
		ERP_FACT: __("ERP 事实 · 权威 ERP 数据"),
		RETRIEVED_KNOWLEDGE: __("检索知识 · 已验证资料"),
		RECOMMENDATION: __("建议 · 推荐意见"),
	};
	const ANSWER_STATUS_COPY = {
		ANSWERED: __("已回答"),
		CONFLICT: __("存在冲突，需要人工确认"),
		UNKNOWN: __("无法形成有依据的答案"),
		REFUSED: __("已拒绝回答"),
	};
	const ERROR_COPY = {
		AUTHENTICATION_REQUIRED: __("登录状态不可用。"),
		AUTHENTICATION_REJECTED: __("当前请求未通过身份校验。"),
		COACH_CLAIMS_NOT_PERSISTED: __("Coach 证据未能保存。"),
		COACH_RESPONSE_INVALID: __("Coach 返回内容未通过安全校验。"),
		COACH_RUN_NOT_AVAILABLE: __("该次 Coach 运行不可用。"),
		CONFIG_ERROR: __("Coach 配置不可用。"),
		CONFLICT: __("数据状态已变化，请刷新后重试。"),
		ERP_ERROR: __("ERP 或 Coach 服务暂时不可用。"),
		INVALID_INPUT: __("请求参数无效。"),
		PERMISSION_DENIED: __("你没有读取该单据的权限。"),
		RUN_REJECTED: __("该次运行不可用。"),
		SCOPE_DENIED: __("当前公司范围不可用。"),
		UNAVAILABLE: __("Coach 服务暂时不可用。"),
	};

	function display_text(value) {
		return value === null || value === undefined ? "" : String(value);
	}

	function set_text(wrapper, value) {
		wrapper.empty().text(display_text(value));
	}

	function add_value(parent, label, value) {
		const row = $("<div>", { class: "small mb-1" });
		$("<b>").text(label + ": ").appendTo(row);
		$("<span>").text(display_text(value)).appendTo(row);
		parent.append(row);
	}

	function saved_context(frm) {
		if (!frm || !frm.doc) {
			return { ok: false, message: __("当前单据不可用，请刷新后重试。") };
		}
		if (typeof frm.is_new === "function" && frm.is_new()) {
			return {
				ok: false,
				message: __("请先保存并重新加载单据。Synora 只读取 ERP 中已保存的版本。"),
			};
		}
		if (typeof frm.is_dirty === "function" && frm.is_dirty()) {
			return {
				ok: false,
				message: __("请先保存并重新加载单据。Synora 只读取 ERP 中已保存的版本。"),
			};
		}
		if (!display_text(frm.doc.name).trim()) {
			return { ok: false, message: __("当前单据没有稳定名称，请刷新后重试。") };
		}
		if (!display_text(frm.doc.company).trim()) {
			return { ok: false, message: __("当前单据缺少公司范围，无法发起 Coach。") };
		}
		return { ok: true, message: "" };
	}

	function error_details(source) {
		const body = source && source.responseJSON ? source.responseJSON : source || {};
		const wrapped = body && body.message && typeof body.message === "object" ? body.message : body;
		const error = wrapped && typeof wrapped.error === "object" ? wrapped.error : {};
		const raw_code = error && error.code;
		const code =
			typeof raw_code === "string" && /^[A-Z0-9_]{1,64}$/.test(raw_code)
				? raw_code
				: "UNAVAILABLE";
		const raw_correlation = wrapped && wrapped.correlation_id;
		const correlation_id =
			typeof raw_correlation === "string" && raw_correlation.length <= 64 ? raw_correlation : "";
		return {
			code: code,
			message: ERROR_COPY[code] || ERROR_COPY.UNAVAILABLE,
			correlation_id: correlation_id,
		};
	}

	function show_failure(status_wrapper, result_wrapper, source, fallback) {
		const details = error_details(source);
		let message = fallback + "（" + details.code + "）";
		if (details.message) {
			message += " " + details.message;
		}
		if (details.correlation_id) {
			message += " " + __("关联标识") + ": " + details.correlation_id;
		}
		set_text(status_wrapper, message);
		set_text(result_wrapper, __("未生成可展示的 Coach 答案。"));
	}

	function render_citation(parent, citation) {
		const source = $("<div>", { class: "border-top pt-2 mt-2 small text-muted" });
		if (!citation || typeof citation !== "object") {
			$("<span>").text(__("来源不可用。")).appendTo(source);
			parent.append(source);
			return;
		}
		if (citation.citation_type === "LIVE_ERP") {
			add_value(source, __("来源"), __("实时 ERP"));
			add_value(source, __("DocType"), citation.document_doctype);
			add_value(source, __("单据"), citation.document_name);
			add_value(source, __("ERP 修改时间"), citation.source_modified_at);
			add_value(source, __("读取时间"), citation.captured_at);
			return parent.append(source);
		}
		if (citation.citation_type === "RETRIEVAL") {
			add_value(source, __("来源"), __("检索资料"));
			add_value(source, __("来源类型"), citation.source_type);
			add_value(source, __("修订版本"), citation.revision);
			add_value(source, __("ERP 版本"), citation.erp_version);
			return parent.append(source);
		}
		$("<span>").text(__("来源类型不可用。")).appendTo(source);
		parent.append(source);
	}

	function render_claims(parent, claims, citations) {
		if (!Array.isArray(claims) || !claims.length) {
			return;
		}
		const section = $("<section>", { class: "mt-3" });
		$("<h5>").text(__("逐条事实与证据")).appendTo(section);
		claims.forEach(function (claim) {
			if (!claim || typeof claim !== "object") {
				return;
			}
			const article = $("<article>", { class: "border rounded p-2 mb-2" });
			const claim_type = typeof claim.claim_type === "string" ? claim.claim_type : "";
			$("<h6>").text(CLAIM_TYPE_COPY[claim_type] || __("未知类型的事实")).appendTo(article);
			$("<p>").text(display_text(claim.text)).appendTo(article);
			const refs = Array.isArray(claim.citation_refs) ? claim.citation_refs : [];
			refs.forEach(function (reference) {
				const citation = Array.isArray(citations)
					? citations.find(function (item) {
							return item && item.citation_id === reference;
						})
					: null;
				render_citation(article, citation);
			});
			section.append(article);
		});
		parent.append(section);
	}

	function render_coach(result_wrapper, payload) {
		const coach = payload && payload.coach;
		if (!coach || typeof coach !== "object") {
			return false;
		}
		const result = $("<div>", { class: "synora-coach-result" });
		const answer_status = typeof coach.answer_status === "string" ? coach.answer_status : "";
		$("<h4>").text(__("回答状态")).appendTo(result);
		$("<p>", { class: "font-weight-bold" })
			.text(ANSWER_STATUS_COPY[answer_status] || __("状态不可用"))
			.appendTo(result);

		if (answer_status === "CONFLICT") {
			$("<p>", { class: "text-warning" })
				.text(__("当前证据存在冲突或不确定性，请以 ERP 当前状态和人工核对为准。"))
				.appendTo(result);
		}
		if (answer_status === "UNKNOWN" || answer_status === "REFUSED") {
			const refusal =
				typeof coach.refusal_reason === "string" && coach.refusal_reason.length <= 500
					? coach.refusal_reason
					: __("没有可展示的拒答原因。 ");
			$("<section>", { class: "mt-2" })
				.append($("<h5>").text(__("拒答说明")))
				.append($("<p>").text(refusal))
				.appendTo(result);
		} else if (answer_status === "ANSWERED" || answer_status === "CONFLICT") {
			$("<section>", { class: "mt-2" })
				.append($("<h5>").text(__("有依据的回答")))
				.append($("<p>").text(display_text(coach.answer)))
				.appendTo(result);
		}

		render_claims(result, coach.claims, coach.citations);
		result_wrapper.empty().append(result);
		return true;
	}

	function open_coach_dialog(frm) {
		const guard = saved_context(frm);
		if (!guard.ok) {
			frappe.msgprint({
				title: __("Synora Coach"),
				message: guard.message,
				indicator: "orange",
			});
			return;
		}

		const dialog = new frappe.ui.Dialog({
			title: __("Ask Synora"),
			fields: [
				{
					fieldname: "question",
					fieldtype: "Small Text",
					label: __("问题"),
					reqd: 1,
					max_length: 1000,
					description: __("请输入关于当前已保存 ERP 单据的问题，最多 1000 字符。"),
				},
				{ fieldname: "status", fieldtype: "HTML" },
				{ fieldname: "result", fieldtype: "HTML" },
			],
			primary_action_label: __("Ask Synora"),
			primary_action: function () {
				submit();
			},
		});

		const question_input = dialog.fields_dict.question.$input;
		const status_wrapper = dialog.fields_dict.status.$wrapper;
		const result_wrapper = dialog.fields_dict.result.$wrapper;
		question_input.attr("maxlength", 1000);
		status_wrapper.attr({ "aria-live": "polite", role: "status" });
		result_wrapper.attr({ role: "region", "aria-label": __("Coach 结果") });
		set_text(status_wrapper, __("答案将只基于服务器已保存的 ERP 版本。"));
		set_text(result_wrapper, __("输入问题后提交，结果会显示在这里。"));

		let busy = false;

		function set_busy(value) {
			busy = value;
			dialog.get_primary_btn().prop("disabled", value);
		}

		function submit() {
			if (busy) {
				return;
			}
			const current_guard = saved_context(frm);
			if (!current_guard.ok) {
				set_text(status_wrapper, current_guard.message);
				return;
			}
			const question = display_text(dialog.get_value("question"));
			if (!question.trim()) {
				set_text(status_wrapper, __("请输入问题后再提交。"));
				return;
			}
			if (question.length > 1000) {
				set_text(status_wrapper, __("问题超过 1000 字符上限，请缩短后重试。"));
				return;
			}

			set_busy(true);
			set_text(status_wrapper, __("正在读取当前 ERP 证据…"));
			let issued_message = null;
			let capability = null;
			let request_args = null;

			function clear_capability() {
				if (request_args) {
					request_args.capability = null;
				}
				if (issued_message && issued_message.run) {
					issued_message.run.capability = null;
				}
				request_args = null;
				issued_message = null;
				capability = null;
			}

			function finish() {
				clear_capability();
				set_busy(false);
			}

			try {
				frappe.call({
					method: "synora_agentic_erp.api.issue_run",
					type: "POST",
					args: {
						company: frm.doc.company,
						goal: question,
						execution_mode: "DETERMINISTIC",
						time_window_days: 90,
					},
					callback: function (response) {
						try {
							issued_message = response && response.message;
							const run = issued_message && issued_message.ok ? issued_message.run : null;
							if (
								!run ||
								typeof run.run_id !== "string" ||
								typeof run.capability !== "string"
							) {
								show_failure(
									status_wrapper,
									result_wrapper,
									issued_message,
									__("无法创建 Coach 运行。")
								);
								finish();
								return;
							}

							const run_id = run.run_id;
							capability = run.capability;
							delete run.capability;
							issued_message = null;
							request_args = {
								run_id: run_id,
								capability: capability,
								question: question,
								current_doctype: frm.doctype,
								current_name: frm.doc.name,
							};
							capability = null;
							set_text(status_wrapper, __("正在整理有依据的 Coach 回答…"));

							try {
								frappe.call({
									method: "synora_agentic_erp.api.ask_coach",
									type: "POST",
									args: request_args,
									callback: function (coach_response) {
										try {
											if (
												!coach_response ||
												!coach_response.message ||
												!coach_response.message.ok ||
												!render_coach(result_wrapper, coach_response.message)
											) {
												show_failure(
													status_wrapper,
													result_wrapper,
													coach_response && coach_response.message,
													__("Coach 请求未能返回可展示答案。")
												);
											} else {
												set_text(status_wrapper, __("已返回服务器校验后的 Coach 结果。"));
											}
										} catch (_error) {
											show_failure(
												status_wrapper,
												result_wrapper,
												{},
												__("Coach 请求未能返回可展示答案。")
											);
										} finally {
											finish();
										}
									},
									error: function (source) {
										show_failure(
											status_wrapper,
											result_wrapper,
											source,
											__("Coach 请求失败。")
										);
										finish();
									},
								});
							} catch (_error) {
								show_failure(
									status_wrapper,
									result_wrapper,
									{},
									__("Coach 请求失败。")
								);
								finish();
							}
						} catch (_error) {
							show_failure(
								status_wrapper,
								result_wrapper,
								{},
								__("Coach 请求失败。")
							);
							finish();
						}
					},
					error: function (source) {
						show_failure(
							status_wrapper,
							result_wrapper,
							source,
							__("无法创建 Coach 运行。")
						);
						finish();
					},
				});
			} catch (_error) {
				show_failure(
					status_wrapper,
					result_wrapper,
					{},
					__("无法创建 Coach 运行。")
				);
				finish();
			}
		}

		dialog.show();
	}

	function refresh(frm) {
		if (frm.__synora_contextual_coach_action) {
			return;
		}
		frm.__synora_contextual_coach_action = true;
		frm.add_custom_button(
			__("Ask Synora"),
			function () {
				open_coach_dialog(frm);
			},
			__("Synora")
		);
	}

	frappe.ui.form.on("Material Request", { refresh: refresh });
	frappe.ui.form.on("Purchase Order", { refresh: refresh });
})();
