# Phase 11 阶段报告（草稿）

状态：`PENDING INDEPENDENT REVIEW / BLOCKED BY VISION PROVIDER`。

本草稿不把实验页面、test double 或固定开发 ERP 读对照描述为生产部署、客户采用、模型质量提升或业务写入授权。真实视觉依赖和 managed Harness fingerprint 同步尚未闭合，因此不能写 `COMPLETED / PASS`。

## 1. 业务结果与用户可见范围

Phase 11 交付了一套只绑定 loopback 的 `LAB_ONLY` 采购读取实验。用户可以启动合成采购页面，选择 DOM、ARIA、截图或混合观察，查看每次 Observation、Action Proposal、Action Receipt、最终字段、错误码、停止原因和预算统计；页面同时提供列表、搜索、详情、空结果、权限、异步、登录失效和安全故障场景。

实验统一任务是：找到指定采购单，读取采购单号、供应商、业务状态和币种，并说明观察是否完整。目标不存在、页面未就绪、权限拒绝、认证失效、观察冲突、陈旧引用和预算耗尽都保持明确终态，未知字段不猜测。

数据流为：合成 fixture/API 或固定开发 ERP typed Gateway 提供只读事实 → 隔离 BrowserContext 生成 DOM/ARIA/PNG/混合 Observation → 受限 Action Proposal 经过引用、坐标、origin、路径、HTTP 方法和预算校验 → Action Receipt 后重新观察 → fixture/ERP oracle 校验字段并写入脱敏证据。视觉 runner 的 decider 只收到 PNG、视口摘要和任务；没有 Cookie、capability、Authorization、DOM 文本、网络响应或 API 答案。

## 2. 主要入口与文件

1. [labs/web_gui/cli.py](../../labs/web_gui/cli.py) 与 [labs/web_gui/__main__.py](../../labs/web_gui/__main__.py)：`serve`、`smoke`、`probe-vision`、`benchmark`。
2. [labs/web_gui/browser.py](../../labs/web_gui/browser.py)、[labs/web_gui/gui.py](../../labs/web_gui/gui.py)、[labs/web_gui/hybrid.py](../../labs/web_gui/hybrid.py)：DOM/ARIA、截图坐标和同步混合循环。
3. [labs/web_gui/erp_readonly.py](../../labs/web_gui/erp_readonly.py)、[labs/web_gui/erp_browser.py](../../labs/web_gui/erp_browser.py)、[labs/web_gui/redaction.py](../../labs/web_gui/redaction.py)、[labs/web_gui/erp_visual.py](../../labs/web_gui/erp_visual.py)：真实 ERP 只读 API/Web、截图遮罩和 GUI 边界。

实现代码 HEAD（最后一个业务代码提交）：`4708b5ebe7eac624b1edf3dc8b2293a5ba2a4dad`。

## 3. 步骤与提交追踪

| 步骤 | 结果 | 主要提交/证据 |
| --- | --- | --- |
| P11.0 | 执行边界、预算、LAB_ONLY 和保护文件约束冻结 | `35627ba`、[execution contract](phase11-execution-contract.md) |
| P11.1 | loopback 合成采购页、fixture API、空/错误/安全场景 | `ba2ff55` |
| P11.2 | 有界 DOM 查询和 Observation→Receipt→Result | `9058e44` |
| P11.3 | ARIA role/name 观察对照 | `96e52c9` |
| P11.4 | origin/路径/方法、下载、弹窗、写入和注入边界 | `09d4ad8` |
| P11.5 | 图片 wire format、大小、响应 schema、usage 和 provider 探测 | `dfe4ed9`；真实探测见 [ERP visual boundary](phase11-erp-visual-boundary.json) |
| P11.6–P11.7 | 截图坐标与显式 hybrid 观察，冲突 fail-closed | `7b44bdd`、`7aa9764` |
| P11.8–P11.9 | 可观察就绪、一次恢复、预算、登录失效、权限和弹窗 | `3582958`、`1a1ded8` |
| P11.10 | v2 定位变化先失败、保留失败、修复后复验 | `ef405f1`、`f69354c`、[failure](phase11-page-change-failure-v1.json)、[repair](phase11-page-change-repair-v1.json) |
| P11.11 | 真实 ERP typed API 与精确 allowlist Web 对照 | `8feaee1`、`b0d9961`、[comparison](phase11-erp-readonly-comparison.json) |
| P11.12 | 真实 ERP 确定性截图遮罩和 GUI test-double 安全边界 | `16bd822`、`a3433ea`、[boundary](phase11-erp-visual-boundary.json) |
| P11.13 | 调用预算、统一 CLI、五方法 benchmark、三次冻结证据 | `9bb4c5b`、`701413d`、`4da1986`、`bd05dae`、[synthetic](phase11-benchmark-synthetic.json)、[ERP](phase11-benchmark-erp-readonly.json) |

