# Phase 11 Adoption Card

状态：`PENDING_REVIEW_RECHECK / BLOCKED / VISION_PROVIDER_UNAVAILABLE / LIVE_GUI_MISMATCH / HARNESS_DRIFT`。

实现代码冻结 HEAD（待最终文档提交后重新记录）：`15a476e`。本卡只描述实验和固定开发 ERP 只读证据，不授予业务 Runtime 或 ERP 写入权限。

主要证据：

- Synthetic deterministic：[phase11-benchmark-synthetic.json](phase11-benchmark-synthetic.json)，SHA-256 `1956c43950d3a22a407827cc7f8f1991eb64a9324b49a14bb4bd4c72b80487f6`，45 条业务 trial、33 条故障记录。
- 真实 ERP deterministic API/Web：[phase11-benchmark-erp-readonly-deterministic-20260911.json](phase11-benchmark-erp-readonly-deterministic-20260911.json)，SHA-256 `b8a12b8cf958053583f0f2c1f52de18e5ed151747154f5621fbf7a3be1805f24`，3/3 `MATCHED`。
- 真实 ERP live API/Web/GUI：[phase11-benchmark-erp-readonly-live-d444e97.json](phase11-benchmark-erp-readonly-live-d444e97.json)，SHA-256 `1973dbe929dc2eefc989cf7a79e074f212df98c2067e7ce793d05cba553e08bf`，API/Web `MATCHED`，GUI `INCOMPLETE / visual_fields_mismatch`。
- live 汇总校正：[phase11-benchmark-erp-readonly-live-reconciled-8fd20d8.json](phase11-benchmark-erp-readonly-live-reconciled-8fd20d8.json)，SHA-256 `4c305afa040d884b786eee0f0c26e27b129516b870d1ab3c57c458ea457cbf6c`，逐 trial 汇总保留 `INCOMPLETE=1`。
- 图片探测：[phase11-vision-probe-925b96a.json](phase11-vision-probe-925b96a.json)，SHA-256 `273cc7971d7191260cd393f08de87b45a20d76c619de06c98674628598dd12fb`；四个已配置 role 均未通过两张合成图的可读性验证，状态 `VISION_PROVIDER_UNAVAILABLE`。

## 方法决策

| 方法 | 决策 | 适用条件 | 当前证据和限制 |
| --- | --- | --- | --- |
| typed API | `KEEP BUSINESS DEFAULT` | 采购事实有稳定、受治理的 typed Gateway | 固定 ERP API/Web 三次 `MATCHED`；继续经过现有 Run、capability、权限和审计边界 |
| DOM | `LAB_ONLY CANDIDATE` | 结构、临时引用和目标唯一且模型延迟在预算内 | live `glm-5.3-flash` 成功读取 synthetic DOM 一次；deterministic 9/9；未注册业务 Runtime |
| ARIA | `LAB_ONLY CANDIDATE WITH TIMEOUT LIMIT` | role、accessible name、键盘路径稳定且模型及时返回 | 修复前目标引用缺失已保留；修复后进入第二次调用但因 10 秒动作上限安全超时；没有稳定率结论 |
| screenshot GUI | `BLOCKED / EXPERIMENT ONLY` | 脱敏可靠、图片模型通过内容探测、坐标动作可确认 | backup `grok-4.5` 一次返回与 trusted API 不一致；真实三方成功缺失，不能使用 test double 替代 |
| hybrid | `LAB_ONLY CANDIDATE` | 结构和截图来自同一 page version，冲突能停止 | deterministic test double 9/9；live 视觉 provider 尚无通过证据，不能静默降级 |

## 统一任务

找到指定采购单，读取单号、供应商、业务状态和币种，并说明观察是否完整。目标不存在、权限拒绝、未完成加载、登录失效、观察冲突、陈旧引用和预算耗尽都返回明确终态；数量明细不跨单位相加。

## 采用边界

- 所有新增执行器仍为 `LAB_ONLY`，只绑定 loopback/独立会话，不进入业务 Runtime。
- deterministic 视觉/Hybrid 成绩明确是 `scripted-fixture-replay`，只证明执行器回归；live DOM 的单次成功也不构成生产收益或模型稳定性承诺。
- 真实 ERP Web 结果只读且版本稳定；live GUI 字段必须同时满足截图观察、可信 API 版本和安全事件检查才可成功。
- usage 缺失保持 `null`，没有核验价格就不换算货币成本；不自动反复调用付费 provider。

## 重新评估触发器

1. `probe-vision` 用两张内容不同的合成 PNG 返回严格结构化且逐图可核验的四字段，冻结 role/model/protocol/预算。
2. 同一真实 ERP 单据、同一脱敏映射和同一页面版本完成 API/Web/GUI 三方只读对照，四字段一致且无副作用。
3. 新页面、provider 或浏览器版本变化时保留旧失败，先补回归测试和独立审查，再刷新本卡。

## 当前风险和门禁

- 图片 provider 能力和 live GUI mismatch 是阶段必做阻断；不得用 DOM/API 答案或 recorded response 填补。
- 首轮独立 Review 的四项修复已完成，当前等待同一授权周期的唯一一次复查；首轮 `CHANGES_REQUIRED` 保留为修复依据，不能当作当前 PASS。
- `.harness` structure/manifest/references 已通过，pyproject/uv.lock 指纹同步和 drift 复跑待最终审查后执行。

本卡保持 `BLOCKED`，typed API 继续作为业务默认；Phase 11 结束后停止，不进入 Phase 12。
