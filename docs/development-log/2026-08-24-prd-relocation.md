# 2026-08-24 PRD Relocation

## 完成内容

- 将根目录 `PRD.md` 与 Harness 原有 `docs/PRODUCT.md` 合并为唯一事实源 `docs/PRD.md`。
- 保留完整落地 PRD，并补入原 Product 文档中的核心业务任务与非目标边界。
- 删除重复的根目录 PRD 和 `docs/PRODUCT.md`，同步 `AGENTS.md`、架构来源、Harness manifest 与 source index。
- 将项目内安装的 Harness Armor 必需文件检查和初始化模板从 `docs/PRODUCT.md` 适配为 `docs/PRD.md`，避免为了通过校验保留重复文档。

## 验证结果

- `docs/PRD.md` 同时包含产品定位、用户、核心任务、非目标、完整 P2P 范围、功能需求和验收要求。
- 根目录不再存在 `PRD.md`，项目内不再引用 `docs/PRODUCT.md`。
- Harness source index 只登记 `docs/PRD.md` 这一份产品需求事实源。
- Harness 结构校验接受 `docs/PRD.md` 作为本项目规范的产品文件名。

## 人工验收步骤

1. 打开 `docs/PRD.md`，确认开头标明本文是唯一产品需求事实源。
2. 检查第 1.6 节的非目标边界，以及 F-009 至 F-012 的完整 P2P 后续范围。
3. 在项目根目录确认不存在 `PRD.md`。
