# Synora 项目严格执行计划

状态：`CONFIRMED` 执行计划。版本：`PLAN-MAP-v1`。

## 1. 目的、权威与边界

本文告诉 Coding Agent 项目应当按什么顺序推进、每一步怎样取证、何时调用 Skill、怎样测试和审查、什么情况下必须停下。它是 `AGENTS.md` 之后的第二必读文档，但不是产品、架构或验收事实源。

阅读和执行顺序固定为：

1. 根目录 `AGENTS.md`；
2. 本文；
3. `docs/项目方向纠偏.md`；
4. 本文为当前阶段指定的权威文档、上阶段证据、ADR、源码和测试；
5. 当前增量直接涉及的实现文件。

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
- Phase 0–Phase 3 的出口证据已经形成；Phase 3 交付的是只读采购 Agent，出口复核已通过；
- Phase 4 已完成并由最新开发日志和启动包记录 `COMPLETED / PASS`；其只读执行内核、Trace、评测和安全证据以 `docs/development-log/20260826-Phase-4-开发日志.md`、`docs/phase4-kickoff.md` 和提交历史为准；
- Phase 5 已完成 P5.1–P5.5 的实现增量，当前阶段状态为 `COMPLETED / PASS`：用户明确授权 Agent 全程接管代码编写，本阶段不创建 Assignment、不写学习笔记；阶段仍禁止 ERP 写工具，工作流 checkpoint 不得成为 Frappe/ERP 事实。真实 Frappe app-test、本地 Runtime 重启/恢复、对照证据、登录态浏览器全路径和 n8n LAB_ONLY import/execute/audit 验收已形成；n8n 官方 audit 对允许的 loopback HTTP Request 报告通用风险提示，已保留为取舍证据且不进入业务 Runtime；最终独立对抗审查第二轮为 `PASS`（第一轮发现的终态陈旧 checkpoint P1 已修复并复验）；Harness managed/source fingerprint drift 已按 `P5-HARNESS-CLOSE-20260827-v1` 同步并通过校验；该阶段边界已闭合，Phase 6 另按本计划的受治理写入门禁执行；
- 项目定位为“Agent 开发岗位学习仓库 + 真实 ERP 实践载体”；业务应用层与教学实验层共存于同一仓库和开发主线，不是两个仓库或长期分支；
- `approval-workflow-mapping` 已按 ADR-0007 和固定 `dev.localhost` bench 的只读证据闭环：当前无 active Workflow、Server Script 或 hooks；Buyer/Approver/Receiver 与 Company-A scope 的 MR/PO 读写矩阵符合预期，Viewer/Accountant 不具备写权限。更严格或无法验证的企业 Workflow 仍优先并 fail closed；Phase 6 仅在该固定映射下开放受治理 MR/PO Draft。
- Frappe/ERPNext 是只读上游依赖，不能修改其核心，也不能绕过其权限、校验、Workflow、事务和审计；
- 当前工作应由最近开发日志、提交历史、测试输出、运行证据和阶段出口条件共同判定。

本文不维护逐步骤复选框。Agent 必须通过提交历史、开发日志、ADR、测试输出、运行证据和阶段出口条件判断进度，不能仅凭文件存在或前任 Agent 的自述判定完成。

阶段选择算法：

1. 读取最近的开发日志、提交历史和工作区状态；
2. 按 Phase 0 到 Phase 13 顺序核对出口证据；
3. 第一个缺少完整出口证据的阶段就是“下个阶段”；
4. 阶段内从编号最小且缺少验收证据的步骤继续；
5. 上一步未通过时不得开始下一步，上阶段未通过时不得开始下一阶段。

## 3. 用户阶段指令的机械解释

### 新 Codex 对话的首轮启动契约

“新对话”是新的 Codex 对话，不是新的 ChatGPT 对话。只要当前对话绑定本仓库工作区，首轮处理任何项目任务前必须先读取根目录 `AGENTS.md` 和本文，并按全局 `codex-with-chatgpt` 官方流程查询本工作区的已保存会话和连接状态：

1. 已存在长期会话和有效连接时，直接复用它们；不得新建 ChatGPT 会话、连接器或项目级替代协议，也不得要求用户再次批准同一连接。
2. 已保存会话或连接不可用时，先执行官方 doctor/reconnect 恢复流程；只有官方流程明确需要登录、验证码、双重验证、配对或外部授权时，才向用户提出一次性动作请求。有效连接不重复索要配对码。
3. 当前消息匹配下一节的阶段指令且处于计划模式时，首轮直接生成阶段细化计划；计划获准切换执行模式后，在同一阶段任务中启动官方 C2C `INIT`，等待 ChatGPT 的 `PLAN` 后才执行第一条原子任务。不得把“没有看到用户再次提示 Skill”当作跳过条件。
4. 若新 Codex 对话没有绑定本仓库工作区，不能假定本契约已加载；先按宿主的项目工作区选择完成绑定，再重新处理阶段指令。不得在未加载本项目规则时修改代码或伪造阶段进度。

### “开始推进 Phase X”或“开始完成阶段 X”

Codex 必须把两种说法机械解释为同一流程，用户无需再提示 Skill、拆解方式或协作步骤：

