# Phase 11 阶段收口报告（最终）

状态：`COMPLETED / PASS / READY FOR NEXT PHASE`。

实现、真实运行证据、全量门禁、最终独立对抗复核和已授权 Harness 同步均已完成。本文只引用已经提交、已经运行并能复核的事实；Phase 12 尚未开始。

实现基线 HEAD：`98ee158`（Responses 图片细节、探测语义和 trial 墙钟预算已修复）。本报告基于最终独立审查前冻结的候选证据，并已补入审查与 Harness 收口结果；所有当前证据均为已提交、已运行结果。

## 1. 业务结果和用户可见范围

Phase 11 交付了一个只绑定 loopback 的 `LAB_ONLY / SYNTHETIC DATA` 采购读取实验。用户可以启动隔离页面，用同一个只读任务分别运行 API、DOM、ARIA、截图坐标和显式 Hybrid 方法，看到 Observation、模型决策、动作回执、最终字段、错误码、停止原因、预算和安全事件。

统一任务是：找到指定采购单，读取采购单号、供应商、业务状态和币种，并判断观察是否完整。目标不存在、页面未就绪、权限拒绝、登录失效、观察冲突、陈旧引用、模型结构错误和预算耗尽都返回明确终态；未知字段不会被补猜。页面明细数量只用于滚动和定位实验，不跨单位相加。

数据流为：合成 fixture/API 或固定开发 ERP typed Gateway 提供只读事实 → 独立 BrowserContext 生成 DOM/ARIA/PNG/Hybrid 观察 → 白名单动作经过当前 Observation、页面版本、坐标、origin、路由、HTTP 方法和预算校验 → Action Receipt 后重新观察 → fixture/ERP oracle 只在执行结束后评分并写入脱敏证据。模型没有 Cookie、Authorization、capability、storage state、DOM/API 答案、登录页或未脱敏 ERP 截图。

三个主要入口：

1. [labs/web_gui/cli.py](../../labs/web_gui/cli.py) 和 [labs/web_gui/__main__.py](../../labs/web_gui/__main__.py)：`serve`、`smoke`、`probe-vision`、`benchmark`。
2. [labs/web_gui/browser.py](../../labs/web_gui/browser.py)、[labs/web_gui/gui.py](../../labs/web_gui/gui.py)、[labs/web_gui/hybrid.py](../../labs/web_gui/hybrid.py)：DOM/ARIA、截图坐标和 Hybrid 循环。
3. [labs/web_gui/erp_readonly.py](../../labs/web_gui/erp_readonly.py)、[labs/web_gui/erp_browser.py](../../labs/web_gui/erp_browser.py)、[labs/web_gui/redaction.py](../../labs/web_gui/redaction.py)、[labs/web_gui/erp_visual.py](../../labs/web_gui/erp_visual.py)：真实 ERP 只读、精确浏览器 allowlist、脱敏和 GUI 对照。

## 2. P11.0–P11.15 完成度

