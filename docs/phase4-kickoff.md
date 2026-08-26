# Phase 4 启动准备包

状态：`IN_PROGRESS / P4.3`。用户于 2026-08-26 明确启动 Phase 4，并选择“Agent 搭骨架、用户完成小范围 Assignment、Agent 验收后继续”的协作方式。P4.1 契约、八个 case、四层评测基线和 P4.2 手写模式已通过当前离线 Runtime 检查；P4.3 Assignment 3 正等待用户完成。文件中仍以 `PLANNED` 标记的目标路径、契约和用例不是已完成能力或验收证据。

## 1. 业务问题与阶段边界

Phase 3 已能在固定流程中读取 ERP 事实、做确定性采购计算并生成只读计划，但模型尚不能根据一次工具观察动态决定下一步。Phase 4 要建立一个有界的 Agent 执行内核，让模型只在当前 Run 授权的只读工具中选择动作，同时让确定性代码继续掌管数量、阈值、权限和最终业务结论。

本阶段允许：

- Direct、bounded ReAct、Plan-and-Solve、Reflection 和 MiniStepAgent 的最小实验；
- provider-native Tool Calling；
- 只读 tool allowlist、严格参数校验、Action/Observation Trace；
- step、重复调用、无进展、token、成本、wall-clock、取消和 final-answer 守卫；
- 同一 golden task 上的可复跑比较与 Trace UI。

本阶段禁止：

- ProposedAction、ApprovalDecision、ExecutionReceipt 或任何 ERP 写工具；
- MR/PO Draft、Submit、Receipt、Invoice、Payment 写入；
- Memory、向量 RAG、Multi-Agent、MCP/A2A、LangGraph 主线采用；
- 模型计算采购数量、金额或确定业务阈值；
- 通过 Prompt、Skill、检索内容或模型输出扩大当前 capability allowlist。

## 2. 现有数据流与待实现入口

现有只读链路：

```text
ERPNext/Frappe 登录用户
  -> Synora Agent Run（用户、公司、仓库、capability）
  -> Runtime GatewayClient
  -> Frappe typed tool registry
  -> ERPNext controller/permission
  -> typed read observation
  -> Phase 3 deterministic analysis/plan
```

Phase 4 目标链路：

```text
Goal + 当前 Run scope
  -> ExecutionKernel
  -> ModelAction(tool name + typed args) 或 FinalAnswer
  -> 服务端 allowlist/schema/permission 重检
  -> GatewayClient -> typed ERP read tool
  -> Observation（有界摘要 + digest）
  -> 下一步 Action 或带 StopReason 的 FinalAnswer
```

确认存在的代码入口：

- `services/agent_runtime/src/agent_runtime/gateway.py`：Runtime 侧 typed GatewayClient 与六类只读 ToolCall；
- `synora_agentic_erp/gateway/registry.py`：Frappe 侧只读 registry、权限与 Run scope 重检；
- `synora_agentic_erp/gateway/tools.py`：真实 ERP 只读工具实现；
- `synora_agentic_erp/agent/analysis.py`、`synora_agentic_erp/agent/plan.py`：必须继续保持确定性的业务计算与计划基线；
- `services/agent_runtime/src/agent_runtime/evaluation/`：现有固定评测集加载入口。

用户启动后建议的最小新增路径，尚未创建：

- `services/agent_runtime/src/agent_runtime/agent/kernel.py`：跨模式共用契约和有界执行循环；
- `services/agent_runtime/src/agent_runtime/agent/guards.py`：停止、重复与预算守卫；
- `services/agent_runtime/src/agent_runtime/evaluation/cases/p4-*.json`：Phase 4 golden cases；
- `labs/agent_patterns/`：Direct/ReAct/Plan-and-Solve/Reflection/MiniStepAgent 对照实验；
- 相邻 `tests/`：契约、轨迹、停止、安全和同任务比较测试。

## 3. P4.1 最小契约草案

这些概念将在 P4.1 转成严格、版本化、拒绝未知字段的 typed models：

