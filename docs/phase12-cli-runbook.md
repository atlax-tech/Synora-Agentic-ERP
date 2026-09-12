# Phase 12 CLI 复跑手册

所有命令都在仓库根目录执行，并固定使用项目 Python：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . <command>
```

默认 `evaluate` 使用本地 deterministic replay，不会发起网络请求。每条命令成功返回 0；数据非法、artifact 缺失或安全门禁失败返回 2。评测失败、`REJECTED` 或 `INCONCLUSIVE` 是实验结果，不等同于 CLI 失败。

## 建立数据和候选

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . audit-data
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . prepare-data
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . make-candidates
```

`audit-data` 只读取显式 Phase 11 白名单，输出脱敏审核结果；`make-candidates` 要求审核 artifact 存在，并且候选只能写入 `output/phase12/candidates/`。

## Replay 对照

开发集三次重复：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split dev --method baseline --repeats 3
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split dev --method reflection --repeats 3
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split dev --method best-of-3 --repeats 3
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split dev --method prompt-candidate --repeats 3
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split dev --method skill-candidate --repeats 3
```

冻结测试集只运行基线、已经生成的候选和 dev 规则选出的一个方法：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split test --method baseline --repeats 3
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split test --method prompt-candidate --repeats 3
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split test --method skill-candidate --repeats 3
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split test --method reflection --repeats 3
```

候选方法会读取唯一的同类候选 artifact，并把 `candidate_id` 写入每条记录。若同类候选超过一个，应先建立新的显式选择流程，不可随意挑一个运行。

## 正式计划与 dev 选择

新计划必须显式指定 `Reflection` 或 `Best-of-3`，不能把 baseline 作为 selected method：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . freeze-experiment --plan-id phase12-plan-r5-v4 --model glm-5.3-flash --selected-method reflection
```

若历史 manifest 在 test 前没有冻结该选择，不修改旧 manifest；在完整 live dev 证据上追加一次不可变、明确 `pre_registered=false` 的选择回执：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . select-test-method
```

该命令只接受同一 plan、model、dataset 下 Reflection 与 Best-of-3 各自完整的 24 案例 × 3 重复 dev 证据，并按安全合格、verifier 成功率、调用数、延迟确定 selected method。

## LAB 选择和回滚

当前复跑使用已有 Prompt 候选和一条开发证据：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . select-lab --candidate-id phase12-prompt-609c62e2b621bb14 --previous-id native-agent/A --evidence phase12-exp-replay-prompt-candidate-1-phase12-complete-read-06-v1 --reason 'dev candidate evidence'
```

`select-lab` 会记录父/新版本内容 digest，并原子更新 `output/phase12/active-version.json`。运行后从命令输出取得新的 `selection_id`，再执行：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . rollback-lab --selection-id <selection_id> --evidence phase12-exp-replay-prompt-candidate-1-phase12-complete-read-06-v1 --reason 'restore native baseline'
```

回滚会重新读取版本内容并把 active 指针恢复到父版本；内容变更、路径穿越、旧 receipt 重复写入或 evidence 不属于该候选的都会返回 2。

## 本地策略训练

先运行三个 SFT，再运行从同 seed SFT 权重起步的 DPO 和 REINFORCE：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . train --method sft --seed 17
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . train --method sft --seed 29
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . train --method sft --seed 43
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . train --method dpo --seed 17
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . train --method dpo --seed 29
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . train --method dpo --seed 43
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . train --method reinforce --seed 17
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . train --method reinforce --seed 29
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . train --method reinforce --seed 43
```

训练权重是有限数值 JSON，只能在新进程读回；不加载 pickle、脚本或远程模型代码。

初始化、固定 seed 的随机策略和规则策略也必须在同一 task environment 评测，且与训练权重评测分开：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind initial --seed 17 --split dev
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind initial --seed 17 --split test
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind initial --seed 29 --split dev
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind initial --seed 29 --split test
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind initial --seed 43 --split dev
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind initial --seed 43 --split test
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind random --seed 17 --split dev
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind random --seed 17 --split test
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind random --seed 29 --split dev
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind random --seed 29 --split test
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind random --seed 43 --split dev
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind random --seed 43 --split test
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind rule --split dev
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate-baseline --kind rule --split test
```

