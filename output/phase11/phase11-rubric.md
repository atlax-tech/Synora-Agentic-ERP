# Phase 11 Rubric（当前收口）

评分范围绑定实现 HEAD `c986c27`、synthetic/ERP deterministic evidence、live DOM/ARIA 摘要、live ERP API/Web/GUI 尝试、图片探测和 Phase11 全量门禁。视觉 provider、live GUI mismatch、独立 Review 和 Harness drift 是明确限制，不用成功的 test double 掩盖。

| 维度 | 分数（0–4） | 证据与当前边界 |
| --- | ---: | --- |
| D1 需求与业务正确性 | 3 | 统一四字段只读任务、五种观察入口、目标不存在/加载/权限/冲突终态；live DOM 有一次真实成功，真实 GUI 仍 mismatch |
| D2 身份、权限与范围 | 3 | synthetic loopback、真实 ERP 同一 Buyer/Company/单据版本、typed Gateway 和精确 Web allowlist；无写入授权 |
| D3 状态、并发、幂等与恢复 | 3 | action/model/wall/output 预算、无进展、陈旧 observation、异步/会话/页面变化故障和修复；本阶段不增加业务写入并发 |
| D4 Agent 信任与成本 | 3 | 严格模型 wire、临时引用、坐标、调用/usage 上限、trusted oracle、live/deterministic 分离；ARIA 超时和视觉事实错误被保留 |
| D5 安全与数据保护 | 4 | origin/路由/方法/下载/弹窗/写入白名单，Service Worker 禁用，响应大小、注入、陈旧坐标、ERP 遮罩和无秘密日志证据 |
| D6 UI、可访问性与双语 | 2 | role/name、ARIA 快照、焦点样式、表头语义、空/错误/权限状态有测试；实验页文案仍非完整双语/ERP 无障碍审计 |
| D7 测试、真实集成与复现 | 3 | `make unit` 936 passed、Frappe 248 tests、Phase11 80 passed、synthetic 45/33、ERP deterministic 3/3 和 live Web MATCHED；真实 GUI 未成功 |
| D8 治理、追踪与非虚构 | 3 | 提交边界、单一日志、失败前后 artifact、hash、Adoption Card、风险和报告均更新；新 Review/Harness 尚未闭合 |
| D9 简洁性与可运维性 | 3 | 独立可选依赖组、单 CLI、错误码/预算/证据路径清晰；模型 provider/浏览器版本变化需重新探测 |

合计：`27/36`，平均 `3.00`。

门槛检查：D1/D2/D3/D5/D7/D8 均 ≥3；当前 P0=`0`；P1 视觉能力阻断仍开放；P2 均有 owner、下一门禁和复验条件。平均分满足当前阶段 rubric，但不等于阶段出口通过。

评分口径：`0=无证据或未知`、`1=失败/重大缺口`、`2=部分通过且有未闭环风险`、`3=达到当前阶段要求`、`4=有冗余证据并具备可复用基线`。阶段出口还必须满足真实图片探测、真实 ERP API/Web/GUI 对照、独立 Review PASS 和 Harness drift 清零。
