# 2026-08-24 Agent Engineering Rules

## 完成内容

- 在根 `AGENTS.md` 增加项目级 Ponytail 自动调用规则，限定为编码、修复、重构、代码评审和依赖选择任务，默认使用 `full`。
- 根据用户指定的 Clean Code 原始清单补充命名、函数、边界、重复、注释和测试规则，同时禁止为了形式化原则制造无用抽象。
- 明确每次改动必须写通俗中文开发日志，代码注释只保留意图、澄清和后果警告。
- 明确每次改动都采用通过验证的小步提交，并排除用户临时文件。
- 增加 READMEWriter 门禁，要求中英文 README 保持语义同步。
- 定义版本更新范围，并要求更新前由独立对抗性 sub-agent 返回带证据的结论。

## 验证结果

- 六项用户要求都出现在根 `AGENTS.md`，不是只存在于普通说明文档。
- Ponytail 的 YAGNI 不得覆盖完整 P2P、安全、错误处理、可访问性或验收要求。
- 对抗性 sub-agent 规则只在版本/发布更新前触发；本轮没有版本更新，因此没有伪造 sub-agent 结果。

## 人工验收步骤

1. 打开根 `AGENTS.md`，确认 Mandatory project workflow 包含六条机械规则。
2. 在下一次编码任务中检查 Agent 是否先声明加载 Ponytail。
3. 在下一次 README 修改中检查 Agent 是否调用 READMEWriter 并同步两种语言。