对 `initial`/`random` 的 seed 使用 `17、29、43`，并分别执行 `dev、test`；`rule` 不绑定训练权重，分别执行两个 split。它们均为零 Provider 调用，但仍进入 task-evaluation 分母。

## 报告和验收

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . bootstrap
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . report
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . verify-artifacts
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . verify-stage
```

`bootstrap` 从活动目录的不可变 live test 记录重新计算，统一写 `method_a=candidate`、`method_b=baseline`；不接受手工改数。`report` 生成一套共享 suffix 的阶段报告、Adoption Card 和 JSON summary。报告把 replay、live dev、live test、本地权重和本地基线分行；Adoption Card 分开列出安全门禁、统计区间和采用决定。`verify-artifacts` 检查文件/绑定完整性，`verify-stage` 才检查批准计划、完整试验矩阵、历史账本、训练与本地 task eval、bootstrap、回滚、Review 和 Harness 出口。

## Live Provider（显式批次）

live 五种方法共用一个 Provider 入口，并且需要用户环境中已配置 provider；不要在日志或终端输出 secret。每个新批次必须使用未重复的显式 `--batch-id`：

```bash
set -a
source env/dev/.env
set +a
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine live --split test --method baseline --repeats 1 --batch-id r5-test-baseline
```

冻结计划完成后，dev 使用 `baseline、reflection、best-of-3、prompt-candidate、skill-candidate` 各自唯一 batch；test 使用 `baseline、prompt-candidate、skill-candidate` 和选择回执中的方法。沿用同一 baseline，不因候选表现差而重跑已有结果。下面列出当前收口批次的完整复跑入口；换批次复跑时必须同时换 `--batch-id`：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine live --split dev --method baseline --repeats 3 --batch-id r5-dev-baseline
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine live --split dev --method reflection --repeats 3 --batch-id r5-dev-reflection
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine live --split dev --method best-of-3 --repeats 3 --batch-id r5-dev-best3
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine live --split dev --method prompt-candidate --repeats 3 --batch-id r5-dev-prompt
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine live --split dev --method skill-candidate --repeats 3 --batch-id r5-dev-skill
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine live --split test --method baseline --repeats 3 --batch-id r5-test-baseline
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine live --split test --method prompt-candidate --repeats 3 --batch-id r5-test-prompt
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine live --split test --method skill-candidate --repeats 3 --batch-id r5-test-skill
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine live --split test --method reflection --repeats 3 --batch-id r5-test-reflection-posthoc
```

Skill test 若发生中断，恢复批次必须使用唯一的 `r5-test-skill-recovery-<n>`，只补未完成 reservation；不得复制旧 JSONL 或重放已记录请求。若选择回执改选 Best-of-3，则 test 的最后一行改用新的唯一 batch，并在报告中披露这是 post-hoc 补充。

请求发出前在 `output/phase12/live-reservations.jsonl` 原子占用调用预算，失败不自动重试；连续三次连接、认证或协议/传输失败会阻塞批次。Provider 未报告 usage 时记录为未知。相同 batch 的 reservation key 在重启后不会再次调用；网络恢复需显式传入新的 `--batch-id`。当前活动账与历史归档账分开披露，活动目录计数不等于 Phase 12 全部成本。

## 输出位置和安全边界

活动证据只在 `output/phase12/`，失效批次在带 `README.md` 的 `output/phase12-invalid-*` 归档。所有能力均为 `LAB_ONLY`，不会修改业务 Prompt、Skill、policy、permission、tools、ERP/Frappe 核心、数据库或 `.env*`。
