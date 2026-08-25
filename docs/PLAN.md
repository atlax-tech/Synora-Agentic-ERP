# Synora 项目严格执行计划

状态：`CONFIRMED` 执行计划。版本：`PLAN-MAP-v1`。

## 1. 目的、权威与边界

本文告诉 Coding Agent 项目应当按什么顺序推进、每一步怎样取证、何时调用 Skill、怎样测试和审查、什么情况下必须停下。它是 `AGENTS.md` 之后的第二必读文档，但不是产品、架构或验收事实源。

阅读和执行顺序固定为：

1. 根目录 `AGENTS.md`；
2. 本文；
3. 本文为当前阶段指定的权威文档、上阶段证据、ADR、源码和测试；
4. 当前增量直接涉及的实现文件。

事实权威保持不变：

- `docs/PRD.md` 决定产品范围、用户、优先级和验收意图；
- `docs/ARCHITECTURE.md` 决定系统、信任、数据、依赖、审批和技术边界；
- `docs/DESIGN.md` 决定前端体验与交互义务；
- `docs/SPEC.md` 决定跨组件契约、状态机和需求追踪；
- `docs/DEVELOPMENT.md`、`docs/TESTING.md`、`docs/ACCEPTANCE.md` 分别决定工程、测试和验收规则；
- `docs/ROADMAP.md` 决定阶段顺序和完整交付范围；
- ADR 只能记录经过证据和批准形成的具体决定。

本文只能安排工作和设置门禁，不能新增、删除、降级或重新解释上述事实。若本文与事实文档冲突，将状态标为 `CONFLICTED`，停止实现并交给用户处理。

## 2. 当前状态与阶段定位

当前确认事实：

- 仓库是 `MANAGED_HARNESS`；产品、架构、设计、开发、测试、验收、Roadmap 和 SPEC 已建立；
- 尚无 Synora 业务代码、依赖清单、ERPNext Runtime 或产品构建/测试命令；
- Frappe/ERPNext 是只读上游依赖，不能修改其核心，也不能绕过其权限、校验、Workflow、事务和审计；
- Phase 0 只有在包含本文、AGENTS 入口、Harness 索引和验证日志的提交存在且校验通过后才算结束；
- Phase 0 结束后的下一个产品阶段是 Phase 1。

本文不维护逐步骤复选框。Agent 必须通过提交历史、开发日志、ADR、测试输出、运行证据和阶段出口条件判断进度，不能仅凭文件存在或前任 Agent 的自述判定完成。

阶段选择算法：

1. 读取最近的开发日志、提交历史和工作区状态；
2. 按 Phase 0 到 Phase 8 顺序核对出口证据；
3. 第一个缺少完整出口证据的阶段就是“下个阶段”；
4. 阶段内从编号最小且缺少验收证据的步骤继续；
5. 上一步未通过时不得开始下一步，上阶段未通过时不得开始下一阶段。

## 3. 用户阶段指令的机械解释

### “开始完成阶段 X”

- 先验证 Phase X 的所有前置阶段；
- 从 Phase X 最早缺少证据的步骤开始；
- 在同一次任务中持续完成该阶段的后续步骤；
- 遇到本文定义的停止条件时立即停下；
- 阶段出口通过后提交阶段报告并停止，不得自动进入 Phase X+1。

### “开始完成下个阶段”

- 使用阶段选择算法确定编号最小的未完成阶段；
- 明确告诉用户识别出的阶段及证据；
- 按“开始完成阶段 X”的规则执行。

### “继续工作”

- 重新检查工作区、输入文件哈希、最近开发日志、未提交 diff、已有测试证据和用户刚批准的决定；
- 以前次阻塞点为候选断点，但不得盲信旧状态；
- 证据未变化且批准范围清晰时继续；证据变化时重新生成 Context Receipt 和执行边界。

## 4. 精简增量闭环

一个增量交付一个用户能解释、能验证、能回滚的结果。默认重心是业务代码和测试，不为流程本身制造额外文件。

### 4.1 十行内 Context Receipt

编辑前用不超过十行说明：当前阶段/任务、业务目的、已知与未知、文件边界、主要风险、最相关自动化检查和人工验收。只有身份、权限、金额、写入、架构选择或权威冲突需要展开详细事实。可从源码或运行环境查到的事实先查，不抛给用户。

### 4.2 风险分级验证

