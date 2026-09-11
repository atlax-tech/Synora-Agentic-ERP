# Phase 11 阶段报告（当前草稿）

状态：`PENDING_FINAL_REVIEW / BLOCKED / VISION_PROVIDER_UNAVAILABLE / LIVE_GUI_MISMATCH / HARNESS_DRIFT`。

本报告只描述已运行的实验和固定开发 ERP 只读证据，不把实验页面、test double、模型单次成功或开发站点结果描述为生产部署、客户采用或业务写入授权。上一份 `phase11-stage-report-draft-4708b5e.md` 是历史快照，本报告绑定当前实现和最新证据。

## 1. 业务结果和用户可见范围

Phase 11 交付了一个隔离的 `LAB_ONLY / SYNTHETIC DATA` 采购读取实验。用户可以启动 loopback 页面，选择 DOM、ARIA、截图或 Hybrid 观察，查看 Observation、模型 Action Proposal、Action Receipt、最终字段、错误码、停止原因、模型调用和 usage。页面提供列表、搜索、详情、空结果、异步加载、权限拒绝、登录失效和安全故障场景。

统一任务是找到指定采购单，读取采购单号、供应商、业务状态和币种，并说明观察是否完整。未知单据、未完成加载、权限拒绝、认证失效、观察冲突、陈旧引用、模型超时和预算耗尽都保持明确终态，未知字段不会猜测；明细行只用于定位/滚动实验，不跨计量单位相加。

数据流为：合成 fixture/API 或固定开发 ERP typed Gateway 提供只读事实 → 独立 BrowserContext 生成 DOM/ARIA/PNG/Hybrid Observation → 严格 ActionProposal 经过 observation ID、目标引用、坐标、origin、路由、HTTP 方法和预算验证 → ActionReceipt 后重新观察 → fixture/ERP oracle 只在完成后核验字段并记录脱敏证据。纯视觉模型只收到脱敏 PNG、视口和任务；不会收到 Cookie、Authorization、storage state、DOM 文本、网络响应或 API 答案。

## 2. 入口和关键文件

1. [labs/web_gui/cli.py](../../labs/web_gui/cli.py) 与 [labs/web_gui/__main__.py](../../labs/web_gui/__main__.py)：`serve`、`smoke`、`probe-vision`、`benchmark`；`benchmark` 必须显式选择 `--engine deterministic|live`。
2. [labs/web_gui/browser.py](../../labs/web_gui/browser.py)、[labs/web_gui/gui.py](../../labs/web_gui/gui.py)、[labs/web_gui/hybrid.py](../../labs/web_gui/hybrid.py)：结构观察、截图坐标、同步 Hybrid 和有界恢复。
3. [labs/web_gui/model.py](../../labs/web_gui/model.py)、[labs/web_gui/vision.py](../../labs/web_gui/vision.py)、[labs/web_gui/erp_readonly.py](../../labs/web_gui/erp_readonly.py)、[labs/web_gui/erp_browser.py](../../labs/web_gui/erp_browser.py)、[labs/web_gui/erp_visual.py](../../labs/web_gui/erp_visual.py)：模型适配、真实 ERP API/Web/脱敏 GUI 对照。

实现代码冻结 HEAD：`c986c27`。最新确定性 ERP 证据提交：`df71466`；live ERP/API/Web/GUI 尝试提交：`490de9b`；当前报告、卡片和 Harness 同步完成后另列最终文档 HEAD，避免自引用。

## 3. 步骤完成度

| 步骤 | 当前结果 | 主要代码/证据 |
| --- | --- | --- |
| P11.0–P11.1 | 执行边界、预算、loopback fixture 页面/API 已完成 | `35627ba`、`ba2ff55`、execution contract |
| P11.2 | deterministic DOM/ARIA 与 live 模型决策入口已完成；DOM 有真实成功轨迹 | `3c68460`、`925b96a`、`c986c27`、live DOM artifact |
| P11.3 | ARIA role/name 观察、临时引用和回归已完成；一次真实 ARIA 在修复后因模型超时安全停止 | `96e52c9`、`d444e97`、ARIA failure/repair artifacts |
| P11.4 | BrowserContext origin/路由/方法、弹窗、下载、Service Worker、危险动作边界已完成 | `09d4ad8` 及 Phase11 安全回归 |
| P11.5 | 图片协议解析、响应大小/JSON/usage 和真实探测已完成；四 role 未通过内容门禁 | `8a4568f`、`748f99f`、vision probe |
| P11.6–P11.7 | live 截图和 Hybrid 决策接线已完成；真实视觉成功轨迹缺失 | `d227631`、`6c23440`、GUI/Hybrid runner |
| P11.8–P11.10 | 异步、恢复、会话、弹窗、页面变化失败/修复证据已完成 | `3582958`、`1a1ded8`、page-change artifacts |
| P11.11 | deterministic ERP API/Web 三次稳定；live Web 一次与 API 匹配 | `3f5d55a`、`df71466`、live ERP artifact |
| P11.12 | 脱敏 GUI runner 可运行并完成一次真实 provider 调用，但字段 mismatch；三方成功未完成 | `7656119`、`490de9b`、visual boundary |
| P11.13 | deterministic 45 trial/33 fault 已冻结；live 引擎和元数据已接线，完整 live 矩阵受视觉门禁阻塞 | `3674be8`、`dc09d6f` |
| P11.14–P11.15 | 全量回归完成；新周期独立 Review、权威状态和 Harness 指纹同步待完成 | 本报告及后续收口提交 |

