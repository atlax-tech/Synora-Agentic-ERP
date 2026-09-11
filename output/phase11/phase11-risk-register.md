# Phase 11 风险登记（当前收口）

概率和影响按 `docs/PLAN.md` §4.6 取 1–4；分数为 likelihood × impact。`OPEN` 项必须有 owner、下一门禁和复验条件，不能被 benchmark 成功率隐藏。

| ID | 风险 | L×I | 级别 | 状态 | owner | 下一门禁与复验 |
| --- | --- | ---: | --- | --- | --- | --- |
| R11-VISION | 已配置图片 role 未返回两张合成图的可核验内容；真实 GUI 一次返回字段 mismatch | 4×3=12 | P1 | `OPEN / BLOCKER` | 实验维护者 | 修复配置/协议后重新做一次两图探测；四字段逐图匹配后冻结模型，否则保持阻塞 |
| R11-REDACTION | 未知页面布局或遮罩目标缺失可能把敏感像素外发 | 2×4=8 | P2 | `MITIGATED` | 实验维护者 | selector/布局变化时拒绝截图；人工检查、敏感标记和 redaction artifact 复验 |
| R11-SIDE-EFFECT | 页面诱导外域、下载、弹窗或写入 | 2×4=8 | P2 | `MITIGATED` | 实验维护者 | 重跑安全 probe；任一未授权副作用立即按 P0/P1 停止 |
| R11-INJECTION | 页面文本操纵模型提出越权动作 | 3×4=12 | P1 | `CLOSED_FOR_SCOPE` | 实验维护者 | observation 引用、动作类型、origin、路由和方法持续由可信执行器校验；新增动作先补负面测试 |
| R11-STALE | 截图/结构 observation 陈旧导致误点或错误读取 | 3×3=9 | P2 | `MITIGATED` | 实验维护者 | Observation ID/page version/坐标边界不一致必须安全停止；复跑 stale 坐标和页面变化用例 |
| R11-AUTH | 登录失效后旧事实被误宣称为当前成功 | 2×4=8 | P2 | `MITIGATED` | 实验维护者 | 每次独立会话；过期场景 `AUTH_REQUIRED`；登录页不进模型观察或公共证据 |
| R11-DRIFT | ERP 单据在 API/Web/GUI 比较期间变化 | 2×3=6 | P2 | `CLOSED_FOR_FROZEN_RUN` | ERP 验证维护者 | API before/after 修改时间变化记 `STATE_DRIFT`，不覆盖旧报告 |
| R11-DOUBLE | test double 或 API oracle 被误读为真实视觉模型成绩 | 3×3=9 | P2 | `OPEN / BLOCKER` | 阶段报告维护者 | 维持 `test_double_methods`、live artifact 和输入隔离；真实图片/三方门禁通过后才重算 |
| R11-TEXT-LATENCY | DOM 单次成功、ARIA 单次超时，样本不足以估计稳定延迟 | 3×2=6 | P2 | `OPEN / LIMITATION` | 实验维护者 | provider 预算稳定后完成冻结批次；当前只报告单次观测和安全停止，不宣称 p95 |
| R11-PYTHON | 主机默认 uv 解释器为 3.13，项目要求 3.14 | 2×2=4 | P3 | `OPEN` | 工程维护者 | 所有计划命令显式 `--python 3.14`；环境修复后重跑入口 |
| R11-HARNESS | pyproject/uv.lock 依赖组变化尚未写入 managed fingerprint | 3×3=9 | P2 | `OPEN / BLOCKER` | Harness 维护者 | Review PASS 后按已批准范围同步两个 source-index 条目和 manifest 管理哈希，再跑 drift |
| R11-USAGE | provider usage/价格不总是返回或无法核验 | 3×2=6 | P2 | `MITIGATED` | 实验维护者 | 缺失保持 `null`；只报告调用数和延迟，不生成货币成本 |
| R11-REVIEW | 当前授权周期的新独立对抗 Review 尚未完成 | 2×3=6 | P2 | `OPEN / PENDING` | 阶段执行者 | 当前报告和最终 diff 完成后启动一次；必要时最多一轮复查，未 PASS 不写阶段通过 |

当前处置计数：P0=`0`；未关闭 P1 为 R11-VISION（外部能力/事实错误阻断）；R11-INJECTION 已 `CLOSED_FOR_SCOPE`，不计为未关闭；P2 均有 owner、下一门禁和复验条件；P3 进入环境改进清单。阶段状态保持 `BLOCKED`，不进入 Phase 12。