1. 核对 Phase X 的前置阶段、当前 Git 状态、最近日志和出口证据，从最早缺少证据的步骤开始；
2. 读取 Phase X 指定的 PRD、SPEC、架构、设计和上阶段证据；
3. 在计划模式生成阶段细化计划，明确目标与禁止范围、PLAN 步骤、顺序与依赖、输入输出、风险等级、停止条件，以及出口所需测试、真实集成和审查证据；
4. 在阶段计划中明确：计划获准并进入执行模式后必须自动调用全局 `codex-with-chatgpt`；
5. 进入执行模式后自动启动官方 C2C 流程，不再询问用户是否调用 Skill；由 ChatGPT 拆解并逐条签发原子任务 Prompt，Codex 顺序执行到阶段出口或停止条件；
6. 阶段出口通过后提交阶段报告并停止，不得自动进入 Phase X+1。

用户不需要再说“使用 codex-with-chatgpt”“把步骤拆成原子任务”“让 GPT 决策”“为 Codex 生成执行 Prompt”“执行后回传 GPT”或“让 GPT 生成独立审查方案”；这些都是阶段推进的默认义务。计划模式只授权制定计划，切换到执行模式后自动启动 C2C 和实施，不把模式限制转化为新的流程确认问题。

### “开始完成下个阶段”

- 使用阶段选择算法确定编号最小的未完成阶段；
- 明确告诉用户识别出的阶段及证据；
- “开始推进下个阶段”与本指令同义；确定 Phase 后按“开始推进 Phase X”的规则执行。

### “继续工作”

- 重新检查工作区、输入文件哈希、最近开发日志、未提交 diff、已有测试证据、已有 C2C 任务/长期会话和用户刚批准的决定；
- 以前次阻塞点为候选断点，但不得盲信旧状态；
- 证据未变化且批准范围清晰时从最近安全断点继续，不新建重复会话也不要求用户重述流程；证据变化时重新生成 Context Receipt，并把变化交 ChatGPT 决定新的执行边界。

## 4. 精简增量闭环

一个增量交付一个用户能解释、能验证、能回滚的结果。默认重心是业务代码和测试，不为流程本身制造额外文件。

### 4.1 十行内 Context Receipt

编辑前用不超过十行说明：当前阶段/任务、业务目的、已知与未知、文件边界、主要风险、最相关自动化检查和人工验收。只有身份、权限、金额、写入、架构选择或权威冲突需要展开详细事实。可从源码或运行环境查到的事实先查，不抛给用户。

### 4.2 风险分级验证

| 等级 | 适用范围 | 必须完成 |
| --- | --- | --- |
| L1 普通 | 普通业务代码、只读页面、文案、局部重构 | Execute 自测、相关自动化检查、`ponytail-review` |
| L2 关键 | 身份、权限、金额、状态机、ERP 集成、幂等、审计、安全边界 | L1 + 一个独立 Test 或 Review；同时跨多个边界时才两者都用 |
| L3 出口/发布 | 阶段出口、发布、Tag、版本或依赖基线、固定 ERP/Frappe 版本 | 全量相关检查、阶段复杂度审计、阶段 Rubric/风险评估、独立对抗审查、Harness 健康检查 |

独立角色只检查，不顺手修代码。阶段实现完成后，Execute 必须自动提供最终 diff、需求、测试和运行证据给独立角色；审查结果为 `PASS` 才能形成阶段出口报告。`CHANGES_REQUIRED` 必须回到 Execute 修正并复验，`BLOCKED` 必须停下交代阻塞，不能用编码 Agent 自评替代独立审查。任何等级都不能省略信任边界校验、数据安全、必要错误处理和可访问性。

### 4.3 文档与提交

- 实现、最小充分测试和必要契约文档放在同一增量；不为普通小修复单独同步 Harness/README。
- 每个 commit 前只更新当前 Phase 的一份开发日志，在文件顶部追加一轮；格式见 `docs/development-log/README.md`。
- 日志写业务结果、真实测试、手工验收、风险和必要的 Assignment/学习说明，不复制完整 diff 或 Agent 对话；只有用户明确要求记录时才保留问题、阻塞或反馈原文。
- 使用一个小而完整的 Conventional Commit；不推送、不发布、不改写历史。
- 阶段出口完成后停止，不自动进入下一阶段；出口前必须完成本节 4.6 的 Rubric、风险登记和独立对抗审查。

### 4.4 实习生协作模式

- 每个步骤必须先给用户一个小而完整的岗位练习 Assignment；适合学习且风险可控的任务优先交给用户，不把整条业务链或安全门禁交出去。
- 布置前说明业务场景、为什么需要、代码入口、输入输出、完成标准、建议 test case 和不应修改的边界。
- Assignment 还必须包含提示梯度（先给方向，再给入口，再给局部示例）、预期耗时和 2–3 个面试追问；用户未完成时记录“待练习”，不得默认为用户已掌握。
- 可在练习入口添加 `TODO(learning)` 结构提示；练习完成后应删除或关闭，不能成为无人负责的生产 TODO。
- 用户实现期间，Agent 默认只解释、给提示、审查和帮助定位；用户明确求助、任务确实超出能力或安全关键路径受阻时才接手。
- 每轮交付先回答五个大白话问题：解决什么业务问题、用户看到什么、数据怎样流动、最重要的三个文件、怎样手工验证。
- 用户明确要求记录的关键困惑、问题和阻塞点才逐字写入当前 Phase 日志，并在其后记录证据、解释、结论和复习动作；阶段结束时基于真实工作定制一组项目/技术栈/Agent 开发问答并引导用户作答。