## 4. 运行证据

### 4.1 Synthetic deterministic

[phase11-benchmark-synthetic.json](phase11-benchmark-synthetic.json) SHA-256：`1956c43950d3a22a407827cc7f8f1991eb64a9324b49a14bb4bd4c72b80487f6`。三次重复包含 3 个正常案例 × 5 种方法 × 3 次，即 45 条业务 trial，另有 33 条故障记录。API、DOM、ARIA、视觉脚本和 Hybrid 脚本各 9/9 正确且安全；视觉和 Hybrid 在本批次的 `test_double_methods` 明确为 `scripted-fixture-replay`，不代表模型能力。

### 4.2 Live structured smoke

[phase11-live-dom-smoke-925b96a.json](phase11-live-dom-smoke-925b96a.json) SHA-256：`a7d68a718a25e4a0e1ae0cb198afbd4a26d8372d022f8f30fcdfd6ffc0f4b5ed`。`glm-5.3-flash` 通过 2 次模型调用完成 `PUR-ORD-0001` 的 DOM 任务，四字段完整，`prompt_tokens=621`、`completion_tokens=1118`、`latency_ms=12356`。

[phase11-live-aria-failure-925b96a.json](phase11-live-aria-failure-925b96a.json) 与 [phase11-live-aria-repair-d444e97.json](phase11-live-aria-repair-d444e97.json) 分别记录修复前 `ACTION_REJECTED` 和修复后 `BUDGET_EXCEEDED/MODEL_TIMEOUT`。修复确认了 ARIA 观察必须同时提供无障碍快照和执行器生成的临时引用；模型延迟仍受每次动作 10 秒上限约束。

### 4.3 真实图片探测

最近一次 `probe-vision` 使用两张内容不同的合成 PNG，提示词没有透露采购值；摘要见 [phase11-vision-probe-925b96a.json](phase11-vision-probe-925b96a.json)，SHA-256：`273cc7971d7191260cd393f08de87b45a20d76c619de06c98674628598dd12fb`。四个已配置 role 均未通过：primary=`TRANSPORT_ERROR`，assist=`TRANSPORT_ERROR`，backup=`RESPONSE_INCOMPLETE`，last_local=`TRANSPORT_ERROR`；总状态 `VISION_PROVIDER_UNAVAILABLE`，usage 为 `null`。HTTP 200 或 schema 可解析不等于图片理解通过；没有冻结视觉模型，也没有用 recorded response 替代真实验收。

### 4.4 真实 ERP API/Web/GUI

[phase11-benchmark-erp-readonly-deterministic-20260911.json](phase11-benchmark-erp-readonly-deterministic-20260911.json) SHA-256：`b8a12b8cf958053583f0f2c1f52de18e5ed151747154f5621fbf7a3be1805f24`。固定采购单 `PUR-ORD-2026-02297` 三次 API/Web 均 `MATCHED`，版本未漂移，四字段为采购单号 `PUR-ORD-2026-02297`、供应商 `SYNORA-P1-Supplier-1`、状态 `To Receive and Bill`、币种 `CNY`。

[phase11-benchmark-erp-readonly-live-d444e97.json](phase11-benchmark-erp-readonly-live-d444e97.json) SHA-256：`1973dbe929dc2eefc989cf7a79e074f212df98c2067e7ce793d05cba553e08bf`。live API-before → assist Web → backup GUI → API-after 中，API/Web 为 `MATCHED`，GUI 真实调用 `grok-4.5` 一次并返回可解析结果，但字段与 trusted API 不一致，终态为 `INCOMPLETE / visual_fields_mismatch`，无安全事件、无业务写入。该结果不能计为 API/Web/GUI 三方成功。

脱敏边界报告 [phase11-erp-visual-boundary.json](phase11-erp-visual-boundary.json) SHA-256：`48a61e3c621454358048d9584320b1e6e6aaaa9294a07ad512105b0fba1fadab`；截图 1024×768、19072 bytes，遮罩保留任务字段并屏蔽账号、导航、评论和动作区。

### 4.5 页面变化失败和修复

v2 将列表行属性由 `data-order-name` 改为 `data-order-id`。原始失败 [phase11-page-change-failure-v1.json](phase11-page-change-failure-v1.json) SHA-256 `36f19889d8d2a663f72241d75cde5f26605b822ec971cfc2bd7503fb2c212553` 保留 `NOT_FOUND`；修复后 [phase11-page-change-repair-v1.json](phase11-page-change-repair-v1.json) SHA-256 `b22dc8324bab273d0b8a143311f30ebc2bbe64ce1c8e1d2787bf7ae7384616db` 为 `SUCCEEDED`，v1 正常案例无回归。

