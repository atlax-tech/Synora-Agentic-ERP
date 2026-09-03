# Phase 9 P9.4 编排模式对照决策包

本报告使用同一份 P9.1 固定 12 案例、同一 typed Planner/Reviewer 契约和同一 recorded role adapter。
LangGraph 只作为显式节点路由壳；所有模式均为 LAB_ONLY，不连接 Frappe、Gateway 或真实 capability。

- code HEAD: `fcab82401b5671259271e7d343bcd7cab1f8899a`
- case spec SHA-256: `05d22c0ddc3617079d279d664a4422a541861af83c64fbb6d9edcc7e2a56acb7`
- deterministic fingerprint: `cfe085759636b6ebbd8010bd3cb715ce208874c7ed911562ef69cae9ee8ee1e3`
- security: `PASS`（未授权工具、ERP 写入、跨范围和 Secret 泄漏均为 0）
- 成本代理：本地 recorded arm 不使用金额价格，比较 prompt/completion/reasoning token 与 elapsed_ms。

## 五种模式

| 模式 | 任务正确率 | 有效解释率 | 安全回退率 | p95 ms | 总 token | 模型调用 | 复杂度备注 |
|---|---:|---:|---:|---:|---:|---:|---|
| `supervisor` | 1.000 | 0.583 | 0.417 | 12 | 3517 | 23 | shared LOC 1308; mode LOC 14; deps stdlib |
| `peer_to_peer` | 1.000 | 0.583 | 0.417 | 12 | 3517 | 23 | shared LOC 1308; mode LOC 12; deps stdlib |
| `hierarchical` | 1.000 | 0.583 | 0.417 | 12 | 3517 | 23 | shared LOC 1308; mode LOC 16; deps stdlib |
| `managed_agent_tool` | 1.000 | 0.583 | 0.417 | 12 | 3517 | 23 | shared LOC 1308; mode LOC 15; deps stdlib |
| `explicit_graph_node` | 1.000 | 0.583 | 0.417 | 12 | 3517 | 23 | shared LOC 1308; mode LOC 168; deps langgraph==1.2.11 |

## 固定六类轨迹

NORMAL、CONFLICT、TIMEOUT、CANCELLED、INVALID_OUTPUT、LOOP_ATTACK 均由同一 adapter 脚本驱动；每个模式各执行一次。
循环攻击在一次修订后以 `LOOP_BLOCKED` 停止，不能触发第四次模型调用。
Explicit graph 的 checkpoint 只包含 `case_id/plan_digest/stage/decision/stop_code` 等编排键，不包含 ERP facts、权限 或 capability。

## 采用边界

此处仅形成 P9.4 工程对照证据。是否接入 `/enhance` 仍由 P9.5 按用户批准的 量化门槛和真实同模型 A/B 决定。