### 4.5 成本预算与停止纠偏

- **按用户结果提交**：默认一个步骤只有一个主业务提交，页面/API/测试一起形成可验收结果；确有独立安全修复或用户决定门禁时才能增加提交。
- **最小阅读包**：默认只读当前 PLAN 步骤、相关 PRD/SPEC 小节、直接实现文件和相邻测试。只有 ERP 行为不明、架构冲突或测试失败无法解释时才扩大到上游源码和更多权威文档。
- **分层测试**：开发中先跑最相关单测；提交前跑受影响的 format/lint/type/unit；真实 ERP integration/E2E、Harness 全检和全量回归留给受影响的 L2 边界或阶段出口。
- **审查上限**：L1 不调用子 Agent；L2 默认最多一个独立 Test 或 Review；同一问题最多两轮，第三轮仍失败时停止调用并提交根因、证据和下一步，不靠堆 Agent 碰运气。
- **30 分钟停止线**：连续 30 分钟没有业务代码、可运行测试、已确认决定或明确阻塞证据，必须暂停并回答“当前用户结果是什么、还缺什么、哪一步没有产生价值”，随后缩小范围或报告阻塞。
- **文档比例提示**：普通实现若文档改动明显多于业务代码与测试，需要在日志解释；比例是跑偏提示，不是删除必要契约、安全说明或验收证据的理由。
- **成本记账**：每次正常开发日志更新时，记录该增量的大致耗时、Token（宿主可见时）、子 Agent 数、测试命令/次数，以及业务代码、测试、文档的改动量。数据只用于发现浪费，不作为跳过质量门禁的授权。
- **Phase 3 评估**：P3.2–P3.7 每步默认最多一个主业务提交和一次必要 Harness 同步（优先阶段出口）；阶段出口用日志比较新旧流程的耗时、Token、审查次数和可见业务结果，再决定是否继续收紧。阶段出口必须按 4.6 打分，并自动执行 4.7 的独立对抗审查。

预算超出不等于任务失败，但 Agent 必须在继续消耗前说明原因。权限、金额、ERP 写入、幂等、审计、安全、真实集成和阶段出口验证永远不因预算取消。

### 4.6 阶段评估 Rubric 与风险标准

阶段评估不是“测试全绿”的同义词。每个阶段出口都要对以下 9 个 Agent 开发维度逐项打分，并链接真实证据；不适用的维度必须写明原因，不能用 `N/A` 抬高平均分。

| 维度 | 评估问题 |
| --- | --- |
| D1 需求与业务正确性 | 用户目标、范围、边界条件和确定性业务结果是否与 PRD/SPEC 一致？ |
| D2 身份、权限与范围 | 当前用户、公司/仓库范围、角色和跨用户隔离是否由服务端重检？ |
| D3 状态、并发、幂等与恢复 | 状态转换、CAS、重复请求、取消/超时/部分失败是否安全？ |
| D4 Agent 信任与成本 | Prompt/检索/模型输出是否不可信处理，数量是否确定性，模型/Token/回退/额度是否可审计？ |
| D5 安全与数据保护 | Secret、注入、SSRF、XSS、敏感错误、上游只读边界是否有证据？ |
| D6 UI、可访问性与双语 | 加载/空/失败/权限/键盘/aria/表格语义和双语文案是否验收？ |
| D7 测试、真实集成与复现 | 相关单测、真实 ERP/HTTP、负面场景、命令和原始证据是否可复跑？ |
| D8 治理、追踪与非虚构 | 日志、ADR、Harness、README、延期项和公开声明是否与实现一致？ |
| D9 简洁性与可运维性 | 是否避免不必要抽象，日志/指标/超时/配置/升级路径是否可理解？ |

评分采用 0–4：`0=无证据或未知`、`1=失败/重大缺口`、`2=部分通过且有未闭环风险`、`3=达到当前阶段要求`、`4=有冗余证据并具备可复用基线`。阶段出口至少满足：所有适用维度已评分；D1/D2/D3/D5/D7/D8 均不低于 3；适用维度平均分不低于 3.0；没有 P0/P1 未关闭；P2 必须有明确 owner、下一阶段门禁和复验条件；P3 进入改进清单。

风险同时记录 likelihood 和 impact，各取 1–4：likelihood `1=理论可能、2=边界可复现、3=常见路径可复现、4=正在发生/易被利用`；impact `1=文案或局部体验、2=可控功能退化、3=业务错误/权限或成本影响、4=数据损失/Secret 泄露/未授权 ERP 写入/审计失真`。风险分为 `likelihood × impact`，并按下列门禁处理：

