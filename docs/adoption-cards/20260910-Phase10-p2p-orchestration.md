# Phase 10 P2P 编排恢复 Adoption Card

状态：`LAB_ONLY / PASS / KEEP_FIXED_WORKFLOW_BUSINESS_BASELINE`

日期：2026-09-10

## 范围

本卡记录 P10.7 的固定 test double 对照：同一组 PO→PR→PI→Payment Entry
步骤、同一初始 ERP 事实、同一 `recorded-p2p-orchestration-v1` 模型标识，分别运行
单 Agent、固定 Workflow 和多 Agent。事件矩阵包含乱序、重复、延迟定时唤醒和进程重启。
实验代码位于 `labs/p2p_orchestration/`，不连接 Frappe，不持有凭证，不改变业务主线。

## Evidence

机器可读结果：`output/phase10/phase10-p2p-orchestration-recorded-v1.json`；数据集 digest：`b9a844f21aa3ca498306dfd96ca9c1098b86fe9757db078eb0519f6827b4540a`。

| Strategy | Quality | Safety violations | Latency (ms) | Tokens | Recovery | Complexity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| single_agent | 1.000 | 4 | 100 | 320 | 0.000 | 1 |
| fixed_workflow | 1.000 | 0 | 71 | 0 | 1.000 | 2 |
| multi_agent | 1.000 | 0 | 141 | 640 | 1.000 | 4 |

单 Agent 的安全违规和恢复失败被保留为原始基线；固定 Workflow 与多 Agent 都忽略
事件中的伪造授权提示，只以持久步骤依赖和当前事实推进。多 Agent 没有在相同任务上
带来质量、安全或恢复收益，增加了延迟、Token 和运维复杂度。

## Decision

`KEEP_FIXED_WORKFLOW_BUSINESS_BASELINE`：业务主线继续使用已验证的手写持久编排；
多 Agent 保留为 `LAB_ONLY`，不因角色数量增加而获得 ERP 写权限。这个结论只适用于
本固定小数据集和 test double，不代表生产性能或客户收益。

后续若要重新评估，需要用户批准的新数据集、真实故障/恢复证据、相同 ERP 初始条件、
权限与审计检查，以及新的 Adoption Card 和独立 Review。