| 等级 | 适用范围 | 必须完成 |
| --- | --- | --- |
| L1 普通 | 普通业务代码、只读页面、文案、局部重构 | Execute 自测、相关自动化检查、`ponytail-review` |
| L2 关键 | 身份、权限、金额、状态机、ERP 集成、幂等、审计、安全边界 | L1 + 一个独立 Test 或 Review；同时跨多个边界时才两者都用 |
| L3 出口/发布 | 阶段出口、发布、Tag、版本或依赖基线、固定 ERP/Frappe 版本 | 全量相关检查、阶段复杂度审计、独立对抗审查、Harness 健康检查 |

独立角色只检查，不顺手修代码。发现问题后由 Execute 修正，只重跑受影响检查；阶段出口再全量运行。任何等级都不能省略信任边界校验、数据安全、必要错误处理和可访问性。

### 4.3 文档与提交

- 实现、最小充分测试和必要契约文档放在同一增量；不为普通小修复单独同步 Harness/README。
- 每个 commit 前只更新当前 Phase 的一份开发日志，在文件顶部追加一轮；格式见 `docs/development-log/README.md`。
- 日志写业务结果、真实测试、手工验收、风险和学习说明，不复制完整 diff 或 Agent 对话。
- 使用一个小而完整的 Conventional Commit；不推送、不发布、不改写历史。
- 阶段出口完成后停止，不自动进入下一阶段。

### 4.4 实习生协作模式

- 适合学习且风险可控的任务优先作为小练习交给用户，不把整条业务链或安全门禁交出去。
- 布置前说明业务场景、为什么需要、代码入口、输入输出、完成标准、建议 test case 和不应修改的边界。
- 可在练习入口添加 `TODO(learning)` 结构提示；练习完成后应删除或关闭，不能成为无人负责的生产 TODO。
- 用户实现期间，Agent 默认只解释、给提示、审查和帮助定位；用户明确求助、任务确实超出能力或安全关键路径受阻时才接手。
- 每轮交付先回答五个大白话问题：解决什么业务问题、用户看到什么、数据怎样流动、最重要的三个文件、怎样手工验证。
- 用户提出的关键困惑及最终解释，写入当前 Phase 日志的“大白话讲解”或“面试追问”部分。

## 5. Skill 调用表

| 触发点 | 必须调用 | 调用方式与边界 |
| --- | --- | --- |
| 编码、修复、重构、测试代码、代码审查、依赖选择 | `ponytail` | 编辑前完整读取 `.agents/skills/ponytail/SKILL.md`，使用默认 `full`。先理解完整流程，再选最小完整方案；不得删除安全、错误处理、可访问性、数据保护或批准需求。 |
| 每个代码增量 | `ponytail-review` | L1 由当前 Agent 做精简审查；L2/L3 可交独立角色。只报告可删除复杂度，不替代正确性与安全检查。 |
| 每个阶段出口 | `ponytail-audit`、`ponytail-debt` | 对全仓做只读复杂度审计并收集明确标注的 Ponytail 延期项；结果进入阶段报告，不凭审计结果自动删代码。 |
| Harness 管理文件或权威事实实际变化 | `harness-update` | 只在需要同步时调用；先给文件级 proposal，批准后写入。普通代码提交不触发。 |
| 每个阶段出口 | `harness-check` | 只读检查 manifest、drift、引用、语义一致性、命令真实性、边界、安全和非虚构性；发现阻塞项不得继续。 |
| README 公开事实发生变化 | `readme-writer` | 修改前完整读取 Skill；只写已有证据，同时保持 `README.md` 与 `README.zh-CN.md` 语义一致。 |
| 用户明确批准产品需求变化 | `prd-writer` | 使用模式 C 增量融合；不覆盖既有 PRD，不用 Skill 自由补齐 `[待确认]`。 |
| 用户明确要求生成持久任务包 | `harness-prompt` | 以本文中的具体阶段/步骤为来源生成 execute/test/review 文件；默认工作流使用本文内置角色契约，不生成 `docs/prompts/`。 |

纯文档任务不因形式需要调用 Ponytail；代码相关任务不能跳过 Ponytail。

## 6. 需求到阶段的完整坐标

