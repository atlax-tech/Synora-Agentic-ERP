# P3.1 产品与前端决策包（Goal 限制 / 默认范围 / 前端基线）

- 状态：`APPROVED` — 2026-08-25 用户批准全部 7 项（§5 列表），本节以下为批准后的固化为准
- 批准记录：2026-08-25 用户通过 AskUserQuestion 逐项确认，三项分组全部选择"批准（推荐）"；批准后动作见 §6
- 需求：PLAN P3.1；PRD F-001 的 `[待确认]` 项；DESIGN §Open Design Decisions

## 0. 决策包结构

每个决策项标注：`CONFIRMED`（固定源码/官方事实取证）或 `PROPOSED`（产品选择，需用户批准）。

## 1. Goal 限制（PROPOSED）

| 决策 | 建议 | 理由 | 影响 |
| --- | --- | --- | --- |
| goal 长度上限 | **1000 字符**（服务端 fail-closed 校验） | PRD F-001 要求"超长输入明确处理"；采购目标自然语言通常远小于此，1000 给足余量且防滥用 | Run DocType 增加 goal 字段（Text）；`issue_run` 输入校验 |
| goal 保存 | 保存原始文本，**不得作为直接写指令** | PRD 边界（恶意指令/敏感信息需处理） | 模型输出与用户输入均按未信任输入处理 |

取证事实（CONFIRMED）：Frappe 文本字段 `longtext` 无长度上限；`bounded_text` 现有上限 140 用于工具参数，goal 属产品语义需单独定值。

## 2. 默认范围（PROPOSED）

| 决策 | 建议 | 理由 | 影响 |
| --- | --- | --- | --- |
| warehouse_scope 为空 | 默认**公司全部仓库**（工具不按仓库过滤，返回该公司全部仓库数据） | PRD 未定义空值规则；采购短缺分析通常需看全公司 | `issue_run(warehouse=None)` 已在 P2.6 验证通过；spec 语义为"公司范围" |
| time_window 缺省 | 缺省表示**"当前库存 + 在途采购 + 截至未来 90 天的需求"** | F-003 确定性分析需要明确时间边界；90 天覆盖典型补货周期 | SPEC 状态机/分析输入增加 time_window 语义；缺省值写入 SPEC |

## 3. 固定 Frappe 前端基线（CONFIRMED + PROPOSED）

取证事实（CONFIRMED，固定 Frappe `6a329d0`）：

- 前端技术栈：**Vue 3.3.0 + Bootstrap 4.6.2** + `@headlessui/vue` + `@vueuse/core`（package.json dependencies）
- **无 `frappe-ui` 依赖**（node_modules 未安装）；Desk 为既有组件体系（`frappe.ui.form`、Control、Dialog、List/Form/Report 视图）
- 无 `browserslist`/`.browserslistrc`（工具链未锁定浏览器范围）
- Frappe 官方 FAQ：前端基于 jQuery + Bootstrap，响应式设计

产品决策（PROPOSED）：

| 决策 | 建议 | 理由 |
| --- | --- | --- |
| Synora UI 组件基线 | 使用 **Frappe Desk 既有组件**（Dialog/Form/List + Bootstrap 4.6.2），不引入 frappe-ui | 固定版本未安装 frappe-ui；沿用 Desk 既有体系保证与 ERPNext 视觉一致、维护成本低 |
| 浏览器矩阵 | **Chrome/Edge/Firefox/Safari 各最新 2 个大版本**（桌面） | Frappe 社区惯例（官方未发布机器可验证矩阵）；DESIGN 已限定桌面第一目标 |
| 桌面 viewport | 最小支持宽度 **1280px**，主布局自适应 | DESIGN 限定桌面优先；1280 为常见企业桌面基线 |
| 可访问性目标 | **WCAG 2.1 AA**，键盘操作 + 非颜色状态 + 可见焦点 | DESIGN 已要求键盘/非颜色状态；AA 为常见企业标准 |

## 4. 双语术语表（PROPOSED 草案）

| 英文 | 中文 | 备注 |
| --- | --- | --- |
| Agent Run | 智能体运行 | 有身份/范围/状态的服务端记录 |
| Goal | 目标 | 用户自然语言业务目标 |
| Proposed Action | 提议动作 | 版本化写入提议 |
| Approval Decision | 审批决定 | 独立审批人的决策记录 |
| Execution Receipt | 执行回执 | 幂等+对账的验证结果 |
| Reconciliation | 对账 | 不确定结果的人工接管路径 |
| Shortage Risk | 缺货风险 | 确定性计算 |
| Duplicate Purchase Risk | 重复采购风险 | 确定性计算 |
| Scope | 范围 | 公司/仓库授权范围 |
| Draft / Submit | 草稿 / 提交 | MR/PO 生命周期 |

规则：**批准后全产品单一术语**（DESIGN §Content and Localization），状态机/错误码文案同步。

## 5. 需要用户批准的决定项（汇总）

1. Goal 长度上限 = 1000 字符（服务端校验）
2. warehouse_scope 空值默认 = 公司全部仓库
3. time_window 缺省 = 当前库存 + 在途 + 未来 90 天需求
4. 前端组件基线 = Desk 既有组件（Bootstrap 4.6.2），不引入 frappe-ui
5. 浏览器矩阵 = 主流浏览器最新 2 个大版本（桌面，最小宽度 1280px）
6. 可访问性 = WCAG 2.1 AA
7. 双语术语表（§4 草案）

## 6. 批准后动作

- Goal 限制/默认范围 → 固化到 PRD F-001 数据规范与 SPEC（Run 状态机/分析输入）
- 前端基线/浏览器/可访问性 → 固化到 DESIGN（Open Design Decisions 关闭）
- 术语表 → 固化到 DESIGN §Content and Localization + SPEC
- 触发 P3.2 Agent Run 实现（Run DocType 扩展 goal/time_window 字段）

## 附：取证来源

- 固定 Frappe `6a329d0` `package.json`（dependencies：vue 3.3.0、bootstrap 4.6.2、@headlessui/vue、@vueuse/core；无 frappe-ui、无 browserslist）
- `apps/frappe/node_modules/frappe-ui` 不存在（容器内实测）
- docs.frappe.io（官方 FAQ：jQuery + Bootstrap + 响应式）
- PRD F-001 数据规范 `[待确认]` 项；DESIGN §Open Design Decisions