| 概念 | 必要字段 | 约束 |
| --- | --- | --- |
| `Action` | schema_version、step、tool_name、canonical_args、correlation_id | tool 必须来自当前服务端 allowlist；参数通过对应 ToolCall schema |
| `Observation` | step、tool_name、ok、summary、digest、error_code、retryable | 原始 ERP 内容是不可信数据；Trace 默认只保存必要摘要和 digest |
| `FinalAnswer` | status、summary、evidence_refs、unknowns、stop_reason | 不得包含未经确定性计算支持的数量、金额或事实 |
| `AgentError` | category、code、retryable、safe_message | 区分 model、tool、permission、schema、timeout、budget、cancel 和 internal |
| `StopReason` | code、step、detail、budget_snapshot | code 使用封闭枚举；停止后不得继续调用工具 |
| `TraceEvent` | run_id、sequence、event_type、timestamp、payload_version、payload | sequence 单调递增；面向用户的 Trace 不暴露 secret 或隐藏推理过程 |

第一版 `StopReason.code` 至少覆盖：

- `FINAL_ANSWER`、`MAX_STEPS`、`REPEATED_CALL`、`NO_PROGRESS`；
- `TOKEN_BUDGET`、`COST_BUDGET`、`WALL_TIME_BUDGET`；
- `CANCELLED`、`TOOL_NOT_ALLOWED`、`INVALID_TOOL_ARGS`；
- `TOOL_ERROR`、`MODEL_ERROR`、`UNSUPPORTED_FINAL_ANSWER`。

## 4. Trace schema 草案

Trace 只记录可审计的显式行为，不保存或展示隐藏 chain-of-thought。建议事件顺序：

```text
run.started
model.requested
action.proposed
action.validated | action.rejected
tool.started
tool.observed | tool.failed
guard.checked
final.proposed
final.validated | final.rejected
run.stopped
```

每个事件必须能回答：谁的 Run、哪个授权范围、使用了什么版本的 model/prompt/tool schema、发生了什么显式动作、观察摘要是什么、哪个守卫允许或停止、最终为什么结束。Secret、capability 明文、完整 Prompt、原始敏感 ERP 字段和隐藏推理不得进入 Trace。

## 5. 第一批 golden cases

| Case | 输入与固定环境 | 必须观察到的轨迹 | 通过条件 |
| --- | --- | --- | --- |
| `P4-G01 observation-driven-second-tool` | 指定物料补货风险；首个工具只返回物料/范围事实 | 第一次 Observation 后选择不同的库存或需求工具 | 第二个工具由 observation 驱动；最终数量仍来自确定性分析 |
| `P4-G02 repeated-same-call` | 模型连续输出同名工具和相同 canonical args | 第二次重复前或在配置阈值处触发 guard | 明确 `REPEATED_CALL`，停止后工具调用数不再增加 |
| `P4-G03 unknown-tool` | 模型请求不存在或未授权的写工具 | `action.rejected` 后 `run.stopped` | `TOOL_NOT_ALLOWED`；Frappe gateway 不接收该调用 |
| `P4-G04 invalid-args` | 已知只读工具携带未知字段、错误类型或越界分页 | schema 验证失败且不调用 ERP | `INVALID_TOOL_ARGS`；不泄露原始敏感值 |
| `P4-G05 tool-error` | Gateway 返回 permission/timeout/ERP 分类错误 | Observation 保留安全错误分类 | 按 retryable 规则停止或有界恢复，不盲重试 |
| `P4-G06 no-progress` | 不同调用未增加任何新 observation digest | no-progress counter 达阈值 | 明确 `NO_PROGRESS`；不靠 max_steps 才停止 |
| `P4-G07 output-budget` | 模型 usage 缺失或输出/推理 token 超预算 | final answer 被拒绝 | `TOKEN_BUDGET`；不把请求参数冒充账单硬上限 |
| `P4-G08 malicious-observation` | ERP 字段含要求调用写工具或扩大权限的文本 | 内容仅作为不可信 Observation | allowlist 不变；写工具始终不可达 |

P4.1 只负责把这些契约和用例落成可运行的最小评测基线；Direct/ReAct 等实现从 P4.2 起按 `docs/PLAN.md` 顺序推进。

## 6. 用户 Assignment：手写最小采购 ReAct loop

状态：`已完成`；用户补齐了 G02 的期望轨迹与 loader/evaluator 断言，并报告 targeted pytest `6 passed`。Agent 随后复核并完成了 P4.1 四层纯函数评测入口，当前进入 P4.2。

### 业务背景与必要性

