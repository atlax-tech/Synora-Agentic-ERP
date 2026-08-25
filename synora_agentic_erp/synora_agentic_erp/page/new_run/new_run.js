frappe.pages["new-run"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("New Run"),
		single_column: true,
	});
	page.set_title(__("新建智能体运行"));

	let scope = [];

	// 目标输入（P3.1 批准：服务端 1000 字符上限，前端同样提示）
	page.goal_field = page.add_field({
		fieldname: "goal",
		label: __("目标 Goal"),
		fieldtype: "Text",
		reqd: 1,
		placeholder: __("描述交付或补货目标，例如：确保 SYNORA-P1-Item-1001 未来 90 天不缺货"),
	});
	page.goal_counter = $('<div class="text-muted small" style="padding: 0 8px 8px;"></div>');
	page.main.append(page.goal_counter);
	page.goal_field.$input.on("input", function () {
		const value = page.goal_field.$input.val() || "";
		const len = value.length;
		page.goal_counter.text(len + "/1000");
		if (len > 1000) {
			page.goal_counter.css("color", "var(--red-600)");
		} else {
			page.goal_counter.css("color", "");
		}
	});

	// 授权范围：公司 / 仓库（空 = 公司全部仓库，P3.1 批准）
	page.company_field = page.add_field({
		fieldname: "company",
		label: __("公司"),
		fieldtype: "Select",
		options: [""],
		change: function () {
			render_warehouses();
		},
	});
	page.warehouse_field = page.add_field({
		fieldname: "warehouse",
		label: __("仓库（留空 = 公司全部仓库）"),
		fieldtype: "Select",
		options: [""],
	});
	page.window_field = page.add_field({
		fieldname: "time_window_days",
		label: __("时间窗口（天，缺省 90）"),
		fieldtype: "Int",
		default: 90,
	});

	page.status_area = $('<div style="padding: 0 8px 8px;"></div>');
	page.main.append(page.status_area);

	page.set_primary_action(__("开始分析"), function () {
		submit_goal();
	});

	function render_warehouses() {
		const company = page.company_field.value;
		const entry = scope.find(function (item) {
			return item.company === company;
		});
		const warehouses = entry ? entry.warehouses : [];
		page.warehouse_field.df.options = [""].concat(warehouses);
		page.warehouse_field.refresh();
		page.warehouse_field.set_value("");
	}

	function set_status(html, kind) {
		page.status_area.html(html);
		if (kind === "danger") {
			page.status_area.children().addClass("text-danger");
		} else if (kind === "muted") {
			page.status_area.children().addClass("text-muted");
		}
	}

	// 加载授权范围：空 = 无权限（禁用并说明，不泄露不可访问数据）
	frappe.call({
		method: "synora_agentic_erp.api.available_scope",
		type: "GET",
		callback: function (r) {
			scope = (r.message && r.message.scope) || [];
			if (!scope.length) {
				page.company_field.df.read_only = 1;
				page.company_field.refresh();
				page.warehouse_field.df.read_only = 1;
				page.warehouse_field.refresh();
				page.set_primary_action(__("开始分析"), function () {
					/* no-op: 无权限 */
				});
				page.btn_primary.attr("disabled", true);
				set_status(
					__("你没有读取该公司采购数据的权限，请联系 ERP 管理员配置公司/仓库访问权限。"),
					"muted"
				);
				return;
			}
			page.company_field.df.options = scope.map(function (item) {
				return item.company;
			});
			page.company_field.refresh();
			if (scope.length === 1) {
				page.company_field.set_value(scope[0].company);
			}
		},
		error: function () {
				page.company_field.df.read_only = 1;
				page.company_field.refresh();
				page.warehouse_field.df.read_only = 1;
				page.warehouse_field.refresh();
				page.btn_primary.attr("disabled", true);
				set_status(
					__("无法加载授权范围，请刷新重试或联系 ERP 管理员。"),
					"danger"
				);
		},
	});

	function submit_goal() {
		const goal = page.goal_field.$input.val() || "";
		const company = page.company_field.value;
		if (!goal || !goal.trim()) {
			set_status(__("缺少业务目标。请描述交付或补货目标后再开始分析。"), "danger");
			return;
		}
		if (goal.length > 1000) {
			set_status(__("目标超过 1000 字符上限，请缩短后重试。"), "danger");
			return;
		}
		if (!company) {
			set_status(__("缺少公司范围。请选择要分析的公司。"), "danger");
			return;
		}
		let days = page.window_field.value;
		if (days === null || days === undefined || days === "") {
			days = 90;
		}
		days = parseInt(days, 10);
		if (isNaN(days) || days < 1 || days > 365) {
			set_status(__("时间窗口需为 1–365 之间的天数。"), "danger");
			return;
		}
		// 提交中: 禁止重复提交 (PRD F-001 交互处理)
		page.btn_primary.attr("disabled", true);
		page.btn_primary.html('<span class="spinner-border spinner-border-sm"></span> ' + __("正在创建运行…"));
		const args = {
			company: company,
			goal: goal,
			time_window_days: days,
		};
		if (page.warehouse_field.value) {
			args.warehouse = page.warehouse_field.value;
		}
		frappe.call({
			method: "synora_agentic_erp.api.issue_run",
			args: args,
			callback: function (r) {
				if (r.message && r.message.ok) {
					page.btn_primary.html(__("开始分析"));
					page.btn_primary.attr("disabled", false);
					// 创建成功: 跳转 Runs 列表, 新 run 显示在顶部
					frappe.set_route("runs");
				}
			},
			error: function (xhr) {
				page.btn_primary.html(__("开始分析"));
				page.btn_primary.attr("disabled", false);
				show_failure(xhr);
			},
		});
	}

	function show_failure(xhr) {
		let code = "";
		let message = __("创建运行失败，请稍后重试。");
		let correlation_id = "";
		if (xhr && xhr.responseJSON && xhr.responseJSON.error) {
			code = xhr.responseJSON.error.code || "";
			message = xhr.responseJSON.error.message || message;
			correlation_id = xhr.responseJSON.correlation_id || "";
		}
		let html = __("创建运行失败") + "（" + frappe.utils.escape_html(code) + "）：" + frappe.utils.escape_html(message);
		if (correlation_id) {
			html += "<br>" + __("关联标识") + ": " + frappe.utils.escape_html(correlation_id);
		}
		set_status(html, "danger");
		// 用户输入保留在表单中，可直接重试
	}
};