每个提交只包含一个可回滚的实验结果；未修改 ERP/Frappe 核心、业务 Runtime、`.env*`、README 或 `.harness`。

## 4. 真实证据与比较结果

### Synthetic

`phase11-benchmark-synthetic.json` SHA-256 为 `b9826a20af630cd757bd3b27cb84930902567b799f5affb3bd106ef3bd76993f`。三次重复包含 45 个业务 trial 和 33 个故障 trial；所有业务 trial `safety_pass`，不删除 `INCOMPLETE`。

| 方法 | 正确 | 业务 trial | 模型调用 | 延迟统计（ms） | 说明 |
| --- | ---: | ---: | ---: | --- | --- |
| API | 9 | 9 | 0 | 0/0/0（min/median/max） | typed fixture oracle |
| DOM | 9 | 9 | 0 | 345/419/934 | bounded Playwright |
| ARIA | 9 | 9 | 0 | 329/402/436 | bounded Playwright |
| 视觉 | 6 | 9 | 9 | 278/303/367 | `scripted-fixture-replay` test double；目标不存在保留 `INCOMPLETE` |
| 混合 | 6 | 9 | 24 | 475/541/648 | `scripted-fixture-replay` test double；同上 |

故障集三次重复均可区分：页面变化修复后 `SUCCEEDED`、异步 `SUCCEEDED`、持续加载 `FAILED/PAGE_NOT_READY`、权限 `PERMISSION_DENIED`、登录失效 `AUTH_REQUIRED`、外域/弹窗/下载/写入/确认均安全停止、陈旧坐标拒绝。

### Real ERP API/Web

`PUR-ORD-2026-02297` 在固定 `dev.localhost`、同一 Buyer/Company 范围和同一单据版本下运行三次，报告 SHA-256 为 `3087cdcf7e52420c7793978e145d28c34bc60dc6664b5b9aeb4bc06bee5b4063`，状态为 `MATCHED=3`、`STATE_DRIFT=0`、`BLOCKED=0`。四个字段为采购单号 `PUR-ORD-2026-02297`、供应商 `SYNORA-P1-Supplier-1`、状态 `To Receive and Bill`、币种 `CNY`；API-before/after `source_modified_at` 一致。Frappe/ERPNext 固定 revision 仍为 `6a329d068416768ec47ccd3326b9cc95a8d7bf99` / `11e0ba0a1c45f217e2e73e885f699102d06da325`。

API 使用现有 `purchase_order.current` typed Gateway、正常 Run/capability 机制和独立会话；Web 只读页面及列举出的 Frappe 只读请求，socket/非目标文档被阻断。前后没有创建或修改业务采购单。

### Real ERP visual

脱敏截图边界报告 SHA-256 为 `a523a39b831058d76a30117c7b4d25760c1651a95874ab23cce8e3283557c575`。截图为 1024×768、约 24 KiB，遮罩后保留任务字段，账号、导航、时间线、评论和动作区隐藏；GUI test double 返回四字段且 `safety_pass=true`。

四个已配置 provider role 的两图真实探测均失败：primary/last_local=`TRANSPORT_ERROR`、assist=`RESPONSE_SCHEMA`、backup=`RESPONSE_CONTENT_MISSING`；最终状态 `VISION_PROVIDER_UNAVAILABLE`，usage 为 `null`。因此 P11.12 要求的真实 ERP API/Web/GUI 三方对照未完成，不能用 test double 或 DOM/API 结果冒充视觉模型成功。

## 5. 页面变化失败与修复

v2 将列表行属性从 `data-order-name` 改为 `data-order-id`。原始文件 [phase11-page-change-failure-v1.json](phase11-page-change-failure-v1.json) 保留 `FAILURE_BEFORE_FIX/NOT_FOUND`，SHA-256 `36f19889d8d2a663f72241d75cde5f26605b822ec971cfc2bd7503fb2c212553`。修复提交 `f69354c` 让观察器读取两个已确认属性且仍要求唯一观察目标；同案例修复后文件 [phase11-page-change-repair-v1.json](phase11-page-change-repair-v1.json) 为 `FIXED_AND_RETESTED/SUCCEEDED`，四字段完整且无安全事件。

## 6. 安全、权限和成本边界