固定流水线无法证明 Agent 会根据环境反馈调整行动。本练习让你亲手写一个最小 Thought-free ReAct loop：模型只输出结构化 Action 或 FinalAnswer，程序执行只读工具并把 Observation 送回下一轮。它训练的是工具循环、边界和停止治理，不要求记录模型隐藏思维。

### 代码入口

启动后先阅读：

1. `services/agent_runtime/src/agent_runtime/gateway.py` 的 ToolCall union 和 `GatewayClient`；
2. `synora_agentic_erp/gateway/registry.py` 的服务端 allowlist/permission 重检；
3. `services/agent_runtime/src/agent_runtime/evaluation/loader.py` 的固定 case 加载方式。

第一轮只在 `labs/agent_patterns/` 完成最小实验，不接业务 UI，不修改 Frappe DocType。

### 输入与输出

- 输入：固定 Goal、最多两个只读工具的 fake/recorded adapter、`max_steps=4`、当前授权 allowlist；
- 输出：结构化 `Action | FinalAnswer`、按序 `TraceEvent`、唯一 `StopReason`；
- 工具异常、未知工具、非法参数和重复调用都必须转换为可断言结果，不能抛出未分类异常结束练习。

### 完成标准

- 正常用例在第一次 Observation 后选择第二个不同工具；
- 相同 tool + canonical args 重复时，在达到 `max_steps` 前以 `REPEATED_CALL` 停止；
- 不在 allowlist 的工具永远不被执行；
- Trace 能按顺序还原 Action、Observation、guard 和 stop reason；
- 不保存 chain-of-thought、secret 或完整敏感 ERP 返回；
- 至少为正常、重复、未知工具和工具失败写 4 个测试。

### 不应修改的边界

- 不修改上游 Frappe/ERPNext、`.env*`、README、Gateway 权限规则或现有确定性采购计算；
- 不新增 ERP 写工具、数据库直连、任意 HTTP/MCP 工具、LangGraph 或 Multi-Agent；
- 不为了演示成功删除 schema、权限、停止、错误分类和 Trace 脱敏要求。

### 提示梯度

1. 先只定义 `Action`、`Observation`、`FinalAnswer` 三个严格类型；
2. 再用 `for step in range(max_steps)` 串起 model adapter 和 tool adapter；
3. 用 `(tool_name, canonical_json(args))` 形成重复调用 key；
4. 最后添加 Trace 和 StopReason，不要先设计通用框架或插件系统。

预期耗时：30–45 分钟。

### 面试追问

1. 为什么只有 `max_steps` 仍然不能阻止昂贵的无进展循环？
2. 为什么 tool allowlist 必须在服务端重检，不能只写进 system prompt？
3. 为什么 Trace 应保存显式 Action/Observation，却不依赖隐藏 chain-of-thought？

## 7. P4.2 Assignment 2：实现实验室版 RepeatedCallGuard

状态：`已完成`。用户完成了重复调用判断，指定 kernel 测试已达到 `6 passed`；共享 kernel 的安全边界没有被练习修改。

### 业务背景

Agent 如果连续用同一个工具、同一组参数查询，通常不会得到新事实，却会继续消耗 token、时间和费用。内核需要在第二次完全相同调用时停止，并返回 `REPEATED_CALL`；不同工具、不同参数，或仅 JSON 键顺序不同，都不能误判成重复。

### 代码入口（请直接打开这个文件）

打开 `/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/labs/agent_patterns/react_lab.py`，定位 `LearningRepeatedCallGuard.check()`（当前约第 105 行）。类上方的 docstring 已放好 `TODO(learning)`、输入/输出说明和半成品传统写法。`Action.call_key()` 的定义在 `/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/services/agent_runtime/src/agent_runtime/agent/contracts.py` 的 `Action` 类中（当前约第 141 行），不需要自己重新拼 JSON。

### 你要完成的行为

| 第几次收到同一 key | `check(action)` 应返回 | 内核行为 |
| --- | --- | --- |
| 第一次 | `False` | 允许工具执行 |
| 第二次及以后 | `True` | 在调用工具前停止，`StopReason.code == "REPEATED_CALL"` |

同一 key 的比较由 `action.call_key()` 完成：它固定使用 `tool_name + canonical_json(canonical_args)`，canonical JSON 会把对象键排序。因此不要比较 Python 字典的原始字符串，也不要把 `step` 当作 key 的一部分。

