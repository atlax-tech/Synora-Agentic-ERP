# Phase 11 Rubric（阶段报告草稿）

评分依据 `docs/PLAN.md` §4.6，范围为代码/证据 HEAD `9cf8e9e`、Phase 11 测试、真实 ERP API/Web、脱敏 GUI 边界、synthetic benchmark 和失败修复证据。第一轮独立对抗 Review 的 `CHANGES_REQUIRED` 已按修复提交处理，第二轮尚未完成。

| 维度 | 分数（0–4） | 证据与当前边界 |
| --- | ---: | --- |
| D1 需求与业务正确性 | 3 | 五种观察入口、统一四字段只读任务、目标不存在/加载/权限/冲突终态和 45 个 synthetic trial；真实 GUI provider 阻塞使三方任务未闭合 |
| D2 身份、权限与范围 | 3 | synthetic loopback、真实 ERP 同一 Buyer/Company/单据版本、API capability 和精确 Web allowlist；视觉无法宣称真实模型成功 |
| D3 状态、并发、幂等与恢复 | 3 | 动作/模型/墙钟/输出预算、无进展、陈旧页面版本、异步/登录失效/页面变化故障；本阶段没有新增业务写入并发场景 |
| D4 Agent 信任与成本 | 3 | 模型输出严格动作契约、调用/耗时/输出上限、trusted oracle、输入/输出摘要、usage `null` 策略和 test double 标记；真实 provider 不可用 |
| D5 安全与数据保护 | 4 | origin/路径/方法/下载/弹窗/写入白名单，Service Worker 禁用，重定向/响应/正文上限，注入和陈旧坐标/版本拒绝，ERP 截图遮罩与敏感标记门禁；无未授权副作用证据 |
| D6 UI、可访问性与双语 | 2 | ARIA role/name、焦点样式、表头语义、空/错误/权限状态有测试；实验页面当前只有英文，未完成完整 ERP 双语/无障碍审计 |
| D7 测试、真实集成与复现 | 3 | `make unit` 915 passed、Frappe 248 tests、修复后重新冻结的三次 synthetic/ERP 报告、修复前后页面变化摘要；真实图片理解仍为环境阻塞 |
| D8 治理、追踪与非虚构 | 3 | 每轮开发日志、提交边界、失败保留、Adoption Card 和 artifact hash，第一轮 Review 问题与修复可追踪；Harness 对 pyproject/uv.lock 的 drift 尚待受保护同步 |
| D9 简洁性与可运维性 | 3 | 独立可选依赖组、单模块 CLI、明确超时/错误码/证据路径；真实 provider 和跨版本浏览器升级需重新探测 |

合计：`27/36`，平均 `3.00`。

门槛检查：D1/D2/D3/D5/D7/D8 均 `≥3`；当前已知未关闭 P0/P1 为 `0`。阶段总体仍不能标为 `COMPLETED/PASS`，因为真实 ERP API/Web/GUI 三方对照被 `VISION_PROVIDER_UNAVAILABLE` 阻塞、第二轮 Review 尚未通过，且 Harness drift 尚未完成授权同步。