| 需求 | 实施阶段 | 阶段出口必须证明的结果 |
| --- | --- | --- |
| F-001 Agent Run 与目标输入 | Phase 3 | 身份、范围、状态、输入和 UI 正常/异常证据。 |
| F-002 授权上下文与 Typed ERP Tools | Phase 2 | 契约、权限、分页、超时和真实 ERP 集成证据。 |
| F-003 确定性采购风险分析 | Phase 3 | 固定输入的确定性计算和 UNKNOWN/NEEDS_INPUT 证据。 |
| F-004 可解释计划与 ProposedAction | Phase 4 | 版本化 schema、证据、冲突、过期和 fail-closed 证据。 |
| F-005 Policy / RBAC / Approval | Phase 4 | Workflow、权限、Draft 确认、职责分离和状态重检证据。 |
| F-006 MR Draft / PO Draft 受控执行 | Phase 4 | 真实创建、读回、权限、重复和失败恢复证据。 |
| F-007 Receipt、幂等与对账 | Phase 4 | replay、响应丢失、对账和人工介入证据。 |
| F-008 Audit / Trace / Failure Evidence | Phase 4 | correlation、脱敏、访问控制和失败分类证据。 |
| F-009 PO Submit | Phase 5 | 独立审批、当前状态、影响和恢复证据。 |
| F-010 Purchase Receipt | Phase 5 | 部分收货、库存、取消、幂等和恢复证据。 |
| F-011 Purchase Invoice | Phase 5 | 部分开票、税务/会计、取消、幂等和恢复证据。 |
| F-012 Payment 相关流程 | Phase 5 | 会计权威、职责分离、状态、对账和审计证据。 |
| F-013 Contextual ERP Coach | Phase 6 | 引用、拒答、权限、版本、冲突和注入评测。 |
| F-014 完整 RAG 演进 | Phase 6 | FTS5 基线和每个后续候选技术的采用或拒绝证据。 |
| F-015 条件式 Multi-Agent | Phase 7 | 相同数据集 A/B 和每个候选角色的采用或拒绝证据。 |

## 7. 未决项路由

| 未决项 | 最迟解决阶段 | 默认处理 |
| --- | --- | --- |
| `erp-version-pair` | Phase 1 | 通过真实 P2P 基线唯一收敛；固定版本前做独立对抗审查。 |
| `approval-workflow-mapping` | Phase 1 提证，Phase 4 启用写入前完成 | 从固定版本权限和 Workflow 取证；具体企业政策或多级规则交用户决定。 |
| `runtime-user-authorization` | Phase 2 | 做安全 Spike 和 ADR 选项，必须交用户批准后实施。 |
| `product-commands` | Phase 2 | 由实际脚手架和成功命令输出解决，不能从 README 推断。 |
| `frontend-design-baseline` | Phase 3 | 从固定 Frappe v16 取证；浏览器、可访问性和双语术语等产品选择交用户批准。 |
| Goal 长度及默认公司、仓库、时间范围 | Phase 3 | 提交产品决策包，用户批准后固化到 PRD/SPEC。 |
| `model-selection` | Phase 3 | 使用同一评测集比较；涉及远程数据、成本或安全边界时交用户决定。 |
| `workflow-engine-spike` | Phase 3，进入 Phase 4 前 | 先验证 interruption、approval、resume、reconciliation；无明确收益则保持确定性服务。 |
| 性能、并发、保留期、浏览器与可访问性目标 | 首次受影响阶段 | 先取得基线数据，再由用户批准验收目标，禁止编造数字。 |
| `vector-retrieval-threshold` | Phase 6 | 用 FTS5 原始结果形成阈值决策包，用户批准后才允许采用后续检索技术。 |
| `multi-agent-adoption-threshold` | Phase 7 | 用单 Agent 原始结果形成阈值决策包，用户批准后才允许采用角色。 |
| `third-party-licenses` | Phase 8 前，任何公开发布前必须完成 | 调查 MIT、GPL-3.0、CC BY-NC、NOTICE 和分发边界，实质发布选择交用户批准。 |
| 生产 checkpoint、存储和扩展路径 | 首次声称超出单实例能力前 | 没有测量需求时保持未决；不得提前引入复杂基础设施。 |

## 8. 必须停止并交给用户的情况

以下情况不能自由发挥：

- 产品规则、安全或身份边界、权限/Workflow 政策、验收阈值、性能目标、许可证发布边界需要决定；
- 两个或更多方案都有实质成立依据并影响架构、数据、成本、运维或公共接口；
- 权威文档互相冲突，或本文与权威文档冲突；
- 前置阶段或当前步骤没有可验证出口证据；
- 缺少凭证、运行环境、固定上游源码或真实 ERP 状态；
- 测试失败无法定位，审批或执行结果无法确认，Harness 有阻塞漂移；
- 用户批准后输入哈希或工作区发生实质变化。

