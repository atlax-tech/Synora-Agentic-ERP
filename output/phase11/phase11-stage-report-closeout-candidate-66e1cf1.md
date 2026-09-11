# Phase 11 阶段收口报告（待最终审查）

状态：`READY_FOR_FINAL_REVIEW`。

实现和运行证据已经完成，尚未把阶段写成 `COMPLETED / PASS`：还需要一次最终独立对抗审查，以及按已授权范围同步 Harness 的两个配置来源指纹和阶段权威状态。本文只引用已经提交、已经运行并能复核的事实。

实现基线 HEAD：`66e1cf1`（`style(lab): format phase11 closeout code`）。

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
| P11.2 | 已完成：有界 DOM 观察和模型决策入口，保留确定性回归 | `3c68460`、`4067f2d`、live DOM 轨迹 |
| P11.3 | 已完成：独立 ARIA snapshot、role/name/层级和键盘路径 | `96e52c9`、live ARIA 轨迹 |
| P11.4 | 已完成：BrowserContext origin/路径/方法/下载/弹窗/Service Worker/写入边界 | `09d4ad8`、`fc87b2f` 安全 probe |
| P11.5 | 已完成：真实图片协议探测和错误分层；冻结首个双图通过角色 | `0c44029`、[vision probe](phase11-vision-probe-r3-e0ffd6d.json) |
| P11.6 | 已完成：纯视觉坐标动作、动作后重截图、最终字段证据 | `7f1569d`、`2bb2aa2`、live vision 轨迹 |
| P11.7 | 已完成：同一 page version 的显式 Hybrid，冲突 fail-closed | `7f1569d`、`eb2d45f`、Hybrid r2 轨迹 |
| P11.8 | 已完成：可观察就绪、一次恢复和动作/模型/墙钟预算 | `7867f70`、`89dbfbb` |
| P11.9 | 已完成：独立会话、认证失效、权限拒绝和安全弹窗处理 | `7867f70`、`fc87b2f` |
| P11.10 | 已完成：v2 页面变化先失败，修复后复验，旧失败保留 | [failure](phase11-page-change-failure-v1.json)、[repair](phase11-page-change-repair-v1.json) |
| P11.11 | 已完成：真实 ERP typed API/Web 与模型 Web 对照，三次版本稳定 | [ERP live batch](phase11-benchmark-erp-readonly-live-glm-2011048-r3.json) |
| P11.12 | 已完成：确定性字段映射、脱敏截图、真实模型 GUI 对照 | 同一 ERP live batch；[redaction boundary](phase11-erp-visual-boundary.json) |
| P11.13 | 已完成：live synthetic 45 个主 trial、78 个故障/安全记录，失败保留 | [synthetic live batch](phase11-benchmark-synthetic-live-glm-e9e0ccf-r3.json) |
| P11.14 | 代码和证据已齐，待最终独立对抗审查 | 本报告冻结后启动审查 |
| P11.15 | 待审查 PASS 后同步 PLAN 和 Harness 指纹，再重跑 drift | 已生成精确同步范围，尚未改 `.harness` |

阶段内提交均为小步原子提交；未修改 ERP/Frappe 核心、业务 Runtime、`.env*`、README 或 `.harness`，没有推送或改写历史。`eb8c2cd` 的大格式证据提交已由 `2314ec4` 显式 revert，当前只保留紧凑同数据 artifact `2011048`。

## 3. 真实多模态诊断和冻结选择

真实探测 artifact：`phase11-vision-probe-r3-e0ffd6d.json`，SHA-256 `7015be5535ad7405ac1c70ec135912179317137206ad6d905c01e7c2a95b564c`。两张合成 PNG 的 SHA-256 为 `80b3e3f8eb0ee079c6d723cadcc57ab46ed3d3d637f19059b9f0bd1ddd6aa2f9` 和 `934309d8c7d89b06f53c78fae087fc89d841fb259b81d7f8b68514136f577945`；预期值没有进入提示词。

| 角色 | 模型/协议 | 实际结果 | 诊断 |
| --- | --- | --- | --- |
| assist | `glm-5.3-flash` / Chat Completions | `PASS`，双图、四字段逐图匹配、`complete=true` | HTTP 200、`choices`、usage 可读；普通 ping 只有 reasoning 内容，结构化 JSON 请求得到 final content |
| backup | `grok-4.5` / Responses | HTTP 200，`output` 和 JSON 都解析成功，但 `RESPONSE_CONTENT_MISMATCH` | 不是连接失败，也不是“模型不支持图片”；可信字段校验四项均不一致，保留为模型/提示词/图像读取质量问题 |
| primary/last_local | 当前配置角色 | 本阶段没有作为冻结角色 | 不把 Ollama 未启动或其它未选角色的连接事实冒充能力结论 |

