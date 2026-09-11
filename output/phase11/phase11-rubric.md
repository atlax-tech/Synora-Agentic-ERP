# Phase 11 Rubric（最终审查前）

评分绑定实现 HEAD `10cb0e1`、GLM 双图 probe、当前 synthetic live、当前 ERP live、当前脱敏边界和 Phase11 全量门禁。旧阻塞 artifact 只作历史证据。

| 维度 | 分数（0–4） | 证据与边界 |
| --- | ---: | --- |
| D1 需求与业务正确性 | 3 | 统一四字段只读任务、五种观察入口和明确失败终态；视觉/DOM 仍受模型波动影响 |
| D2 身份、权限与范围 | 3 | 独立会话、同一 Buyer/Company/单据版本、typed Gateway 和精确 Web allowlist；无写入授权 |
| D3 状态、并发、幂等与恢复 | 3 | action/model/wall/output 预算、无进展、陈旧观察、异步/会话/页面变化恢复；本阶段不增加业务写入并发 |
| D4 Agent 信任与成本 | 3 | 严格 wire、临时引用、坐标、调用/usage 上限、trusted oracle 和 live/deterministic 分离；Grok 内容失败保留 |
| D5 安全与数据保护 | 4 | origin/路由/方法/下载/弹窗/写入白名单，Service Worker 禁用，响应大小、注入、陈旧坐标、ERP 脱敏和无秘密日志 |
| D6 UI、可访问性与双语 | 2 | role/name、ARIA、焦点、表头、空/错误/权限状态有测试；不是完整 ERP 无障碍审计 |
| D7 测试、真实集成与复现 | 4 | 全量单测/集成通过，112 Phase11 测试、GLM live 轨迹、45+78 synthetic、ERP API/Web/GUI 三方证据均可复跑 |
| D8 治理、追踪与非虚构 | 3 | 失败前后 artifact、SHA、当前治理文档和风险登记齐备；最终 Review/Harness 尚未闭合 |
| D9 简洁性与可运维性 | 3 | 独立可选依赖组、单 CLI、错误码/预算/证据路径清晰；provider 变化需重新探测 |

合计：`28/36`，平均 `3.11`。

门槛检查：P0=`0`、P1=`0`；D6 为阶段明确限制，D8 在最终 Review PASS 和 Harness drift 清零后复核。即使视觉成绩良好，也不改变 typed API 的业务默认，不自动进入 Phase 12。