- `P0 Critical`：数据损失、Secret 泄露、未授权外部写入、严重权限绕过或不可审计的真实伤害；立即停止并回滚/隔离。
- `P1 High`：分数 12–16，或涉及身份、权限、状态、幂等、财务/ERP 写入、安全边界且存在可信利用路径；阶段出口前必须修复并复验。
- `P2 Medium`：分数 6–11，影响被限制但会妨碍扩展、运维或证据可信度；可以延期，但必须写 owner、期限/下一阶段门禁和复验命令。
- `P3 Low`：分数 1–5 的改进项；进入 backlog，不得伪装成已解决。

### 4.7 阶段出口自动门禁与导师交付

每个阶段完成时按以下顺序执行，不能跳过最后一步：

1. Execute 完成本阶段实现、Assignment 反馈、相关测试、手工验收和开发日志；日志保留真实失败与修复过程。
2. Execute 生成阶段 Rubric、风险登记和阶段报告草稿，列出所有未运行检查、P2/P3 延期项、用户明确要求记录的原话问题和定制问答。
3. 自动启动一个独立对抗审查角色，输入需求、权威文档、最终 diff、测试输出、运行证据和阶段报告草稿；不输入 Execute 的辩护性结论作为唯一依据。
4. 审查 `PASS`：更新阶段日志、Harness/README（若事实变化），提交阶段报告并停止。审查 `CHANGES_REQUIRED`：Execute 修复、重跑受影响检查，再进行最多两轮复查。审查 `BLOCKED` 或第三轮仍失败：阶段状态为 `BLOCKED`，交用户决定，不得进入下一阶段。
5. 导师交付阶段问答：至少覆盖本阶段 5 个真实追问（业务、代码入口、信任边界、失败恢复、取舍/面试追问），先让用户作答，再给逐题提示和参考答案；未作答项保留为 `待练习`。

### 4.8 Codex with ChatGPT 阶段决策闭环

阶段计划获准并进入执行模式后，Codex 按全局 Skill 的官方协议发送 `INIT`。ChatGPT 读取阶段细化计划和工作区证据，把每个阶段步骤拆成单一目的、可验证、可回滚的原子任务，建立有序队列并识别依赖；每次只签发当前任务的完整 Prompt，根据上一任务的真实结果决定下一条指令，不让 Codex 自行选择。

每条执行 Prompt 必须包含：当前步骤与原子任务编号、业务目的与完成结果、已确认事实与代码入口、精确实现要求、禁止修改边界、输入输出/接口/状态变化、Ponytail 最小实现约束、测试/手工验收/证据要求、失败与停止条件，以及完成后必须回报的结果。Prompt 保留在长期 C2C 会话中，默认不生成仓库内持久 Prompt 文件。

Codex 只执行当前 Prompt，不重新规划阶段、不提前实现下一任务，也不擅自改变技术方案、接口、范围或验收标准。所有编辑、命令、测试、Git、恢复和独立角色调度仍由 Codex 完成。发现问题时，Codex 必须停止受影响操作并回传：问题、观察证据、失效假设、已完成与未执行动作、继续风险和需要决定的准确问题；只使用官方 `EXECUTED → REVIEW → PLAN/BLOCKED`，不新增协议状态，也不复制文件、diff 或日志。取得新 `PLAN` 前不得自行改走替代路线。

只有 ChatGPT 返回 `BLOCKED` 且需要改变用户批准的产品范围或权限、权威文档冲突无法用证据消解、需要新的外部写入/推送/发布/破坏性操作授权、需要用户登录/验证码/明确确认，或官方最大迭代数耗尽时，才询问用户。

#### 独立 Review 边界

- C2C 的 `REVIEW` 只是 ChatGPT 读取执行结果并决定下一条指令，不是项目独立对抗审查。
- 阶段实现完成后，ChatGPT 生成独立 Review 执行 Prompt，包含审查范围、攻击面、验证证据、负面场景和判定标准；Codex 用它启动独立只读 Review 角色，角色返回 `PASS / CHANGES_REQUIRED / BLOCKED`，不得修改代码。
- Codex 把正式 Review 结论交回 ChatGPT：`PASS` 时由 GPT 签发收口 Prompt，`CHANGES_REQUIRED` 时签发修复 Prompt，`BLOCKED` 时整理需要用户决定的问题。ChatGPT 不得亲自承担独立 Review，也不得覆盖其结论。
- L2/L3 所需独立 Test、真实 ERP、安全验证和 Harness 检查仍由 Codex 执行或调度。

#### 可选 GitHub Review

项目默认不推送。只有用户明确授权且 Codex 已完成推送后，ChatGPT 才可对其能够访问的 GitHub 仓库或 PR 做补充性只读 Review；不得评论、Approve、Request changes、提交、推送、修改、合并或关闭任何远端对象。C2C 只保证读取本地工作区，GitHub 可见性取决于仓库权限；远端 Review 不替代本地独立对抗审查，也不是默认阶段出口条件。

#### 成本控制