冻结角色为 assist/`glm-5.3-flash`/Chat Completions，模型超时 60 秒、每次最多 1,024 tokens。该选择来自第一个真实双图通过结果；Grok 的兼容 Responses 解析已经支持，但内容不一致时继续安全失败。xAI 的官方文档也明确描述了 Grok 的 `input_image` 图片输入格式：[xAI Image Understanding docs](https://docs.x.ai/developers/model-capabilities/images/understanding)。

## 4. 真实和合成运行证据

### 合成 live 冻结批次

artifact SHA-256：`c20c9fa6f822c551fcf3660eb5fb30c2fccd32feda124a156cf84d4109c823c2`。批次固定 `engine=live`、`repeats=3`、五种方法、三个案例，共 45 个主 trial 和 78 个故障/安全记录；没有 test double 方法。

| 方法 | 主 trial 正确 | 安全通过 | 模型调用 | 错误/阻塞 | 延迟 ms（min/median/max） |
| --- | ---: | ---: | ---: | ---: | --- |
| API | 9/9 | 9/9 | 0 | 0/0 | 0/0/0 |
| DOM | 4/9 | 9/9 | 19 | 5/0 | 6962/9324/28664 |
| ARIA | 9/9 | 9/9 | 18 | 0/0 | 6628/8231/10393 |
| 视觉 | 3/9 | 9/9 | 15 | 6/0 | 12372/23143/41368 |
| Hybrid | 8/9 | 9/9 | 17 | 0/1 | 15214/20248/28097 |

这里的“错误/阻塞”来自逐 trial 的 `FAILED/BUDGET_EXCEEDED` 和 `BLOCKED` 状态；失败没有被删除或从统计中筛掉。目标不存在的 `NOT_FOUND` 按正确的业务终态统计。网页故障矩阵覆盖页面变化、异步、持续加载超时、权限和登录失效各三次；外域、弹窗、下载、写入、确认和陈旧坐标单独作为执行器安全 probe，全部 `SAFE_STOP`。

### 真实 ERP API/Web/GUI

artifact SHA-256：`b9470912dc54c1594e631b1d5e8483db21353bc7c82560a08365dfe82548cd3b`。采购单 `PUR-ORD-2026-02297` 在固定 `dev.localhost`、同一 Buyer/Company 和同一版本下运行三次：

- API/Web：`MATCHED=3/3`，`MISMATCH=0`，`STATE_DRIFT=0`，`BLOCKED=0`。
- GUI：`SUCCEEDED=2/3`，`FAILED=1/3`；失败为 `MODEL_RESPONSE_SCHEMA`，四字段差异完整保留，没有执行动作写入。
- 两次 GUI 成功都读出 `PUR-ORD-2026-02297`、`SYNORA-P1-Supplier-1`、`To Receive and Bill`、`CNY`，`field_differences=[]`。
- API-before/after 的 `source_modified_at` 均为 `2026-09-11 01:10:41.759974`；三次 policy events 只有预期的 `SOCKET_BLOCKED` 和 `NON_TARGET_DOCUMENT_BLOCKED`。

因此已经有至少一次同一真实 ERP 单据的 API/Web/GUI 四字段一致、版本稳定、零业务写入对照；同时 GUI 的一次模型结构失败仍然计入结果。

### 页面变化失败与修复

v2 把列表行属性从 `data-order-name` 改为 `data-order-id`。修复前 artifact [phase11-page-change-failure-v1.json](phase11-page-change-failure-v1.json)，SHA-256 `36f19889d8d2a663f72241d75cde5f26605b822ec971cfc2bd7503fb2c212553`，结果为 `FAILURE_BEFORE_FIX/NOT_FOUND`；修复后 artifact [phase11-page-change-repair-v1.json](phase11-page-change-repair-v1.json)，SHA-256 `b22dc8324bab273d0b8a143311f30ebc2bbe64ce1c1e8d2787bf7ae7384616db`，结果为 `FIXED_AND_RETESTED/SUCCEEDED`。旧失败文件未删除，v1 正常案例回归通过。

## 5. 安全边界和剩余限制

- 动作只允许打开预定义页面、点击当前观察目标、限定搜索、滚动、有界等待和结束；模型不能指定任意 URL、JavaScript、剪贴板、文件、上传、下载、ERP 写入或任意 HTTP。
- BrowserContext 禁用 Service Worker，阻断新窗口、下载、外域、非目标文档、非 allowlist 方法和超大响应；登录失效立即返回 `AUTH_REQUIRED`，不会让模型填写凭证。
- 视觉输入最多两张 PNG、每张不超过 2 MiB；响应最多 2,000,000 bytes；trial 最多 12 actions、8 model calls、180 秒；模型预算与页面动作预算分离，迟到结果不执行动作。
- 脱敏截图边界 artifact `phase11-erp-visual-boundary.json` SHA-256 `48a61e3c621454358048d9584320b1e6e6aaaa9294a07ad512105b0fba1fadab`；截图 1024×768、19072 bytes，只保留任务四字段，账号、导航、时间线、评论和动作区隐藏。
- GLM 已证明当前配置的真实图片链路可用；视觉方法的成功率和延迟仍受模型输出波动影响，不能当作生产收益或生产稳定率。Grok 保留为“协议可读但可信字段不一致”的限制样本，不用它替换已冻结的 GLM。
- typed API 继续是业务默认；所有网页/GUI/Hybrid 代码保持 `LAB_ONLY`，不注册到业务 Runtime，不获得业务写入权限。

## 6. 实际门禁结果

| 命令 | 退出码 | 实际结果 |
| --- | ---: | --- |
| `make format-check` | 0 | 430 files already formatted |
| `make lint` | 0 | All checks passed |
| `make type` | 0 | 133 个项目源码/测试路径无错误 |
| `make unit` | 0 | 968 passed；55 个既有弃用警告 |
| `make integration` | 0 | Frappe 集成 248/248，`OK` |
| `uv run --python 3.14 --group web-gui-lab mypy labs/web_gui` | 0 | 17 个实验源码文件无错误 |
| `uv run --python 3.14 --group web-gui-lab pytest tests/test_phase11_*.py` | 0 | 112 passed in 45.24s；无 skip |
| `python3 .agents/skills/harness-build/scripts/validate_harness_structure.py .` | 0 | valid；833 references，broken 0；read-only |
| `python3 .agents/skills/harness-check/scripts/validate_manifest.py .` | 0 | valid；warnings 0 |
| `python3 .agents/skills/harness-check/scripts/check_references.py .` | 0 | 833 checked，broken 0，scan 未截断 |
| `python3 .agents/skills/harness-check/scripts/score_harness_health.py .` | 0 | read-only 79/100，grade C；分数受 host evidence 和 drift 影响，不作业务通过依据 |
| `git diff --check` | 0 | whitespace clean |
| `python3 .agents/skills/harness-check/scripts/detect_drift.py .` | 1 | 仅 `pyproject.toml` 和 `uv.lock` source fingerprint 未同步 |

只读 ponytail audit 结论为 `Lean already. Ship.`；ponytail debt 只有仓库既有的 1 条固定上限标记，且有升级触发条件，无新增 Phase11 debt。没有因为可选浏览器依赖而改动业务 runtime 依赖组。

## 7. 采用结论、风险和审查入口

当前候选结论：

- typed API：`KEEP BUSINESS DEFAULT`。
- DOM/ARIA：`LAB_ONLY CANDIDATE`，适用于结构稳定、目标唯一、模型预算可满足的只读定位。
- screenshot GUI：`LAB_ONLY CANDIDATE / EXPERIMENT ONLY`，已经有真实成功轨迹，但稳定性必须按失败率和模型版本重新评测。
- Hybrid：`LAB_ONLY CANDIDATE`，必须保持结构与截图同一 page version；冲突只能停止，不能静默降级成 DOM/API 成功。

候选 Rubric 保持谨慎的 `27/36`（平均 `3.00`）：安全边界和真实集成证据已满足当前要求，D6 仍是部分无障碍/双语覆盖，D8 等最终审查和 Harness 同步后再重算。当前 P0=0；视觉 provider 不再是阻塞项，剩余开放项是评审和管理指纹同步，以及模型稳定性这一明确限制。

最终独立审查的输入范围冻结为：实现 HEAD `66e1cf1`、本报告、Phase11 测试和全量门禁输出、vision probe、synthetic live batch、ERP live batch、redaction boundary、页面变化前后 artifact，以及 `.env*`/ERP 核心/业务 Runtime 未被修改的证据。审查重点是模型输入隔离、动作与网络 allowlist、API-after 漂移、真实三方字段一致、Grok/GLM 诊断是否如实和失败样本是否完整保留。

审查 PASS 后，按已授权范围只同步：`docs/PLAN.md` 的 Phase11 状态、`.harness/source-index.json` 中 `pyproject.toml` 与 `uv.lock` 两条当前 SHA、`.harness/manifest.json` 对应管理 SHA，以及必要的 Phase11 权威报告引用。README 不因本阶段实验而改变。同步后必须重新运行 structure、manifest、references、drift 和 `git diff --check`，drift 目标为 0。

## 8. 手工复验

```bash
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui --help
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui smoke --mode dom
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui smoke --mode aria
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui benchmark --suite synthetic --repeats 3
```

需要访问已配置 provider 或固定开发 ERP 时，在受保护 shell 中加载 `env/dev/.env`，再显式使用 `--python 3.14 --group web-gui-lab`。打开页面变化两个 artifact，先确认修复前失败，再确认修复后成功；打开 ERP live artifact，核对 API/Web/GUI 的字段、版本和 policy events。

## 9. 学习和阶段边界

本阶段按用户明确要求不生成学习笔记、不自动安排问答、不触发 Assignment、不调用 c2c/codex-with-chatgpt。用户在阶段结束后明确触发答疑时，才按学习笔记规则记录问题和回答。达到最终门禁后停止在 Phase 11，不自动开始 Phase 12。
