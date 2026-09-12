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

冻结测试集只运行基线和已经生成的候选：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split test --method baseline --repeats 3
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split test --method prompt-candidate --repeats 3
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine replay --split test --method skill-candidate --repeats 3
```

候选方法会读取唯一的同类候选 artifact，并把 `candidate_id` 写入每条记录。若同类候选超过一个，应先建立新的显式选择流程，不可随意挑一个运行。

## LAB 选择和回滚

当前复跑使用已有 Prompt 候选和一条开发证据：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . select-lab --candidate-id phase12-prompt-609c62e2b621bb14 --previous-id native-agent/A --evidence phase12-exp-replay-prompt-candidate-1-phase12-complete-read-06-v1 --reason 'dev candidate evidence'
```

`select-lab` 会记录父/新版本内容 digest。运行后从命令输出取得新的 `selection_id`，再执行：

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . rollback-lab --selection-id <selection_id> --evidence phase12-exp-replay-prompt-candidate-1-phase12-complete-read-06-v1 --reason 'restore native baseline'
```

回滚会重新读取版本内容；内容变更、路径穿越或旧 receipt 重复写入都会返回 2。

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

## 报告和验收

```bash
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . report
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . verify-artifacts
```

`report` 生成 `output/phase12/phase12-stage-report-draft-<code>.md`、Adoption Card 和 JSON summary。`verify-artifacts` 会检查数据 digest、候选来源、候选评测绑定、选择版本 digest、训练权重和累计调用预算。

## Live Provider（显式选择）

live 只允许 baseline，并且需要用户环境中已配置 provider；不要在日志或终端输出 secret：

```bash
set -a
source env/dev/.env
set +a
uv run --frozen --python 3.14 python -m labs.self_improvement.cli --root . evaluate --engine live --split test --method baseline --repeats 1
```

请求发出前占用调用预算，失败不自动重试；连续三次连接、认证或协议/传输失败会阻塞批次。Provider 未报告 usage 时记录为未知。当前证据保留一轮 24 条 live 记录；一次三重复跑因 provider 长连接无响应而中断，没有把半批输出写成结果。

## 输出位置和安全边界

活动证据只在 `output/phase12/`，失效批次在带 `README.md` 的 `output/phase12-invalid-*` 归档。所有能力均为 `LAB_ONLY`，不会修改业务 Prompt、Skill、policy、permission、tools、ERP/Frappe 核心、数据库或 `.env*`。
