# Phase 5 同任务对照

`comparison.py` 使用同一 P5-G01 任务、同一计划和同一组 recorded Observation，运行手写 Fixed Workflow、ReAct 子图和 Plan-and-Execute。输出只包含状态、步骤、调用次数、digest、revision 和 Trace 完整性，不包含 capability、用户凭证、Prompt 或 ERP 原始响应。

LangGraph 与 n8n 行保持显式 `LAB_ONLY / UNAVAILABLE`，只有依赖和真实运行证据补齐后才能加入原始数据；不能把未运行的框架填写为成功或性能更优。

```bash
PYTHONPATH=services/agent_runtime/src uv run --python 3.14 python -c \
  'from labs.workflow_comparison.comparison import run_recorded_comparison, rows_as_json; import json; print(json.dumps(rows_as_json(run_recorded_comparison()), ensure_ascii=False, indent=2))'
```

阶段采用门槛：安全、恢复、权限、Trace 和不重放矩阵必须全部通过；若手写与 LangGraph 没有可验证的支配优势，业务主线保留手写引擎，LangGraph 继续留在 `LAB_ONLY`。