阻塞报告必须写清：阶段/步骤、已完成内容、未提交 diff、已运行命令及退出码、失败证据、不能继续的原因、可选方案及利弊、推荐方案、用户需要决定的准确问题，以及批准后从哪里继续。

通过源码、官方测试或运行时证据能够唯一确定的技术事实，可以在边界内通过 Spike、ADR、独立 Test 和 Review 收敛；不能把个人偏好包装成唯一技术事实。

## 9. Phase 0 — Governance 收尾

必读：全部 Harness 权威文档和本计划变更 proposal。

- **P0.1 计划入口**：落库本文；更新 `AGENTS.md`；登记 Harness manifest、source index 和中文开发日志。附加 Skill：`harness-update`。
- **P0.2 治理验证**：验证 manifest、结构、引用、drift、文档语义、Skill 路由、未决项覆盖和用户临时文件保护。附加 Skill：`harness-check`。

出口证据：本文和 AGENTS 入口已提交；manifest 有效；引用与 drift 为零；产品命令继续诚实标为 `UNRESOLVED`；没有创建业务代码；独立审查确认无权威冲突。

## 10. Phase 1 — ERP 基线与业务考古

必读：PRD、ARCHITECTURE、DEVELOPMENT、TESTING、ACCEPTANCE、SPEC 的 Phase 1/未决项、固定候选版本的官方源码与测试。

- **P1.1 候选环境**：根据官方证据建立未修改的 Bench、Frappe v16、ERPNext v16、MariaDB 和 Redis 候选环境；记录实际依赖和命令，不提前声称固定版本。Skill：依赖选择使用 `ponytail full`。
- **P1.2 确定性数据**：建立幂等的测试公司、Supplier、Item、Warehouse、需求和采购主数据，以及可重复清理步骤。Skill：脚本和测试使用 `ponytail full`。
- **P1.3 人工 P2P**：跑通 MR → PO → Receipt → Invoice，观察 Payment 状态，保存输入、步骤、权限、单据名、最终状态和失败证据。
- **P1.4 源码地图**：定位相关 DocType、controller、permission、Workflow、官方测试和业务不变量；区分源码事实、运行观察和推断。
- **P1.5 固定基线**：P1.3 通过后才固定完整 commit pair；形成 ADR、权限/Workflow 基线和验证命令。版本固定触发独立对抗审查和 Harness 文档同步授权。

出口证据：基线可从干净环境复跑；上游 diff 为零；不存在 Synora 业务写入代码；核心对象、转换、权限和失败路径能引用源码、官方测试或运行证据解释。

## 11. Phase 2 — Typed 只读 ERP Gateway

必读：Phase 1 证据、ARCHITECTURE 的信任/数据/依赖边界、SPEC 5/9/16/17、PRD F-002。

- **P2.1 最小工程骨架**：创建根目录可安装 Frappe App、独立 `services/agent_runtime` Python 边界和最小锁定工具链；实际跑通后登记 format、lint、type、unit、integration 和 runtime 命令。Skill：`ponytail full`。
- **P2.2 身份授权 Spike**：验证 Frappe 登录态、服务端 Run 引用和 Runtime 调用的用户绑定方式；提交安全 ADR 选项，用户批准后才实现。
- **P2.3 Gateway 契约**：建立版本化 typed input/output/error、风险分类、授权范围、分页、超时、限制、快照和 correlation；未知类型和字段 fail closed。
- **P2.4 只读工具**：按独立增量实现 Item、Supplier、projected stock、open demand、open MR、open PO；每个工具都先找固定上游源码/测试，再写契约和真实集成测试。
- **P2.5 Runtime 边界**：建立 typed HTTP client 和架构测试，机械证明 Runtime 无 MariaDB、ERP 表、ERP 内部 import、任意 URL 或任意工具路径。
- **P2.6 真实验证**：覆盖权限拒绝、跨公司、分页、超时、停用对象、取消单据、缺字段和版本差异。

出口证据：全部 read tool 契约、真实 ERP 集成、权限和架构测试通过；实际命令进入 DEVELOPMENT；Runtime 没有 ERP 数据库或内部 import 路径。

## 12. Phase 3 — 只读采购 Agent

