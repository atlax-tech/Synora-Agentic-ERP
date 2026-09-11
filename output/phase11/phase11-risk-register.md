# Phase 11 风险登记（阶段报告草稿）

概率和影响按 `docs/PLAN.md` §4.6 取 1–4；分数为 `likelihood × impact`。`OPEN` 项有 owner、下一门禁和复验条件，不能被 benchmark 成功率隐藏。

| ID | 风险 | L×I | 级别 | 状态 | owner | 下一门禁与复验 |
| --- | --- | ---: | --- | --- | --- | --- |
| R11-VISION | 已配置角色均无法返回可核验图片观察 | 3×3=9 | P2 | `OPEN / BLOCKER` | 实验维护者 | `probe-vision` 对两张新合成图返回严格四字段；否则保持视觉依赖阻塞 |
| R11-REDACTION | 未知像素敏感内容可能逃过文本标记检查 | 2×4=8 | P2 | `MITIGATED` | 实验维护者 | 新页面 selector/布局变化时拒绝外发；人工检查和敏感检测复验 |
| R11-SIDE-EFFECT | 浏览器页面诱导外域、下载、弹窗或写入 | 2×4=8 | P2 | `MITIGATED` | 实验维护者 | 重跑 security probe；任一未授权副作用立即 P0/P1 停止 |
| R11-INJECTION | 页面文本操纵模型提出越权动作 | 3×4=12 | P1 | `CLOSED_FOR_SCOPE` | 实验维护者 | 动作引用、类型、origin、方法和目标持续由可信执行器校验；新增动作先补负面测试 |
| R11-STALE | 截图/结构引用陈旧导致误点 | 3×3=9 | P2 | `MITIGATED` | 实验维护者 | 页面版本和 observation ID 不一致时安全停止；重跑坐标陈旧用例 |
| R11-AUTH | 登录失效后旧事实被误宣称为当前成功 | 2×4=8 | P2 | `MITIGATED` | 实验维护者 | 每次独立会话；过期场景必须 `AUTH_REQUIRED`，登录页不得进入模型输入 |
| R11-DRIFT | ERP 单据在 API/Web 比较期间变化 | 2×3=6 | P2 | `CLOSED_FOR_FROZEN_RUN` | ERP 验证维护者 | API before/after 修改时间变化就记 `STATE_DRIFT`，不得覆盖旧报告 |
| R11-DOUBLE | test double 成绩被误读为真实视觉模型成绩 | 2×3=6 | P2 | `OPEN` | 阶段报告维护者 | Adoption Card 保持 `EXPERIMENT/BLOCKED`；provider 通过后重新跑同模型三方任务 |
| R11-PYTHON | 主机默认 uv 解释器为 3.13，项目要求 3.14 | 2×2=4 | P3 | `OPEN` | 工程维护者 | 使用 `--python 3.14`；不降低 requires-python，环境修复后复跑计划命令 |
| R11-HARNESS | pyproject/uv.lock 依赖组变更尚未写入 managed fingerprint | 3×3=9 | P2 | `OPEN / BLOCKER` | Harness 维护者 | 取得文件级批准后运行 harness-update；structure/manifest/references/drift 全部清零 |
| R11-USAGE | provider usage/价格不可核验 | 3×2=6 | P2 | `MITIGATED` | 实验维护者 | 继续记录 `null`，只报告延迟范围和调用数，不生成货币成本 |

当前处置计数：P0=`0`；P1=`0` 未关闭（R11-INJECTION 已由边界和负面测试关闭）；P2 未关闭项有 owner、下一门禁和复验条件；P3 进入环境改进清单。
