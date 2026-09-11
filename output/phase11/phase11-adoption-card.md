# Phase 11 Adoption Card

状态：`READY_FOR_FINAL_REVIEW / HARNESS_SYNC_PENDING`。

实现基线 HEAD：`89ded56`；本卡只描述 `LAB_ONLY` 实验和固定开发 ERP 只读证据，不授予业务 Runtime 或 ERP 写入权限。旧阻塞 artifact 保留为历史记录，当前结论只引用下列新证据。

主要证据：

- 真实图片探测：[phase11-vision-probe-r3-e0ffd6d.json](phase11-vision-probe-r3-e0ffd6d.json)，SHA-256 `7015be5535ad7405ac1c70ec135912179317137206ad6d905c01e7c2a95b564c`；assist/`glm-5.3-flash` 双图 `PASS`，backup/`grok-4.5` HTTP 200、Responses `output` 可解析但可信字段 `RESPONSE_CONTENT_MISMATCH`。
- synthetic live 冻结：[phase11-benchmark-synthetic-live-glm-e9e0ccf-r3.json](phase11-benchmark-synthetic-live-glm-e9e0ccf-r3.json)，SHA-256 `c20c9fa6f822c551fcf3660eb5fb30c2fccd32feda124a156cf84d4109c823c2`；45 主 trial、78 fault、安全记录，`test_double_methods=[]`。
- 真实 ERP live：[phase11-benchmark-erp-readonly-live-glm-89ded56-r3.json](phase11-benchmark-erp-readonly-live-glm-89ded56-r3.json)，SHA-256 `a36373bcb07902cf8848d1f3f947623ba5375c86d07e67fc2a37fe76bf73a38f`；API/Web `MATCHED=3/3`，GUI `SUCCEEDED=2/3`、一次 `BLOCKED/TRANSPORT_ERROR`，before/after 字段、revision、时间和 digest 已落盘。
- 当前视觉边界：[phase11-erp-visual-boundary-live-glm-89ded56.json](phase11-erp-visual-boundary-live-glm-89ded56.json)；脱敏 `READY`、任务字段保留、账号和导航隐藏、业务写入为 0。
- 页面变化复盘：[failure](phase11-page-change-failure-v1.json) 保留修复前失败；[repair](phase11-page-change-repair-v1.json) 为 `FIXED_AND_RETESTED/SUCCEEDED`。

## 方法决策

| 方法 | 决策 | 适用条件 | 当前证据和限制 |
| --- | --- | --- | --- |
| typed API | `KEEP BUSINESS DEFAULT` | 有稳定、受治理的 typed Gateway | 真实 ERP API/Web 三次一致，继续经过 Run、capability、权限和审计边界 |
| DOM | `LAB_ONLY CANDIDATE` | 结构稳定、临时引用唯一、模型在预算内 | live `glm-5.3-flash` 有成功轨迹；冻结批次 4/9 正确，失败保留，不宣称生产稳定率 |
| ARIA | `LAB_ONLY CANDIDATE` | role/name、焦点和键盘路径稳定 | 冻结批次 9/9 正确；只覆盖本实验页面，不等同 ERP 无障碍审计 |
| screenshot GUI | `LAB_ONLY CANDIDATE / EXPERIMENT ONLY` | 脱敏可靠、坐标可确认、模型能读当前截图 | GLM 双图真实 PASS，ERP GUI 2/3 成功；不接入业务 Runtime |
| hybrid | `LAB_ONLY CANDIDATE` | 结构和截图同一 page version，冲突即停止 | 冻结批次 8/9 正确；不能从视觉失败静默切 DOM/API |

统一任务：找到采购单并读取采购单号、供应商、业务状态和币种；目标不存在、权限、加载、登录、冲突、陈旧引用和预算问题都返回明确终态，未知字段不猜测。

## 采用边界和剩余风险

- 所有新增执行器只绑定 loopback/独立会话，保持 `LAB_ONLY`，不注册业务 Runtime，不执行 ERP 写入。
- Grok 的失败是内容校验不一致，不能改写为 provider 不可用；冻结后续对照使用已验证的 assist/`glm-5.3-flash`。
- 视觉和 DOM 的成功率受模型版本、响应结构和延迟波动影响；小样本只报告实际 trial，不生成生产 p95 或收益结论。
- 原始 `phase11-erp-visual-boundary.json`、旧 probe 和旧 live mismatch 文件保留为历史证据，不作为当前状态。
- 最终独立 Review PASS 和 Harness drift 清零是阶段出口前剩余门禁；通过后再把状态写入 PLAN。

本卡不进入 Phase 12；阶段结束前不生成学习笔记或自动问答。