必读：PRD F-001/F-003、DESIGN、SPEC 的状态机/RAG/Phase 3、Phase 2 契约和原始测试证据。

- **P3.1 产品与前端决策包**：调查 Goal 限制、默认范围、固定 Frappe 组件、浏览器、可访问性和双语术语；可发现事实先取证，产品选择交用户批准。
- **P3.2 Agent Run**：实现发起人、授权范围、目标、缺失条件、确定性状态机及 New Run/Runs 的空、加载、无权限、失败、取消和历史状态。
- **P3.3 确定性采购分析**：实现库存、需求、在途采购、重复采购、UOM、日期和 UNKNOWN/NEEDS_INPUT；LLM 不处理数量、金额和阈值计算。
- **P3.4 Provider 基线**：建立 provider 接口、CI 确定性响应和同一数据集模型评测；远程数据、成本或安全变化先交用户决定。
- **P3.5 单 Agent 只读计划**：实现目标理解、受限上下文、tool allowlist、可解释结果、来源、未知和失败恢复，不产生可执行写入。
- **P3.6 FTS5 与工作流 Spike**：建立 curated source 和 SQLite FTS5/BM25 基线；验证 checkpoint/resume。只有 interruption/approval/resume/reconciliation 的实测需要成立时才采用 LangGraph。
- **P3.7 安全评测**：固定并运行正常、歧义、无权限、tool failure、恶意目标、恶意 ERP 字段、检索注入和完全无写入场景。

出口证据：单 Agent 和 FTS5 原始基线可复跑；相同输入产生确定性业务计算；所有写工具均不可达。

## 13. Phase 4 — Proposal、审批与首批写入

必读：PRD F-004–F-008、SPEC 7–11、DESIGN 高风险交互、第一受控写入验收、固定 Workflow/权限证据。

- **P4.1 治理记录**：实现版本化 ProposedAction、Approval Decision、Execution Receipt、digest 和合法状态转换；未知和非法状态 fail closed。
- **P4.2 决策门禁**：严格实现 schema → identity → permission → deterministic checks → Workflow/policy → risk/snapshot/expiry/digest 的顺序，并在执行前全部重检。
- **P4.3 MR Draft**：先独立交付 MR Draft 的提议、显式确认、幂等预留、ERP controller 创建、读回和 Receipt；同一 idempotency key 与同一 digest 返回已有已验证结果，不同 digest 返回冲突且不得执行。
- **P4.4 故障与对账**：实现失败分类、响应丢失、`RECONCILIATION_REQUIRED`、查询证据、人工接管和完整 audit correlation，禁止盲重试。
- **P4.5 Approvals/Audit UI**：展示真实后果、证据、风险、快照、过期、拒绝、要求修改、失败和权限过滤。
- **P4.6 PO Draft**：只有 MR Draft 全部门禁通过后，才能以独立增量交付 PO Draft 的同等治理闭环。

出口证据：无权限、schema 错误、过期、状态漂移、重复和结果不确定全部安全失败；MR/PO Draft 真实 ERP 场景、读回、Receipt、对账和审计通过。

## 14. Phase 5 — 完整 P2P 生命周期

必读：PRD F-009–F-012、SPEC Phase 5、完整 P2P 验收、固定 ERP 源码地图和 Phase 4 治理证据。

每个子里程碑必须单独执行“源码证据 → 契约与审批 → 实现 → 幂等与恢复 → UI → 真实 ERP 测试 → L2 独立 Test 或 Review”，不得合并提交；完整对抗审查留在阶段出口：

- **P5.1 PO Submit**：不同于发起人的授权审批人、当前状态、Workflow、提交影响、幂等和恢复；
- **P5.2 Purchase Receipt**：部分收货、库存状态、前置单据、取消、重复、响应丢失和恢复；
- **P5.3 Purchase Invoice**：部分开票、税务/会计校验、前置单据、取消、重复和恢复；
- **P5.4 Payment 相关流程**：会计权威、职责分离、状态、受控操作、对账和审计；
- **P5.5 全流程 E2E**：完整 P2P、部分业务、跨单据状态漂移、人工接管和失败恢复。

出口证据：F-009–F-012 各自拥有实现、权限、会计、审批、幂等、恢复和真实 ERP 验收证据，没有任何阶段被静默删除。

## 15. Phase 6 — Contextual ERP Coach 与 RAG 演进

必读：PRD F-013/F-014、SPEC 12、Retrieval Acceptance、Phase 3 FTS5 原始数据。