- 允许动作只有预定义打开、观察中目标点击、限定搜索、滚动、有界等待和结束；禁止任意 URL、JavaScript、剪贴板、文件、上传、下载、写入和任意 HTTP。
- BrowserContext 禁用 Service Worker、拒绝下载/弹窗/外域/非目标文档/非 allowlist 方法；动作必须绑定当前 Observation/page version，截图坐标必须在可信视口内。
- 页面内容视为不可信数据；模型输出不能改变权限、外发目标或业务状态。登录由可信初始化完成，登录页不进入模型观察。
- 图片最多两张 PNG、每张 ≤2 MiB，响应 ≤2,000,000 bytes，输出 ≤1,024 tokens；每 trial ≤12 actions、≤8 model calls、≤180s，usage 不可核验时为 `null`。

## 7. 验证门禁

| 检查 | 退出码 | 实际结果 |
| --- | ---: | --- |
| `make format-check` | 0 | 422 files already formatted |
| `make lint` | 0 | All checks passed |
| `make type` | 0（修复后） | 131 个项目源/测试路径无错误 |
| `make unit` | 0 | 901 passed；55 个既有弃用 warning |
| `make integration` | 0 | Frappe 248 tests `OK` |
| `uv run --python 3.14 mypy labs/web_gui` | 0 | 16 个实验源码文件无错误 |
| synthetic benchmark | 0 | 45 business + 33 fault trials written |
| ERP benchmark | 0 | 3/3 `MATCHED` |
| `git diff --check` | 0 | whitespace clean |
| Harness manifest | 0 | valid；references 772、broken 0 |
| Harness drift | 1 | `pyproject.toml`、`uv.lock` fingerprint 尚未同步 |

首次 `make type` 退出码 `1` 的 9 个新增测试注解错误已在 `4708b5e` 修复；修复后 full type 与 29 个受影响测试通过。按计划原样运行无 `--python` 的 `uv run --group web-gui-lab ...` 曾因主机选中 3.13 与项目 3.14 不兼容退出码 `2`；所有实际验收命令显式使用已安装 Python 3.14，未降低项目基线。

## 8. Adoption、Rubric 与风险

- 方法采用结论见 [phase11-adoption-card.md](phase11-adoption-card.md)：typed API 保持业务默认；DOM/ARIA/hybrid 仅为 `LAB_ONLY CANDIDATE`；视觉为 `BLOCKED / EXPERIMENT ONLY`。
- Rubric 见 [phase11-rubric.md](phase11-rubric.md)：`27/36`、平均 `3.00`；D1/D2/D3/D5/D7/D8 均 ≥3，D6 因英文实验页和未完成完整 ERP 无障碍审计为 2。
- 风险登记见 [phase11-risk-register.md](phase11-risk-register.md)：当前未关闭 P0/P1 为 0；视觉 provider 和 Harness drift 为带 owner/下一门禁/复验条件的 P2 阻塞项。

## 9. 独立审查与受保护同步

本报告草稿提交后，才启动一次独立对抗 Review。审查输入包括原始 Phase 11 计划、`35627ba..` 最终 diff、全部 Phase 11 测试输出、synthetic/ERP/视觉/失败修复 artifact、Adoption Card、Rubric、风险表和秘密保护边界；审查只返回 `PASS`、`CHANGES_REQUIRED` 或 `BLOCKED`。

`.harness/manifest.json` 当前有效，但依赖组变更导致 `pyproject.toml` 与 `uv.lock` fingerprint drift。根据 `harness-update` 规则，需先提交文件级只读 proposal，再由用户明确批准具体 Harness 文件/指纹同步；在批准前不修改 `.harness`、README 或其它用户维护文件。

## 10. 阶段结论（草稿）

代码、合成实验、浏览器安全边界、真实 ERP API/Web 对照和失败修复均已完成并可复跑；真实 ERP 脱敏截图和 GUI 坐标边界可运行，但真实图片 provider 未返回可验证观察，导致三方视觉验收阻塞。阶段暂定 `BLOCKED`，不进入 Phase 12，不获得业务写入权限。只有 provider 真实探测通过、Harness 受保护同步获批且独立 Review 最终 `PASS` 后，才能重新评估阶段是否满足 `COMPLETED / PASS`。

## 11. 手工验收

```bash
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui --help
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui smoke --mode dom
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui smoke --mode aria
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui benchmark --suite synthetic --repeats 3
```

在受保护 shell 中执行 `source env/dev/.env` 后运行 `probe-vision` 或 `benchmark --suite erp-readonly --repeats 3`；只检查状态、四字段和 policy events，不打印环境变量。打开两个 page-change artifact，先确认修复前失败，再确认修复后成功；打开 Adoption Card 确认视觉仍为阻塞。

## 12. 学习记录边界

本阶段按用户指令不生成学习笔记、不自动记录答疑、不触发 Assignment、不调用 c2c/codex-with-chatgpt。阶段结束后只有用户明确触发答疑，才按学习笔记规则记录问题和回答。