- 一个工作区复用一个长期 ChatGPT 会话；ChatGPT 维护任务队列，Codex 每次只加载当前 Prompt 所需上下文。
- Codex 不重复生成方案、技术比较、调试策略或 Review 判断；控制消息保持简短，具体证据由 ChatGPT 从工作区读取，C2C 结果检查不再复制成另一轮同类 Review。
- 开发日志记录宿主可见的 Codex Token、C2C 迭代数、返工次数和耗时；不可见项明确标记不可见。
- 连续两个可比较增量未降低 Codex 消耗时，暂停下一任务，由 ChatGPT 重新决定任务粒度和 Prompt 质量；不得以成本为由降低测试、真实集成、安全或阶段出口质量。

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
| 阶段计划获准并进入执行模式；或执行问题需要决策 | 全局 `codex-with-chatgpt` | 自动启动并复用本工作区的长期连接器和会话；ChatGPT 只负责决策、原子任务 Prompt 与 Review 方案，Codex 负责所有行动。严格使用官方状态流，不创建项目级 Skill、`.c2c.json`、自定义状态或持久 Prompt 包。 |

纯文档任务不因形式需要调用 Ponytail；代码相关任务不能跳过 Ponytail。

## 6. 需求到阶段的完整坐标

| 需求 | 实施阶段 | 阶段出口必须证明的结果 |
| --- | --- | --- |
| F-001 Agent Run 与目标输入 | Phase 3 | 身份、范围、状态、输入和 UI 正常/异常证据。 |
| F-002 授权上下文与 Typed ERP Tools | Phase 2 | 契约、权限、分页、超时和真实 ERP 集成证据。 |
| F-003 确定性采购风险分析 | Phase 3 | 固定输入的确定性计算和 UNKNOWN/NEEDS_INPUT 证据。 |
| F-004 可解释计划与 ProposedAction | Phase 4 动态规划基线；Phase 6 写入提议 | Tool Calling/Trace 与版本化 schema、证据、冲突、过期、fail-closed 证据。 |
| F-005 Policy / RBAC / Approval | Phase 6 | Workflow、权限、Draft 确认、职责分离和状态重检证据。 |
| F-006 MR Draft / PO Draft 受控执行 | Phase 6 | 真实创建、读回、权限、重复和失败恢复证据。 |
| F-007 Receipt、幂等与对账 | Phase 6 | replay、响应丢失、对账和人工介入证据。 |
| F-008 Audit / Trace / Failure Evidence | Phase 4 建基线；Phase 6 补写入证据 | Action/Observation/stop reason 与 correlation、脱敏、访问控制、写入失败分类证据。 |
| F-009 PO Submit | Phase 10 | 独立审批、当前状态、影响和恢复证据。 |
| F-010 Purchase Receipt | Phase 10 | 部分收货、库存、取消、幂等和恢复证据。 |
| F-011 Purchase Invoice | Phase 10 | 部分开票、税务/会计、取消、幂等和恢复证据。 |
| F-012 Payment 相关流程 | Phase 10 | 会计权威、职责分离、状态、对账和审计证据。 |
| F-013 Contextual ERP Coach | Phase 8 | 引用、拒答、权限、版本、冲突和注入评测。 |
| F-014 完整 RAG 演进 | Phase 8 | FTS5 基线和每个后续候选技术的采用或拒绝证据。 |
| F-015 条件式 Multi-Agent | Phase 9 | 相同数据集 A/B 和每个候选角色的采用或拒绝证据。 |
| F-016 Agent 学习实验与采用证据 | Phase 4–13 | 最小实验、开源对照、Synora 转译、Trace、评测、Adoption Card、Assignment 与面试追问。 |

## 7. 未决项路由

| 未决项 | 最迟解决阶段 | 默认处理 |
| --- | --- | --- |
| `erp-version-pair` | Phase 1 | 通过真实 P2P 基线唯一收敛；固定版本前做独立对抗审查。 |
| `approval-workflow-mapping` | Phase 1 提证，Phase 6 启用写入前完成 | 从固定版本权限和 Workflow 取证；具体企业政策或多级规则交用户决定。 |
| `runtime-user-authorization` | Phase 2 | 做安全 Spike 和 ADR 选项，必须交用户批准后实施。 |
| `product-commands` | Phase 2 | 由实际脚手架和成功命令输出解决，不能从 README 推断。 |
| `frontend-design-baseline` | Phase 3 | 从固定 Frappe v16 取证；浏览器、可访问性和双语术语等产品选择交用户批准。 |
| Goal 长度及默认公司、仓库、时间范围 | Phase 3 | 提交产品决策包，用户批准后固化到 PRD/SPEC。 |
| `model-selection` | Phase 3 | 使用同一评测集比较；涉及远程数据、成本或安全边界时交用户决定。 |
| `workflow-engine-spike` | Phase 3 只读结论已闭环；Phase 5 重评已闭环 | ADR-0004 记录 Phase 3 不需要 LangGraph；ADR-0006 已用中断/恢复、取消、过期、崩溃和同任务对照证据决定手写引擎为主线，LangGraph 保持 `LAB_ONLY`。 |
| 性能、并发、保留期、浏览器与可访问性目标 | 首次受影响阶段 | 先取得基线数据，再由用户批准验收目标，禁止编造数字。 |
| `vector-retrieval-threshold` | Phase 8 | 用 FTS5 原始结果形成阈值决策包，用户批准后才允许技术进入业务主线；教学对照实验不因未采用而删除。 |
| `multi-agent-adoption-threshold` | Phase 9 | 用单 Agent 原始结果形成阈值决策包；业务主线只采用有净收益的角色，教学实验保留全部候选的对照证据。 |
| `third-party-licenses` | Phase 13 前，任何公开发布前必须完成 | 调查 MIT、GPL-3.0、CC BY-NC、NOTICE 和分发边界，实质发布选择交用户批准。 |
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
- **P3.6 FTS5 与工作流 Spike**：建立 curated source 和 SQLite FTS5/BM25 基线。ADR-0004 只证明 Phase 3 同步只读链路不需要 LangGraph；批准的新路线要求 Phase 5 用 interruption/resume/recovery 实验将手写工作流与 LangGraph 重新对比。确定性状态机已定义后续中断/对账转换并被 `tests/test_run_state_machine.py` 覆盖。
- **P3.7 安全评测**：固定并运行正常、歧义、无权限、tool failure、恶意目标、恶意 ERP 字段、检索注入和完全无写入场景。