## 5. 安全和预算结论

- 动作仅允许预定义打开、当前观察目标点击、限定搜索、滚动、有界等待和结束；模型不能指定任意 URL、JavaScript、剪贴板、文件、上传、下载、ERP 写入或任意 HTTP。
- BrowserContext 禁用 Service Worker，拒绝外域、非目标文档、弹窗和下载；每个动作绑定 Observation ID/page version，视觉坐标绑定 CSS 视口。
- 每 trial 上限为 12 actions、8 model calls、180 秒、1024 output tokens；图片最多 2 张 PNG、每张 ≤2 MiB，响应 ≤2,000,000 bytes。没有 usage 或价格证据时保持 `null`。
- 真实 ERP 只读使用正常 typed Gateway、独立会话和精确 allowlist；API-before/after 未发现业务状态变化。任何 provider 字段 mismatch、模型超时、会话失效或权限状态均 fail closed。

## 6. 出口门禁

| 检查 | 结果 |
| --- | --- |
| `make format-check` | 退出码 0，428 files already formatted |
| `make lint` | 退出码 0 |
| `make type` | 退出码 0，132 source files |
| `make unit` | 退出码 0，936 passed，55 warnings |
| `make integration` | 退出码 0，Frappe app-test 248 tests OK |
| `uv run --python 3.14 --group web-gui-lab mypy labs/web_gui` | 退出码 0，17 files |
| `uv run --python 3.14 --group web-gui-lab pytest tests/test_phase11_*.py` | 退出码 0，80 passed |
| `validate_harness_structure.py` | 退出码 0，valid，references 804，broken 0 |
| `validate_manifest.py` | 退出码 0，valid |
| `check_references.py` | 退出码 0，checked 804，broken 0 |
| `detect_drift.py` | 退出码 1；仅 `pyproject.toml`、`uv.lock` fingerprint drift |
| `git diff --check` | 退出码 0 |

Ponytail 只读审计未发现本轮新增可安全删除的复杂度；全仓仅 1 条已有 `ponytail:` 标记且带升级触发器。没有为追求分数删除安全、错误处理、可访问性或数据保护。

## 7. 当前阻断、风险和采用结论

1. `R11-VISION`：四个已配置图片 role 未通过真实内容探测，且一次真实 GUI 返回事实错误字段。没有可冻结的视觉模型，因此 P11.5、P11.12 的视觉成功门禁未关闭。
2. `R11-REVIEW`：本报告等待本授权周期的新独立对抗 Review；上一周期第二轮 `CHANGES_REQUIRED` 只作为历史输入，不能冒充当前 PASS。
3. `R11-HARNESS`：structure、manifest、references 已通过，但 pyproject/uv.lock 指纹需按已批准范围同步后再复跑 drift。
4. `R11-TEXT-LATENCY`：DOM 有一次 live 成功，ARIA 修复后一次因模型超时停止；当前样本不足以宣称稳定 p95 或生产收益。

typed API 保持业务默认。DOM/ARIA 可作为 `LAB_ONLY CANDIDATE`，前提是页面结构、可访问名称和模型延迟适用；Hybrid 仍需同版本结构/截图并在冲突时停止；截图 GUI 保持 `BLOCKED / EXPERIMENT ONLY`，直到真实图片模型读对两张合成图并完成同一真实 ERP 单据的 API/Web/GUI 对照。Phase 11 不授予业务 Runtime 或 ERP 写入权限，不进入 Phase 12。

## 8. 新周期剩余工作

1. 在当前实现和本报告上启动一次独立对抗 Review；如需修复，先补回归和受影响门禁，最多一轮复查。
2. Review PASS 后，按已批准范围只同步 `.harness/source-index.json` 中 pyproject/uv.lock 两个来源指纹及 `.harness/manifest.json` 管理哈希；不修改 README 或其它 Harness 内容。
3. 复跑 structure、manifest、references、drift、全量静态/测试门禁，更新最终文档 HEAD 和哈希。
4. 只有图片探测和真实 GUI 三方对照也通过时，才可能将阶段状态改为 `COMPLETED / PASS / READY FOR NEXT PHASE`；当前证据不满足，保留 `BLOCKED`。

## 9. 手工验证

```bash
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui --help
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui smoke --mode dom
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui smoke --mode aria
uv run --python 3.14 --group web-gui-lab python -m labs.web_gui benchmark --suite synthetic --engine deterministic --repeats 3
```

真实 provider/ERP 验收需在受保护 shell 中先 `source env/dev/.env`，再显式使用 `--engine live`；只检查状态、字段、调用摘要、版本和 policy events，不打印环境变量、prompt、截图正文或原始响应。

## 10. 学习记录边界

本阶段按用户指令不生成学习笔记、不自动安排问答、不触发 Assignment、不调用 c2c/codex-with-chatgpt。阶段结束后只有用户明确触发答疑，才按学习笔记规则记录问题和回答。
