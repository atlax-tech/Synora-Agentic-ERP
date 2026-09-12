# Phase 12 失效归档：pre-R2

该目录保留 Phase 12 第一收口周期的全部活动输出，内容未删除、未覆盖。

失效原因：

- 合成案例的模型可见输入主要由模板和编号构成，缺少可区分的采购事实；
- Reflection、Best-of-3 和 Prompt/Skill replay 没有使用真实 Provider 方法路径；
- 旧 test 已被查看并参与修复，不能作为新的 held-out 证据；
- 报告和 artifact 的 PASS 只代表文件完整性，不能代表阶段出口通过。

这些文件只用于历史追踪、回归和审计，不得与 `output/phase12/` 的新 v2 数据、实验或报告混排。新的根因修复必须使用新的 batch、experiment 或 dataset digest。