出口证据：单 Agent 和 FTS5 原始基线可复跑；相同输入产生确定性业务计算；所有写工具均不可达。

## 13. Phase 4 — Agent 执行内核与原生 Tool Calling

启动状态：`COMPLETED / PASS`。最新出口日志已记录完整验证、独立对抗审查和 Phase 4 限制；准备包本身不计作实现证据。

必读：`docs/项目方向纠偏.md` Phase 4、`docs/phase4-kickoff.md`、PRD F-002/F-003/F-008/F-016、SPEC Tool Gateway/评测契约、Phase 3 单 Agent 与安全基线。本阶段不实现 ERP 写入。

- **P4.1 执行契约与评测基线**：`COMPLETED_FOR_CURRENT_INCREMENT`；已定义 Action、Observation、FinalAnswer、Error、StopReason 和 8 个 golden tasks，并建立 Component/Trajectory/Task/System 评测入口；阶段出口仍需全量证据。
- **P4.2 手写模式实验**：`COMPLETED_FOR_CURRENT_INCREMENT`；在 `labs/agent_patterns/` 完成 Direct、bounded ReAct、Plan-and-Solve、Reflection 和采购版 MiniStepAgent 的离线 recorded 对照，并统一记录比较指标；阶段出口仍需全量证据。
- **P4.3 原生 Tool Calling**：`COMPLETED`；provider/native skeleton 已支持六类只读 function definitions、provider call id、tool result message、单调用拒绝并行、allowlist/schema/evidence 校验；smolagents 对照和真实验证已按出口日志记录。
- **P4.4 循环与停止治理**：实现 max steps、相同参数重复、工具频率、无进展、token、成本、wall-clock、cancel 和 final-answer checks，保留明确 stop reason。
- **P4.5 业务接入与 Trace UI**：至少一个真实只读任务必须根据第一次 observation 选择第二个不同工具；页面区分业务结论与可折叠 Trace。

出口证据：Direct/ReAct/MiniStepAgent/原生 Tool Calling 在同一任务集可比较；真实观察驱动了下一步工具选择；循环攻击和超预算请求以可解释 stop reason 终止；数量、金额和阈值仍由确定性代码决定。

## 14. Phase 5 — 持久工作流与 Plan-and-Execute

必读：纠偏方案 Phase 5、ARCHITECTURE Agent Execution Modes、SPEC 状态/恢复契约、ADR-0004、Phase 4 Trace 与失败证据。本阶段仍不开放 ERP 写入。

- **P5.1 typed plan/state**：定义 PlanStep、依赖、状态、clarification、replan reason 和版本兼容。
- **P5.2 手写与框架对照**：比较固定 Workflow、ReAct 子图、Plan-and-Execute、LangGraph，并用 Dify/n8n 复刻一个只读流程作为低代码对照。
- **P5.3 持久恢复**：实现 checkpoint、interrupt、resume、cancel、expiry、crash recovery；checkpoint 不是 Frappe 业务事实。
- **P5.4 竞态与内部边界**：处理并发恢复、取消/过期、Runtime 内部认证、短期 capability 和已完成工具不重放。
- **P5.5 可视化与取舍**：Run 页面展示计划、步骤、观察摘要、中断与停止原因；Adoption Card 记录主线是否采用 LangGraph。

实施状态（2026-08-27）：P5.1–P5.5 代码、真实 Frappe 只读集成、Runtime targeted tests、真实进程重启恢复、手写同任务 comparison、登录态浏览器全路径、n8n LAB_ONLY import/execute/audit、ADR-0006 和 Adoption Card 已提交；最终独立对抗审查第二轮为 `PASS`，阶段出口为 `COMPLETED / PASS`。n8n audit 的 HTTP Request 通用风险提示已原样保留并明确不进入业务主线；Harness managed/source fingerprint drift 已按文件级 proposal 同步，未修改业务代码、README、`.env*`、上游或学习笔记正文。已形成的出口证据覆盖进程重启后从最近安全点恢复、取消/过期后不再调工具、终态陈旧 checkpoint 不再展示和已完成工具不重复执行；Plan-and-Execute 与 ReAct 的同任务质量、成本、恢复对比已由 recorded comparison 形成。

