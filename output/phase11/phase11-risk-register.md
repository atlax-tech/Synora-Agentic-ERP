# Phase 11 风险登记（最终）

概率和影响按 `docs/PLAN.md` §4.6 取 1–4；分数为 likelihood × impact。当前实现基线 `98ee158`，真实图片和 ERP live 证据均已刷新；Review 与 Harness 同步均已完成；仅模型质量波动作为阶段外限制保留。

| ID | 风险 | L×I | 级别 | 状态 | owner | 下一门禁与复验 |
| --- | --- | ---: | --- | --- | --- | --- |
| R11-VISION | 已验证 GLM 图片链路；Grok 可解析但四字段内容不一致 | 2×3=6 | P2 | `MITIGATED / QUALITY_LIMIT` | 实验维护者 | provider/model 变化时重做双图探测；不把 Grok 失败改成能力不可用 |
| R11-REDACTION | 未知布局或遮罩目标缺失可能外发敏感像素 | 2×4=8 | P2 | `MITIGATED` | 实验维护者 | 当前边界 `READY`；selector/布局变化即拒绝截图并复验 |
| R11-SIDE-EFFECT | 页面诱导外域、下载、弹窗或写入 | 2×4=8 | P2 | `MITIGATED` | 实验维护者 | 安全 probe 全部 `SAFE_STOP`；任一副作用按 P0/P1 停止 |
| R11-INJECTION | 页面文本操纵模型提出越权动作 | 3×4=12 | P1 | `CLOSED_FOR_SCOPE` | 实验维护者 | 可信执行器持续校验 observation、动作、origin、路由和方法 |
| R11-STALE | 截图或结构 observation 陈旧导致误点 | 3×3=9 | P2 | `MITIGATED / RECHECKED` | 实验维护者 | 当前视觉有 page version；结构路径动作前复查，stale 用例已重跑 |
| R11-AUTH | 登录失效后旧事实被误宣称为当前成功 | 2×4=8 | P2 | `MITIGATED` | 实验维护者 | 每次独立会话；过期场景 `AUTH_REQUIRED`；登录页不进模型 |
| R11-DRIFT | ERP 在 API/Web/GUI 比较期间变化 | 2×3=6 | P2 | `MITIGATED` | ERP 验证维护者 | 新 artifact 三次 `versions_match=true`；变化记 `STATE_DRIFT` |
| R11-DOUBLE | test double 被误读为真实视觉成绩 | 2×3=6 | P2 | `CLOSED_FOR_SCOPE` | 阶段报告维护者 | live batch `test_double_methods=[]`，旧 deterministic 文件保持单独标记 |
| R11-TEXT-LATENCY | DOM/视觉模型延迟和结构波动，样本不足以推生产指标 | 3×2=6 | P2 | `OPEN / LIMITATION` | 实验维护者 | 只使用冻结批次实际中位数/范围；模型变化重跑三次 |
| R11-PYTHON | 默认 uv 主机可能选择 3.13，项目要求 3.14 | 2×2=4 | P3 | `MITIGATED` | 工程维护者 | 所有验收命令显式 `--python 3.14` |
| R11-HARNESS | pyproject/uv.lock source fingerprint 曾未同步 | 3×3=9 | P2 | `CLOSED / PASS` | Harness 维护者 | 已同步 docs/PLAN、pyproject、uv.lock 指纹；最终 drift=0 |
| R11-USAGE | provider usage/价格不总是返回或无法核验 | 3×2=6 | P2 | `MITIGATED` | 实验维护者 | 缺失保持 `null`；不生成货币成本 |
| R11-REVIEW | 最终独立审查发现的墙钟预算、当前 probe 引用和测试计数问题已修复 | 2×3=6 | P2 | `CLOSED / PASS` | 阶段执行者 | 三项 P2 逐项复核通过；机械文档问题已核销 |

当前计数：P0=`0`；未关闭 P1=`0`；开放 P2 仅为模型质量限制（`R11-TEXT-LATENCY`），有 owner、下一门禁和复验条件；Harness 与 Review 门禁均已关闭，P3 环境项已通过显式解释器规避。阶段为 `COMPLETED / PASS / READY FOR NEXT PHASE`，不进入 Phase 12。