| 步骤 | 当前结果 | 证据 |
| --- | --- | --- |
| P11.0 | 已完成：边界、预算、LAB_ONLY 和保护文件约束冻结 | [phase11-execution-contract.md](phase11-execution-contract.md) |
| P11.1 | 已完成：loopback fixture 页面、列表/搜索/详情和只读 API | `ba2ff55` 及 Phase11 浏览器测试 |
| P11.2 | 已完成：有界 DOM 观察和模型决策入口，保留确定性回归 | `3c68460`、`4067f2d`、`e0dce4f`、`93896f5`、live DOM 轨迹 |
| P11.3 | 已完成：独立 ARIA snapshot、role/name/层级和键盘路径 | `96e52c9`、live ARIA 轨迹 |
| P11.4 | 已完成：BrowserContext origin/路径/方法/下载/弹窗/Service Worker/写入边界 | `09d4ad8`、`fc87b2f`、`a6975ac`、`10cb0e1`、`45a793a` 安全 probe |
| P11.5 | 已完成：真实图片协议探测和错误分层；冻结首个双图通过角色 | `0c44029`、`98ee158`、[current vision probe](phase11-vision-probe-live-glm-98ee158.json) |
| P11.6 | 已完成：纯视觉坐标动作、动作后重截图、最终字段证据 | `7f1569d`、`2bb2aa2`、`93896f5`、live vision 轨迹 |
| P11.7 | 已完成：同一 page version 的显式 Hybrid，冲突 fail-closed | `7f1569d`、`eb2d45f`、`93896f5`、Hybrid r2 轨迹 |
| P11.8 | 已完成：可观察就绪、一次恢复和动作/模型/墙钟预算 | `7867f70`、`89dbfbb` |
| P11.9 | 已完成：独立会话、认证失效、权限拒绝和安全弹窗处理 | `7867f70`、`fc87b2f` |
| P11.10 | 已完成：v2 页面变化先失败，修复后复验，旧失败保留 | [failure](phase11-page-change-failure-v1.json)、[repair](phase11-page-change-repair-v1.json) |
| P11.11 | 已完成：真实 ERP typed API/Web 与模型 Web 对照，三次版本稳定 | [ERP live batch](phase11-benchmark-erp-readonly-live-glm-6392dc5-r3.json) |
| P11.12 | 已完成：确定性字段映射、脱敏截图、真实模型 GUI 对照 | 同一 ERP live batch；[current redaction boundary](phase11-erp-visual-boundary-live-glm-6392dc5.json)；历史阻塞文件仍保留 |
| P11.13 | 已完成：live synthetic 45 个主 trial、78 个故障/安全记录，失败保留 | [synthetic live batch](phase11-benchmark-synthetic-live-glm-6392dc5-r3.json) |
| P11.14 | 已完成：最终独立对抗复核为 `PASS` | 最终复核记录（本报告第 8 节） |
| P11.15 | 已完成：PLAN、source-index、manifest 已按授权同步，最终 drift 为 0 | Harness 收口提交及第 6 节最终门禁 |

阶段内业务实现提交均为小步原子提交；未修改 ERP/Frappe 核心、业务 Runtime、`.env*` 或 README；最终收口只按授权更新 `.harness` 的三条来源指纹和对应管理哈希，没有推送或改写历史。旧的大格式证据提交已显式 revert；当前同时保留修复前 ERP 回归、修复后 ERP artifact `6392dc5-r3`、当前 synthetic artifact 和视觉 probe；失败证据没有被覆盖。

## 3. 真实多模态诊断和冻结选择

真实探测 artifact：`phase11-vision-probe-live-glm-98ee158.json`，SHA-256 `f4bf910373a7995135e755dbfedd803d4c024e0500867937e0d7c9c80a74dabd`。两张合成 PNG 的 SHA-256 为 `80b3e3f8eb0ee079c6d723cadcc57ab46ed3d3d637f19059b9f0bd1ddd6aa2f9` 和 `934309d8c7d89b06f53c78fae087fc89d841fb259b81d7f8b68514136f577945`；预期值没有进入提示词。

| 角色 | 模型/协议 | 实际结果 | 诊断 |
| --- | --- | --- | --- |
| assist | `glm-5.3-flash` / Chat Completions | `PASS`，双图、四字段逐图匹配、`complete=true` | HTTP 200、`choices`、usage 可读；普通 ping 只有 reasoning 内容，结构化 JSON 请求得到 final content |
| backup | `grok-4.5` / Responses | HTTP 200，`output` 和 JSON 都解析成功，但 `RESPONSE_CONTENT_MISMATCH` | 不是连接失败，也不是“模型不支持图片”；可信字段校验四项均不一致，保留为模型/提示词/图像读取质量问题 |
| primary/last_local | 当前配置角色 | 本阶段没有作为冻结角色 | 不把 Ollama 未启动或其它未选角色的连接事实冒充能力结论 |