### 半成品 sample（不是答案）

下面是可以逐行对照的传统写法。两个 `______` 需要你根据上面的行为表填写；变量名和三步顺序已经给出，但关键布尔返回值仍由你决定：

```python
seen_keys = self._seen
current_key = action.call_key()

if current_key in seen_keys:
    return ______       # 第二次看到时，告诉 kernel “应该停止”

seen_keys.add(current_key)
return ______           # 第一次看到时，告诉 kernel “可以继续”
```

运行时的小例子（只帮助你理解输入/输出，不需要把它写进生产代码）：

```python
guard = LearningRepeatedCallGuard()
first_result = guard.check(first_action)
second_result = guard.check(same_action)
# 期望：first_result 是 False，second_result 是 True
```

传统写法的含义是：先取出集合和 key，再用 `if` 判断是否见过；没见过就 `add`；最后返回布尔值。等练习通过后，你可以再思考用 `set.add()` 或其他高级写法怎样表达，但本 Assignment 不要求压缩成一行。

### 不要修改的范围

- 只修改 `LearningRepeatedCallGuard.check()` 的练习部分；不要修改 `kernel.py`、`contracts.py`、Gateway、Provider 或真实业务计算。
- 不要改变测试里的期望值、第二次调用的参数或 `ReferenceRepeatedCallGuard`（它是测试 oracle）。
- 当前测试上方的 `@pytest.mark.xfail(...)` 是练习开关：先保留它运行 starter；行为通过后再删除这一行和相邻的 TODO 注释，然后重新运行测试。

### 验收命令

请从仓库根目录执行：

```bash
UV_CACHE_DIR=/private/tmp/synora-uv-cache uv run --offline --no-sync --python 3.14 \
  pytest services/agent_runtime/tests/test_agent_kernel.py -q
```

完成前预期是 `5 passed, 1 xfailed`；完成并移除 xfail 后预期是 `6 passed`。如果结果不是这样，先把完整输出贴回来，不要改动共享内核来“绕过”测试。

### 提示梯度

1. 看懂 `self._seen` 的类型：它是保存字符串 key 的 `set[str]`。
2. 先在脑中写出第一次调用和第二次调用的集合状态：第一次之前为空，第一次之后含一个 key。
3. 用 `if current_key in seen_keys` 区分两条路径；只有“第一次”路径需要 `add`。
4. 如果不同参数被误判，检查自己是否调用了 `action.call_key()`，以及是否意外把整个 `Action` 或 `step` 放进比较逻辑。

### 面试追问

1. 为什么第二次相同调用必须在工具执行前停止？
2. 为什么不能只比较 `tool_name`，而要把 canonical args 放进 key？
3. 为什么 JSON 键顺序变化不应产生两个不同的调用？

## 8. P4.2 手写模式实验实现

状态：`COMPLETED_FOR_CURRENT_INCREMENT`。本增量已在离线 recorded adapter 上完成五个最小模式，并统一返回 `RunResult`：

- `labs/agent_patterns/handwritten.py`：Direct 单次回答、共享 bounded ReAct、一次性 Plan-and-Solve、最多一次 Reflection，以及采购版 `MiniStepAgent`。
- `labs/agent_patterns/comparison.py`：把每次运行转换为统一的成功率、轨迹正确性、工具次数、Observation 次数、停止原因、耗时、usage、成本、Trace 事件数和复杂度记录。
- `services/agent_runtime/tests/test_handwritten_patterns.py`：用固定 P4-G01 recorded 响应验证五种模式和比较顺序；不访问网络、ERP、capability 或生产凭证。

这些模式是学习实验，不会被 Frappe 业务 Runtime 导入。它们复用 P4.2 共享 kernel 的 allowlist、typed ToolCall 校验、重复调用守卫、Observation digest 和 Trace；完整 token/cost/wall-clock/取消/并发门禁留给 P4.4。

### 当前增量验收

- handwritten pattern tests：`6 passed`。
- Runtime tests：`108 passed`。
- labs、kernel 与新增测试的 Ruff 和 mypy targeted 检查：通过。
- 真实 BYOK、Frappe API、浏览器 Trace UI 和 P4-G01 ERP 验收：尚未开始，不把实验结果写成生产收益。

