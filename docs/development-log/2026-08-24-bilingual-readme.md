# 2026-08-24 Bilingual README

## 完成内容

- 使用项目级 READMEWriter Skill 扫描当前仓库并重写英文 `README.md`。
- 新增中文 `README.zh-CN.md`，两份 README 互相提供语言入口。
- 展示产品价值、完整 P2P 范围、治理架构、审批边界、RAG 演进、Multi-Agent 准入、安全、测试和路线图。
- 明确当前处于 Phase 0，产品运行时尚未实现，不提供虚假的 Bench、Docker、依赖或环境变量命令。

## 验证结果

- 两份 README 都包含 Mermaid 架构图和核心流程图、技术方向表格、真实目录树、安全设计、Roadmap、FAQ 和许可证边界。
- 唯一可执行的快速开始命令是仓库克隆与已经实际通过的 Harness 结构校验。
- 没有声称已存在产品代码、可运行 Demo、性能数据、客户采用或生产部署。
- READMEWriter 自带模板中的相对 LICENSE 示例在 Skill 子目录会形成断链，已改为指向项目根目录概念的内联说明，没有复制或伪造许可证文件。

## 人工验收步骤

1. 在 GitHub Markdown 预览中分别打开 `README.md` 与 `README.zh-CN.md`，确认语言链接和 Mermaid 图正常。
2. 检查 Project status/项目状态和 Product installation/产品安装状态，确认没有把目标架构写成已实现能力。
3. 对照 `docs/ROADMAP.md` 检查阶段勾选，仅 Phase 0 标记完成。