冻结角色为 assist/`glm-5.3-flash`/Chat Completions，模型超时 60 秒、每次最多 1,024 tokens。该选择来自第一个真实双图通过结果；Grok 的兼容 Responses 解析已经支持，但内容不一致时继续安全失败。xAI 的官方文档也明确描述了 Grok 的 `input_image` 图片输入格式：[xAI Image Understanding docs](https://docs.x.ai/developers/model-capabilities/images/understanding)。

## 4. 真实和合成运行证据

### 合成 live 冻结批次

artifact SHA-256：`4db02dd03ffe760688c40805183e9214f20349909927a4b4742b2cd1b6506da3`。批次固定 `engine=live`、`repeats=3`、五种方法、三个案例，共 45 个主 trial 和 78 个故障/安全记录；没有 test double 方法。

| 方法 | 主 trial 正确 | 安全通过 | 模型调用 | 错误/阻塞 | 延迟 ms（min/median/max） |
| --- | ---: | ---: | ---: | ---: | --- |
| API | 9/9 | 9/9 | 0 | 0/0 | 0/0/0 |
| DOM | 5/9 | 9/9 | 20 | 4/0 | 5837/9476/16633 |
| ARIA | 8/9 | 9/9 | 18 | 1/0 | 5813/7300/10966 |
| 视觉 | 4/9 | 9/9 | 13 | 5/0 | 20642/29470/41445 |
| Hybrid | 5/9 | 9/9 | 17 | 2/2 | 5380/33373/43586 |

这里的“错误/阻塞”来自逐 trial 的 `FAILED/BUDGET_EXCEEDED` 和 `BLOCKED` 状态；当前统计同时保留模型空响应、传输失败和预算停止，失败没有被删除或从统计中筛掉。目标不存在的 `NOT_FOUND` 按正确的业务终态统计。网页故障矩阵覆盖页面变化、异步、持续加载超时、权限和登录失效各三次；外域、弹窗、下载、写入、确认和陈旧坐标单独作为执行器安全 probe，全部 `SAFE_STOP`。

### 真实 ERP API/Web/GUI

artifact SHA-256：`62a1b8649dc4850b820d22eff8ad26343394e5784ed7101edf3d6f0d1b4c712e`。采购单 `PUR-ORD-2026-02297` 在固定 `dev.localhost`、同一 Buyer/Company 和同一版本下运行三次：

- API/Web：`MATCHED=3/3`，`MISMATCH=0`，`STATE_DRIFT=0`，`BLOCKED=0`。
- GUI：`SUCCEEDED=2/3`，`FAILED=1/3`；失败为 `RESPONSE_CONTENT_MISSING`，停在模型响应阶段，没有执行坐标动作或业务写入。
- 两次 GUI 成功都读出 `PUR-ORD-2026-02297`、`SYNORA-P1-Supplier-1`、`To Receive and Bill`、`CNY`，`field_differences=[]`。
- 三次记录都落盘了 `api_before_snapshot` 和 `api_after_snapshot`，包含四字段、`source_modified_at`、Frappe/ERPNext revision 和 evidence digest；成功记录的 `reconciliation.fields_match=true`、`versions_match=true`。时间均为 `2026-09-11 01:10:41.759974`；三次 policy events 只有预期的 `SOCKET_BLOCKED` 和 `NON_TARGET_DOCUMENT_BLOCKED`；两次成功的 `reconciliation.fields_match` 与 `versions_match` 均为 true。

因此已经有至少一次同一真实 ERP 单据的 API/Web/GUI 四字段一致、版本稳定、零业务写入对照；同时 GUI 的一次模型结构失败仍然计入结果。

### 页面变化失败与修复

v2 把列表行属性从 `data-order-name` 改为 `data-order-id`。修复前 artifact [phase11-page-change-failure-v1.json](phase11-page-change-failure-v1.json)，SHA-256 `36f19889d8d2a663f72241d75cde5f26605b822ec971cfc2bd7503fb2c212553`，结果为 `FAILURE_BEFORE_FIX/NOT_FOUND`；修复后 artifact [phase11-page-change-repair-v1.json](phase11-page-change-repair-v1.json)，SHA-256 `b22dc8324bab273d0b8a143311f30ebc2bbe64ce1c1e8d2787bf7ae7384616db`，结果为 `FIXED_AND_RETESTED/SUCCEEDED`。旧失败文件未删除，v1 正常案例回归通过。

## 5. 安全边界和剩余限制

- 动作只允许打开预定义页面、点击当前观察目标、限定搜索、滚动、有界等待和结束；模型不能指定任意 URL、JavaScript、剪贴板、文件、上传、下载、ERP 写入或任意 HTTP。
- BrowserContext 禁用 Service Worker，阻断新窗口、下载、外域、非目标文档、非 allowlist 方法和超大响应；登录失效立即返回 `AUTH_REQUIRED`，不会让模型填写凭证。
- 视觉输入最多两张 PNG、每张不超过 2 MiB；响应最多 2,000,000 bytes；trial 最多 12 actions、8 model calls、180 秒；模型预算与页面动作预算分离，迟到结果不执行动作。
- 当前脱敏边界 artifact [phase11-erp-visual-boundary-live-glm-6392dc5.json](phase11-erp-visual-boundary-live-glm-6392dc5.json)，SHA-256 `5d2f88cb5102e39b718044432ad40f5af7870be8c5dd4430474cc3695613b8d2`，明确记录 `READY`、1024×768、当前截图 SHA 和 GLM probe/batch 引用；旧的 `phase11-erp-visual-boundary.json` 保留为历史阻塞快照，不能当作当前状态。
- GLM 已证明当前配置的真实图片链路可用；当前版本合成和 ERP live 仍如实记录模型偶发空内容/协议错误，视觉方法的成功率和延迟受模型输出波动影响，不能当作生产收益或生产稳定率。Grok 保留为“协议可读但可信字段不一致”的限制样本，不用它替换已冻结的 GLM。
- typed API 继续是业务默认；所有网页/GUI/Hybrid 代码保持 `LAB_ONLY`，不注册到业务 Runtime，不获得业务写入权限。

## 6. 实际门禁结果

| 命令 | 退出码 | 实际结果 |
| --- | ---: | --- |
| `make format-check` | 0 | 432 files already formatted |
| `make lint` | 0 | All checks passed |
| `make type` | 0 | 133 个项目源码/测试路径无错误 |
| `make unit` | 0 | 974 passed；55 个既有弃用警告 |
| `make integration` | 0 | Frappe 集成 248/248，`OK` |
| `uv run --python 3.14 --group web-gui-lab mypy labs/web_gui` | 0 | 17 个实验源码文件无错误 |
| `uv run --python 3.14 --group web-gui-lab pytest tests/test_phase11_*.py` | 0 | 118 passed in 45.29s；无 skip |
| `python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .` | 0 | valid；887 references，broken 0；read-only |
| `python3 .agents/skills/harness-check/scripts/validate_manifest.py .` | 0 | valid；warnings 0 |
| `python3 .agents/skills/harness-check/scripts/check_references.py .` | 0 | 887 checked，broken 0，scan 未截断 |
| `python3 .agents/skills/harness-check/scripts/score_harness_health.py .` | 0 | read-only 87/100，grade B；语义维度仍按 Harness 规则限分，不作业务通过依据 |
| `git diff --check` | 0 | whitespace clean |
| `python3 .agents/skills/harness-check/scripts/detect_drift.py .` | 0 | Harness 同步后无剩余 drift |

只读 ponytail audit 结论为 `Lean already. Ship.`；ponytail debt 只有仓库既有的 1 条固定上限标记，且有升级触发条件，无新增 Phase11 debt。没有因为可选浏览器依赖而改动业务 runtime 依赖组。Harness 同步前的 drift 只有两个已知依赖指纹，已在最终收口中精确更新并复验为 0。

## 7. 采用结论、风险和审查入口

当前最终采用结论：

- typed API：`KEEP BUSINESS DEFAULT`。
- DOM/ARIA：`LAB_ONLY CANDIDATE`，适用于结构稳定、目标唯一、模型预算可满足的只读定位。
- screenshot GUI：`LAB_ONLY CANDIDATE / EXPERIMENT ONLY`，已经有真实成功轨迹，但稳定性必须按失败率和模型版本重新评测。
- Hybrid：`LAB_ONLY CANDIDATE`，必须保持结构与截图同一 page version；冲突只能停止，不能静默降级成 DOM/API 成功。

最终 Rubric 保持谨慎的 `28/36`（平均 `3.11`）：安全边界、真实集成、最终 Review 和 Harness 门禁均已满足；D6 仍是部分无障碍/双语覆盖，R11-TEXT-LATENCY 仍作为明确的模型质量限制保留。当前 P0=0、P1=0；不存在阶段出口阻断。

最终独立审查的输入范围以修复后重新冻结：实现 HEAD `98ee158`、证据提交 `cb4a473`、本报告、Phase11 测试和全量门禁输出、当前 vision probe、`6392dc5` synthetic live batch、`6392dc5-r3` ERP live batch、当前 redaction boundary、页面变化前后 artifact，以及 `.env*`/ERP 核心/业务 Runtime 未被修改的证据。审查重点是模型输入隔离、动作与网络 allowlist、API-after 漂移、真实三方字段一致、Grok/GLM 诊断是否如实和失败样本是否完整保留。

审查 PASS 后已按已授权范围同步：`docs/PLAN.md` 的 Phase11 状态、`.harness/source-index.json` 中 `docs/PLAN.md`、`pyproject.toml`、`uv.lock` 当前 SHA、`.harness/manifest.json` 对应管理 SHA。README 未因本阶段实验改变。同步后 structure、manifest、references、drift 和 `git diff --check` 均复验通过，drift 为 0。

## 8. 最终独立对抗审查与权威同步

- 审查范围：实现基线 `98ee158`、证据冻结提交 `cb4a473`、最新测试修复 `f713f05`、候选文档提交 `321e2a0`、Phase11 专项测试、全量门禁、GLM 双图 probe、synthetic/ERP live artifact、脱敏边界、页面变化前后复盘以及 `.env*`、ERP 核心和业务 Runtime 未修改证据。
- 本轮最终确认复核为第 2 次收口审查周期中的确认步骤，结论 `PASS`。前次提出的三项 P2 已逐项核销：所有 trial 内 Playwright 调用绑定剩余墙钟；当前 `98ee158` 的 `detail=high`/探测语义与双图 PASS artifact 一致；报告与 Rubric 均记录 unit `974`、Phase11 `118`。
- Harness 只同步用户已授权的管理范围：`pyproject.toml`、`uv.lock`、`docs/PLAN.md` 的 source fingerprint 和对应 manifest 管理哈希；未修改 README、`.env*`、ERP/Frappe 核心或其它 Harness 内容。最终结构检查核对 887 项引用、0 断链；manifest、references、drift（0）和 diff 均通过。
- 阶段停止在 Phase 11。typed API 仍是业务默认；网页、截图和 Hybrid 仅保持 `LAB_ONLY` 实验候选，不取得业务写入权限。

## 9. 手工复验

```bash
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui --help
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui smoke --mode dom
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui smoke --mode aria
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui benchmark --suite synthetic --repeats 3
```

需要访问已配置 provider 或固定开发 ERP 时，在受保护 shell 中加载 `env/dev/.env`，再显式使用 `--python 3.14 --group web-gui-lab`。打开页面变化两个 artifact，先确认修复前失败，再确认修复后成功；打开 ERP live artifact，核对 API/Web/GUI 的字段、版本和 policy events。

## 10. 学习和阶段边界

本阶段按用户明确要求不生成学习笔记、不自动安排问答、不触发 Assignment、不调用 c2c/codex-with-chatgpt。用户在阶段结束后明确触发答疑时，才按学习笔记规则记录问题和回答。达到最终门禁后停止在 Phase 11，不自动开始 Phase 12。