## 15. Phase 6 — 受治理的第一批 ERP 行动

必读：PRD F-004–F-008、SPEC 7–11、DESIGN 高风险交互、第一受控写入验收、固定 Workflow/权限证据。`approval-workflow-mapping` 未闭环时禁止开放写工具。

实施状态（2026-08-28）：P6.1–P6.5 的治理记录、审批门禁、MR/PO Draft、幂等/对账和 Runs 页面增量已完成；固定真实 ERP、故障恢复、浏览器验收、Harness 五项和独立 Test 均通过，最终独立对抗 Review 明确返回 `PASS`。阶段状态为 `COMPLETED / PASS`；业务代码冻结于 `a36197c`，PO Submit、Receipt、Invoice、Payment、后续 P2P 写操作和 generic writer 仍不可达。

- **P6.1 治理记录与映射**：完成 Workflow/Role/Permission 取证，实现版本化 ProposedAction、PolicyDecision、ApprovalDecision、ExecutionReceipt、digest 和合法状态转换。
- **P6.2 决策与执行门禁**：按 schema → identity → permission → deterministic checks → Workflow/policy → snapshot/expiry/digest 执行，写入前全部重检。
- **P6.3 MR Draft**：交付提议、人工确认/审批、幂等预留、ERP controller 创建、read-back 和 Receipt。
- **P6.4 故障与对账**：处理响应丢失、部分失败、`RECONCILIATION_REQUIRED`、人工接管和 audit correlation，禁止盲重试。
- **P6.5 PO Draft 与 UI**：MR Draft 闭环通过后再交付 PO Draft；页面展示后果、证据、风险、快照、过期、拒绝、修改、失败和权限过滤。

出口证据：Buyer 目标 → Agent 调查 → ProposedAction → 人审 → 单次 MR/PO Draft → read-back/Receipt 的真实链路通过；无权限、未审批、过期、状态漂移、重复和结果不确定全部安全失败。

## 16. Phase 7 — Prompt、Context Engineering 与 Skills

必读：纠偏方案 Phase 7、PRD F-016、Phase 4–6 Trace/预算/安全证据。

状态：`COMPLETED / PASS`（2026-08-29）。Phase 7 已完成并停止在本阶段出口；Phase 8 仍未启动，必须等待用户明确启动，不得预先实现或验收 Phase 8 功能。

- **P7.1 Prompt 分层与版本**：区分边界、决策、恢复与输出契约，记录 version/hash 和单变量 A/B。
- **P7.2 ContextBuilder**：实现 Gather/Select/Structure/Compress、JIT context、structured notes、summary 和 token budget，Trace 记录选择与丢失。
- **P7.3 Procurement Skills**：实现版本、来源、渐进披露、自由度、allowed tools 和回归证据；Skill 不得扩展 capability allowlist。
- **P7.4 职责对照**：用同一任务说清 Prompt、Tool、Skill、Workflow 和 MCP 的边界，保留采用/拒绝证据。

出口证据：同一任务可复现 Prompt/Context/Skill 版本；精简上下文后安全不退化且 token 有可测变化；恶意 Skill 不能扩权。

Phase 7 收口证据（CONFIRMED）：

- Prompt A/B 的共同 `boundary`、`recovery`、`output_contract` canonical bytes 完全一致；A hash 为 `1a676172e121c37910512c73b4a77cf3955cad7bca2c659f342d5b2c6e9dbda4`，B hash 为 `49ffea7a309feb53abdd5227e6ec1803646f60eba7940854c912ca8641123572`；B 未形成严格净收益，业务主线保留 A。
- G01 长上下文估算从 `31,417` 降至 `14,861`，G08 从 `31,401` 降至 `14,845`，均低于显式 `16,000` budget；安全层、最新 Observation、evidence digest 和有效 tool schema 均保留。缺失/非法预算和 Provider 实际超限均在 Provider 动作前或动作后安全回退。
- 四个采购 Skill 均完成 manifest、版本、来源、hash、自由度、渐进披露和 allowlist 契约；当前 profile 只启用 `replenishment-analysis` 与 `duplicate-purchase-check`。恶意 Skill 的 Provider 调用为 `0`，写工具 schema 不可见。
- Runtime 全量单元测试 `316 passed`；Frappe app-test `147 tests ... OK`；`make format-check`、`make lint`、`make type`、compileall、`git diff --check` 均通过。
- 固定开发环境的真实只读链路已由 Buyer Run 验证：Frappe → Runtime → recorded OpenAI-compatible Provider → typed read tool → Trace/evidence；Material Request 与 Purchase Order 数量前后不变，ERP 写交互为 `0`。固定上游 SHA 为 Frappe `6a329d068416768ec47ccd3326b9cc95a8d7bf99`、ERPNext `11e0ba0a1c45f217e2e73e885f699102d06da325`，checkout clean。
- 登录态 Runs 页面已验证 Trace 版本、上下文估算/实际 token、Skill 元数据、事件顺序、键盘控件、状态播报、HTML 转义和无原始 Prompt/Skill；Viewer 对该 Buyer Run 的列表、详情和 Trace 请求均返回 `404` 且无目标标识泄露。
- 真实付费模型质量、真实模型 A/B 和生产成本结论未运行；recorded/deterministic 结果只证明工程复现与安全回归，不证明模型质量提升或生产收益。MCP、RAG/Memory、Multi-Agent 和第三方依赖保持 `DEFERRED`。

