# ADR-0001：Phase 1 开发环境采用 Docker 容器内 Bench

- 状态：已批准（候选基线）
- 日期：2026-08-24
- 关联：`docs/PLAN.md` P1.1；`.trae/documents/synora-phase1-first-round-plan.md` Inc-1

## 背景（Context）

Phase 1 需要一个未修改的 Frappe/ERPNext v16 上游基线环境用于取证（人工 P2P、源码地图、
权限矩阵）。上游 `version-16` 要求 Python `>=3.14,<3.15` 与 Node `>=24`，本机系统
Python 为 3.13，无法直接满足；且需要在可重复销毁/重建的一次性环境中操作，避免污染本机。

## 决策（Decision）

Phase 1 开发环境采用 Docker Compose 四服务拓扑：`bench`（容器内 Bench + Frappe/ERPNext
源码）、`mariadb`、`redis-cache`、`redis-queue`。所有数据放 `synora_phase1_dev` 项目
具名卷，宿主机仅通过 `127.0.0.1:8000/9000` 回环访问。

版本固定（`env/dev/versions.env`，候选阶段）：

- 基础镜像：`python:3.14-bookworm@sha256:8771427e…`（digest 固定）
- Node 24 / frappe-bench 5.31.0 / MariaDB 11.4 / Redis 7
- Frappe 候选 SHA：`6a329d068416768ec47ccd3326b9cc95a8d7bf99`
- ERPNext 候选 SHA：`11e0ba0a1c45f217e2e73e885f699102d06da325`

## 备选方案（Alternatives）

1. **本机安装 bench**：系统 Python 3.13 不满足 v16 要求，需额外版本管理工具，且销毁
   重建会污染本机；否决。
2. **Frappe 官方 `frappe_docker` 生产镜像**：面向托管多站点，不便于 Phase 1 的源码级
   取证与逐命令验证；否决。
3. **容器内 Bench（采纳）**：满足版本要求、完全一次性可重建、源码可直接只读取证。

## 后果（Consequences）

- 正向：环境完全可复跑（空卷 bootstrap 已验证）；凭据/数据全部隔离在具名卷；上游
  checkout 可逐 SHA 断言。
- 代价：构建期较长；Trae 终端中断可能连带杀死宿主侧 bootstrap 进程（已用孤儿进程
  方式规避并记录）；`bench setup requirements` 会自动改写上游 `banking/yarn.lock`
  （bootstrap 在构建后显式恢复原状以满足"上游 diff=0"断言，不隐藏差异）。
- site scheduler 保持 Frappe 默认禁用：Phase 1 人工流程（MR→PO→Receipt→Invoice）为
  即时操作，不依赖定时任务；启用与否留待 Phase 2 按需决策。
- 候选 SHA 晋升正式基线前（P1.5）不得用于任何结论性证据之外的用途。

## 证据（Evidence）

- 空卷完整重建（2026-08-24）：`bootstrap` 从零执行成功——镜像构建、bench init、
  get-app、双 SHA checkout 断言、`new-site`（密码经标准输入，不进 argv/日志）、
  `install-app erpnext`、`migrate`、`Role=Analytics`/`Operation=Assembly` 产物断言。
- `list-apps`：frappe 16.31.0、erpnext 16.32.3；两上游 `git status --porcelain` 为空。
- HTTP `GET /api/method/ping` → `{"message":"pong"}`（127.0.0.1:8000）。
- 凭据轮换验证：旧 mariadb root 与 Administrator 旧密码均被拒绝；新凭据全部通过。
- 详细命令与输出见 `docs/development-log/2026-08-24-phase1-inc1-empty-rebuild.md`。

## 批准来源（Approval）

- 用户于 2026-08-24 会话中批准执行 v3 计划（含“Docker 容器内 Bench”环境选型与
  Inc-1 全部内容）。该批准发生在会话中，仓库内载体为本 ADR 的引述记录；计划文档
  属 `.trae/` 技术夹，按其边界不入库。
- 用户于同一会话中批准销毁 `synora_phase1_dev` 全部具名卷并空卷重建（凭据泄露
  处置）；仓库载体见
  `docs/development-log/2026-08-24-phase1-inc1-empty-rebuild.md`。

## 取代（Supersession）

无。后续 P1.5 晋升正式基线时将以 ADR-0002 记录，不取代本 ADR 的环境拓扑决策。
