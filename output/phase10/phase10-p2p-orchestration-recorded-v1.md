# Phase 10 P2P 编排恢复对照实验

状态: `LAB_ONLY / PASS`

数据集: `phase10-p2p-events-v1`; digest: `b9a844f21aa3ca498306dfd96ca9c1098b86fe9757db078eb0519f6827b4540a`; 模型标识: `recorded-p2p-orchestration-v1`。
事件只负责唤醒和重检, 事件中的授权提示被忽略; 三种策略读取相同的内存 ERP 事实快照。

| Strategy | Quality | Safety violations | Latency (ms) | Tokens | Recovery | Complexity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| single_agent | 1.000 | 4 | 100 | 320 | 0.000 | 1 |
| fixed_workflow | 1.000 | 0 | 71 | 0 | 1.000 | 2 |
| multi_agent | 1.000 | 0 | 141 | 640 | 1.000 | 4 |

事件矩阵覆盖重复、乱序、延迟定时唤醒和进程重启。单 Agent 作为原始基线保留失败; 固定 Workflow 使用持久步骤和依赖重检, 多 Agent 增加角色协调但不获得 ERP 写权限。

结论: `KEEP_FIXED_WORKFLOW_BUSINESS_BASELINE`。本实验不授权任何生产采用; 完整 JSON 与失败数据按原样保留。

recorded test double only; no ERP write, credential, or event authorization。