- **P6.1 知识来源**：建立带 source type、path/URL、revision、ERP version、permission scope 和 ingestion time 的 ERP 知识及模拟公司 SOP 摄取，索引必须可重建。
- **P6.2 ERP Coach**：实现基于当前页面、单据、角色和错误的引用、拒答、来源冲突、未知和权限过滤；检索文本永远是数据，不是指令。
- **P6.3 FTS5 基线**：固化同一数据集的 recall、ranking、groundedness、refusal、injection、version isolation、permission 和 latency 原始结果。
- **P6.4 后续准入**：依据基线向用户提交 vector threshold；批准后按 embedding → vector → hybrid → rerank → compression 顺序逐项实验。
- **P6.5 采用决定**：候选技术只有在同一数据集有可测净收益且无治理回退时采用；无收益时记录“不采用”的 ADR 仍可完成阶段。

出口证据：引用、拒答、权限、版本隔离和 Prompt Injection 有可复跑证据；所有已采用检索层都在同一评测集上产生可测净收益且没有治理回退。

## 16. Phase 7 — Multi-Agent 条件评估

必读：PRD F-015、SPEC 13、Phase 3/6 单 Agent 原始评测和治理证据。

- **P7.1 准入阈值**：用现有数据形成质量、安全、延迟、成本、trace、恢复和复杂度阈值决策包，用户批准后才实验采用。
- **P7.2 隔离实验**：分别评估 Procurement Planner、Policy/Compliance Reviewer、ERP Coach、Reconciliation Agent；使用 typed state/event、handoff schema、tool allowlist、deadline、maximum steps、cancellation 和 loop detection。
- **P7.3 A/B 对比**：每个角色与同一单 Agent 数据集比较，保留原始输入、输出、错误、成本和运行环境。
- **P7.4 采用或拒绝**：只采用产生净收益且不削弱 Gateway、policy、approval、idempotency 和 audit 的角色；否则保留单 Agent并记录证据。自由对话式 swarm 永久禁止。

面试与学习补足路线：Phase 3 先保存单 Agent 的正确率、安全、延迟、Token、工具调用和恢复基线；Phase 7 优先实验 `Procurement Planner → Policy Reviewer` 两角色协作，重点练习 typed handoff、共享状态、角色工具隔离、冲突裁决、超时和失败恢复。目标是形成真实 A/B 证据，不为展示而堆 Agent 数量。

出口证据：每个候选角色都有可复跑的采用或拒绝结论；ERP Gateway 和治理边界没有被替换。

## 17. Phase 8 — Hardening 与最终验收

必读：全部 PRD、架构、设计、SPEC、开发、测试、验收、Roadmap、ADR、开发日志和原始评测证据。

- **P8.1 追踪审计**：逐项核对 F-001–F-015 的阶段、实现、测试和验收链接；缺口只能补齐或明确阻塞，不能降低要求。
- **P8.2 故障和安全演练**：运行权限攻击、Prompt Injection、状态漂移、重复、超时、响应丢失、secret leakage、恢复、对账和人工接管。
- **P8.3 Benchmark**：用固定版本、固定数据和固定步骤比较人工流程与 Agent 流程，保存页面跳转、用户输入、审批次数、完成时间和原始结果，不推导未经支持的数字。
- **P8.4 文档与发布边界**：同步实际命令、架构、测试、验收、feature dossier、许可证/NOTICE 和中英文 README；调用 `readme-writer`，禁止超出证据的公开声明。
- **P8.5 最终审查**：运行全量自动化与人工验收、Harness 健康检查、阶段复杂度审计和独立发布级对抗审查。

出口证据：最终 Review=`PASS`；上游无改动；有限安全套件 100% 通过；所有公开声明可复跑；没有影响已验收范围的阻塞未决项。除非用户另行明确授权，不发布、不打 Tag、不部署，也不声称生产采用。

## 18. 阶段出口报告

每个阶段完成后必须停止，并用大白话提交：

- 完成的步骤、需求和用户可见结果；
- 提交列表和文件边界；
- 实际运行的命令、退出码和原始证据位置；
- 按 L1/L2/L3 实际采用的测试与审查结论；
- ERP 上游是否保持干净；
- 未运行检查、限制、未决项和被拒绝的技术；
- 可重复人工验收步骤；
- 下一阶段编号和为什么尚未开始。

阶段报告不能用“应该可以”“理论上通过”替代实际证据。