Phase 7 最终 Rubric：D1 需求与业务正确性 `3`；D2 身份/权限/范围 `4`；D3 状态/并发/幂等/恢复 `3`；D4 Agent 信任与成本 `3`；D5 安全与数据保护 `4`；D6 UI/可访问性/双语 `3`；D7 测试/真实集成/复现 `4`；D8 治理/追踪/非虚构 `4`；D9 简洁性/可运维性 `3`。合计 `31/36`，平均 `3.44`；D1/D2/D3/D5/D7/D8 均不低于 `3`，P0/P1 已关闭。阶段收口文档与 Harness 指纹为两个连续小提交；本次收口无业务代码变更，按用户明确授权不启动独立对抗性审查。

## 17. Phase 8 — Memory、RAG 与 Contextual ERP Coach

必读：PRD F-013/F-014/F-016、SPEC Retrieval/Memory 契约、Phase 3 FTS5 与 Phase 7 Context 基线。

- 实现 Working/Episodic/Semantic/Procedural Memory 的写入候选、审核、scope、过期、纠正、删除、召回和污染防护。
- 用同一语料对比 FTS5/BM25、vector、hybrid 和 rerank；业务主线只采用有净收益的层。
- 实现带引用的 ERP Coach；库存、订单、权限等实时事实必须重查 ERP，不得信任记忆。

出口证据：经审核的经验和 SOP 可带来源召回；跨用户/公司、过期、恶意记忆/检索文本 fail closed；Coach 会重查实时 ERP 事实。

## 18. Phase 9 — Multi-Agent、MCP 与 A2A

- 实现 Supervisor、Peer-to-Peer、Hierarchical、managed-agent-as-tool 和显式 graph node 的最小对照。
- 先评估 `Planner → Policy/Risk Reviewer`，只在异常路径启动 Reconciliation Agent；所有 handoff typed、工具隔离、预算有界。
- 实现本地 MCP server 和最小 A2A Agent Card/Task/status/cancel；ANP 保留概念实验和选型报告。

出口证据：每个候选角色有同任务质量、安全、延迟、成本、恢复对比；只有净收益角色进入业务主线，其他实验保留拒绝证据。

## 19. Phase 10 — 完整 P2P 运营 Agent

按 PO Submit、Purchase Receipt、Purchase Invoice、Payment 相关流程逐项执行“源码证据 → 契约/审批 → 实现 → 幂等/恢复 → UI → 真实 ERP 测试”，覆盖部分收货/开票、取消、会计影响、状态漂移和人工接管。

出口证据：F-009–F-012 各自拥有权限、会计、审批、幂等、恢复和真实 ERP 验收证据，完整 P2P 可运行且无阶段被静默删除。

## 20. Phase 11 — Web/GUI Agent 与多模态观察

实现有界 Web/GUI 实验，覆盖 DOM/视觉观察、元素定位、动作、登录态、异步页面、可访问性和安全；与 typed API tools 比较适用条件。业务主线采用不得绕过 ERP 权限或写入门禁。

## 21. Phase 12 — 自进化、后训练与 Agentic RL

将经审核失败轨迹转为可版本回滚的 Prompt/Skill 候选，并用 held-out eval 筛选；完成 rerank、SFT/DPO、reward design 和 Agentic RL 前置条件的小型离线实验。禁止线上自动改写业务 Prompt、policy、permission 或 tools。

## 22. Phase 13 — AI Infra、系统强化与毕业作品

依据实测需要评估模型路由、缓存、并发、限流、降级、本地推理和部署；运行故障注入、恢复演练、对抗发布审查、人工对 Agent Benchmark 和全系统评测；形成可复跑毕业作品与面试 dossier。除非用户另行授权，不发布、打 Tag、部署或声称生产采用。

## 23. 阶段出口报告

每个阶段完成后必须停止，并用大白话提交：

- 完成的步骤、需求和用户可见结果；
- 提交列表和文件边界；
- 实际运行的命令、退出码和原始证据位置；
- 按 L1/L2/L3 实际采用的测试与审查结论；
- 阶段 Rubric 的 9 维度分数、likelihood/impact 风险登记、P0–P3 关闭或延期证据；
- 独立对抗审查的输入范围、轮次、原始结论和最终 `PASS` 证据；
- ERP 上游是否保持干净；
- 未运行检查、限制、未决项和被拒绝的技术；
- 可重复人工验收步骤；
- 本阶段每个步骤的 Assignment 完成情况、用户明确要求记录的原话问题/卡点及导师解释；
- 基于真实工作的至少 5 个面试问答练习和用户待练习项；
- 下一阶段编号和为什么尚未开始。

阶段报告不能用“应该可以”“理论上通过”替代实际证据。