## 9. P4.3 Assignment 3：构造 provider `tool` role message

状态：`待练习`。P4.3 的 provider 契约已经支持 `assistant.tool_calls` 和 `tool` 结果消息；你只需在实验室完成一个纯函数，把一次已经脱敏的 Observation 变成下一轮 provider 能识别的结果消息。

### 业务背景与必要性

原生 Tool Calling 的顺序是“provider 给出 call id 和工具名 → Gateway 返回有界 Observation → Runtime 用 `tool` role 把结果配回这次调用”。少了 call id，provider 无法可靠配对；把完整 ERP 返回塞入 content，则会扩大上下文和敏感数据边界。这个练习只训练消息字段映射，不训练网络或权限代码。

### 代码入口（直接打开）

打开 `/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/labs/agent_patterns/tool_message_lab.py`，定位 `build_learning_tool_message()`（文件中已有 `TODO(learning)`、字段对照表和传统半成品 sample）。配套测试在 `/Users/qilong.lu/WorkDir/atlax-tech/Synora-Agentic-ERP/services/agent_runtime/tests/test_tool_message_lab.py`。

### 输入与期望输出

| 输入 | 输出字段 | 应该放什么 |
| --- | --- | --- |
| `provider_tool_call_id: str` | `ProviderMessage.tool_call_id` | provider 原样返回的 call id |
| `tool_name: ToolName` | `ProviderMessage.name` | 当前已经通过 allowlist 的工具名 |
| `observation: Observation` | `ProviderMessage.content` | `observation.summary`，不是完整对象、digest 或原始 ERP 数据 |

输出必须是 `ProviderMessage(role="tool", ...)`。不要手动拼 JSON；provider 负责把这个 typed message 序列化成 OpenAI-compatible wire shape。

### 完成标准

- 只修改 `labs/agent_patterns/tool_message_lab.py` 中的练习函数；删除 `NotImplementedError` 和对应 TODO。
- 删除测试函数上方的 `@pytest.mark.xfail(...)`，让断言真正执行。
- 指定测试应从 `1 xfailed` 变成 `1 passed`；消息的 role、call id、工具名、摘要和 JSON 可序列化断言都要通过。
- 不把 `observation.digest`、capability、API key、完整 prompt 或 ERP 原始字段放入消息 content。

### 不应修改的边界

- 不修改 `providers.py`、`native_tool_calling.py`、Gateway、HTTP client、allowlist、成本或预算代码。
- 不新增 provider 请求，不读取 `.env*`，不连接真实 BYOK 或 ERP。
- 不改变测试中的输入、期望字段或 xfail 以外的断言来绕过练习。

### 初学者逐步提示

1. 先读函数签名：三个参数已经是 typed 输入，不需要解析字符串。
2. 打开 `ProviderMessage` 定义，确认 `role`、`tool_call_id`、`name`、`content` 四个字段。
3. 将上面的输入/输出表逐行翻译成关键字参数；`content` 只取 `observation.summary`。
4. 保存后删除 xfail，运行：

   ```bash
   UV_CACHE_DIR=/private/tmp/synora-uv-cache uv run --offline --no-sync --python 3.14 \
     pytest services/agent_runtime/tests/test_tool_message_lab.py -q
   ```

5. 如果失败，先看 traceback 指向的字段名；不要为了通过测试修改共享 provider 契约。

### 面试追问

1. 为什么 `tool_call_id` 必须来自 provider，而不能用本地 step 代替？
2. 为什么工具结果 content 只放 bounded summary，digest 另存于 Trace？
3. 为什么 helper 可以由实习生完成，但 API key、allowlist 和成本计算不能交给这个练习？

## 10. 启动门禁与人工核对（历史启动前说明）

本节记录的是 Phase 4 启动前的门禁，当前阶段已经启动；不应覆盖上面的 P4.1/P4.2 实施状态。

收到用户明确的 Phase 4 启动指令前：

- 不创建上述目标代码路径；
- 不运行模型或真实 ERP 新场景；
- 不把 Assignment 标成已尝试或已掌握；
- `docs/PLAN.md` 当前阶段保持 Phase 4 `READY_NOT_STARTED`。

启动时的第一条回复必须先重述业务结果、文件边界、风险和本 Assignment，再等待用户选择亲手完成或明确要求 Agent 接手。
