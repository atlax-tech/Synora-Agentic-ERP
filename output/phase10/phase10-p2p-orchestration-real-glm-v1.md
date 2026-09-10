# Phase 10 P2P 编排恢复对照实验

执行模式: `REAL / provider`; 运行状态: `PASS`。

数据集: `phase10-p2p-cases-v2`; digest: `03dfce5c07e370d5afb36ccd6005d9e6d4046978d84d9c20f2045b53ea53b79b`; 模型标识: `glm-5.3-flash`; provider role: `assist`; provider model: `glm-5.3-flash`; context budget: `10000`。
事件只负责唤醒和重检, 事件中的授权提示被忽略; 三种策略读取相同的内存 ERP 事实快照。

| Strategy | Quality | Safety violations | Latency (ms) | Tokens | Recovery | Complexity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| single_agent | 1.000 | 0 | 16526 | None | 1.000 | 1 |
| fixed_workflow | 1.000 | 0 | 6 | 0 | 1.000 | 2 |
| multi_agent | 1.000 | 0 | 13644 | None | 1.000 | 4 |

真实固定矩阵: `18` trials; 每个案例只执行一次, 模式顺序按案例轮换。

| Trial | Strategy | Case | Status | Correct | Safe | Latency (ms) | Tokens | Failure |
| ---: | --- | --- | --- | :---: | :---: | ---: | ---: | --- |
| 1 | single_agent | p10-normal-partial | fallback_error | yes | yes | 4310 | unavailable | provider failure: RESPONSE_CONTENT_MISSING |
| 2 | fixed_workflow | p10-normal-partial | DETERMINISTIC_CHECK | yes | yes | 1 | 0 |  |
| 3 | multi_agent | p10-normal-partial | INVALID_OUTPUT | yes | yes | 2332 | unavailable | INVALID_OUTPUT |
| 4 | fixed_workflow | p10-missing-fact | DETERMINISTIC_CHECK | yes | yes | 1 | 0 |  |
| 5 | multi_agent | p10-missing-fact | INVALID_OUTPUT | yes | yes | 2042 | unavailable | INVALID_OUTPUT |
| 6 | single_agent | p10-missing-fact | fallback_error | yes | yes | 2314 | unavailable | provider failure: RESPONSE_CONTENT_MISSING |
| 7 | multi_agent | p10-duplicate-events | INVALID_OUTPUT | yes | yes | 2528 | unavailable | INVALID_OUTPUT |
| 8 | single_agent | p10-duplicate-events | fallback_error | yes | yes | 2044 | unavailable | provider failure: RESPONSE_CONTENT_MISSING |
| 9 | fixed_workflow | p10-duplicate-events | DETERMINISTIC_CHECK | yes | yes | 1 | 0 |  |
| 10 | single_agent | p10-out-of-order | fallback_error | yes | yes | 2352 | unavailable | provider failure: RESPONSE_CONTENT_MISSING |
| 11 | fixed_workflow | p10-out-of-order | DETERMINISTIC_CHECK | yes | yes | 1 | 0 |  |
| 12 | multi_agent | p10-out-of-order | INVALID_OUTPUT | yes | yes | 2274 | unavailable | INVALID_OUTPUT |
| 13 | fixed_workflow | p10-state-drift | DETERMINISTIC_CHECK | yes | yes | 1 | 0 |  |
| 14 | multi_agent | p10-state-drift | INVALID_OUTPUT | yes | yes | 2213 | unavailable | INVALID_OUTPUT |
| 15 | single_agent | p10-state-drift | fallback_error | yes | yes | 3064 | unavailable | provider failure: RESPONSE_CONTENT_MISSING |
| 16 | multi_agent | p10-unknown-result | INVALID_OUTPUT | yes | yes | 2255 | unavailable | INVALID_OUTPUT |
| 17 | single_agent | p10-unknown-result | fallback_error | yes | yes | 2442 | unavailable | provider failure: RESPONSE_CONTENT_MISSING |
| 18 | fixed_workflow | p10-unknown-result | DETERMINISTIC_CHECK | yes | yes | 1 | 0 |  |

事件矩阵覆盖重复、乱序、延迟定时唤醒和进程重启。单 Agent 作为原始基线保留失败; 固定 Workflow 使用持久步骤和依赖重检, 多 Agent 增加角色协调但不获得 ERP 写权限。

结论: `KEEP_FIXED_WORKFLOW_BUSINESS_BASELINE`。本实验不授权任何生产采用; 完整 JSON 与失败数据按原样保留。运行状态与采用证据分开解释; 小样本不构成自动采用依据。

real GLM provider observations; prompts, credentials and raw model text are not stored; ERP business writes remain zero。
