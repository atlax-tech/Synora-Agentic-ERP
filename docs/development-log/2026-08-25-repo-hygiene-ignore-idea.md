# 仓库卫生：忽略 IDE 本地配置目录 .idea/

- 日期：2026-08-25
- 需求：AGENTS.md「不包含用户临时文件」。

## 改动

根 `.gitignore` 新增 `.idea/` 规则，并取消了对 `.idea/synora_agentic_erp.iml` 的误暂存。`.idea/` 下其余 IDE 生成文件（modules.xml、vcs.xml、workspace.xml、inspectionProfiles、iml）均为本地工具配置，不入库；用户本地文件未做任何删除。

## 验证

`git status --short` 不再显示任何 `.idea` 条目；暂存区恢复为空。

## 限制

无。与 P2.5 断点处工作区（gateway.py / test_gateway.py / test_runtime_boundary.py / p2_5 日志）保持分离，未混入同一提交。
