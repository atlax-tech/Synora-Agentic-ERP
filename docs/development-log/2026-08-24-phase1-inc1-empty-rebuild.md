# 2026-08-24 Phase 1 Inc-1 凭据处置与空卷重建验收

## 完成内容

- 处置已泄露的 disposable 凭据（接续 `2026-08-24-phase1-inc1-environment-recovery.md`
  的限制项）：
  1. 将 site 备份（`database.sql.gz` 874,520 字节 + `site_config_backup.json`）从
     `bench_work` 卷 `docker cp` 外导至 `/tmp/synora-phase1-backups/20260824-pre-rebuild/`
     （沙箱限制无法写入家目录；该位置重启即失效，仅作重建期安全网）；
  2. 验证外导备份：`gzip -t` 完整性通过、JSON 可解析，SHA-256
     `a6bd765d…`（数据库）/ `4119f4c2…`（site config）已记录在案；
  3. 经用户明确批准后执行 `env.sh reset`（三重门禁全部满足），删除
     `synora_phase1_dev` 全部 4 个具名卷与网络——旧 root/Administrator 凭据随卷销毁；
  4. 以 `openssl rand -hex` 生成全新随机凭据写入 `env/dev/.env`（gitignored，
     权限 600，值不进任何日志/argv）。
- 修复空卷重建的已知断点：`bench setup requirements` 会按 `package.json` 自动改写
  上游 `banking/yarn.lock`（两个间接依赖），导致"上游 diff=0"断言失败关闭。按恢复
  日志"必须显式决定、不得隐藏差异"的要求，在 `env.sh` 构建步骤后显式
  `git checkout -- banking/yarn.lock` 恢复上游原状并在脚本内注释说明。
- 从空卷完整重跑 `env.sh bootstrap` 并通过全部断言与独立验收（见下）。
- 补齐计划 Inc-1 要求的基础镜像 digest：
  `FDP_BASE_IMAGE=python:3.14-bookworm@sha256:8771427e…`（与实际构建解析一致）。
- 新增 `docs/decisions/ADR-0001-docker-bench-environment.md`（环境选型、候选 SHA、
  批准来源、后果）。

## 为什么这样改

泄露凭据已进入 bench 持久日志且无法从卷内清除，唯一彻底的处置是随卷销毁；而
Inc-1 验收本来就要求"修正后的 bootstrap 从空卷完整重跑"，两者合并为一次销毁重建，
避免二次破坏性操作。销毁前必须外导并验证备份（reset 门禁强制），避免误删仅存现场。

## 实际验证（全部实际执行）

- 空卷 bootstrap：镜像构建 → bench init（yarn install 完成，`.yarn-integrity` 存在）
  → `get-app erpnext` → 双上游 checkout 候选 SHA → HEAD 断言 + porcelain 断言通过
  （含 yarn.lock 恢复生效）→ `new-site`（密码经标准输入）→ `install-app erpnext`
  → `migrate` → `Role=Analytics` / `Operation=Assembly` 产物断言 →
  `[env] bootstrap 完成`。
- `bench --site dev.localhost list-apps`：frappe 16.31.0、erpnext 16.32.3。
- 上游状态：frappe HEAD=`6a329d0…`、erpnext HEAD=`11e0ba0…`，两仓库
  `git status --porcelain` 为空。
- Redis：global 与 site 的 cache/queue/socketio 均指向 Compose 服务；
  `bench --version` = 5.31.0；`bench doctor`：Workers online: 1，scheduler 保持
  Frappe 默认禁用（Phase 1 人工流程不依赖，见 ADR-0001）。
- HTTP：宿主机 `curl http://127.0.0.1:8000/api/method/ping` → `{"message":"pong"}`。
- 凭据轮换独立验证：旧 root 密码 → `Access denied`；新 root 密码 → 查询成功；
  Administrator 旧密码 `admin` → `AuthenticationError`；新密码 → 通过
  （`frappe.auth.check_password`，经 bench console，密码值不落日志）。

## 过程中的坑（排障记录）

- 第一次空卷 bootstrap（17:22 启动）在 bench init 的 yarn fetch 阶段被杀死：宿主
  侧 `env.sh bootstrap` 与 Trae 终端会话同进程组，终端收到 SIGINT（退出码 130）时
  连坐终止整个进程组，日志无错误、连 `BOOTSTRAP_RC` 都未写入。判断依据：宿主进程
  消失 + 容器内仅剩僵尸进程 + yarn `.yarn-integrity` 实际已写完（init 未中断在数据层）。
- macOS 无 `setsid`；改用 `( nohup … & )` 孤儿进程方式重启 bootstrap，成功走完全程。
  幂等设计（apps/frappe 存在则跳过 init）使续跑无需人工干预。
- Trae 终端存在三类不稳定：输出整段被吞（改用文件重定向 + Read）、长命令假丢失
  （实际仍在 sleep）、复合命令部分未执行；本轮全部以"写文件再读"模式绕过。

## 限制与未完成项

- 外导备份在 `/tmp`，宿主机重启即失效；它只是重建期安全网，重建已成功，不再保留
  必要性（含旧凭据的备份文件不应长期保留）。
- `FDP_VER_NODE/MARIADB/REDIS` 仍为大版本 tag（Node 24 / MariaDB 11.4 / Redis 7），
  非 digest 级固定；完整依赖固定属 Inc-5（P1.5）晋升要求，本步保持计划规定的候选态。
  【2026-08-24 补充】"完整依赖固定属 Inc-5（P1.5）晋升要求"的表述已被
  `docs/decisions/ADR-0002-frozen-baseline-pair.md` 取代：P1.5 冻结范围仅为
  Frappe/ERPNext commit pair；Node/MariaDB/Redis 维持 major tag 的说明与风险缓释见该 ADR。
- 候选 SHA 尚未经 P1.5 独立对抗审查晋升正式；此前仅可用于 Inc-2~Inc-4 的候选态工作。
- 备份内含旧 site db 密码（已随卷销毁的旧凭据），文件本身不外发、不提交。

## 检查点审查（独立对抗审查）

- 结论 `CHANGES_REQUIRED`，3 项（无安全问题、无虚构证据，核心断言审查方已独立实测
  复现：digest 与 registry 现值一致、卷时间戳证明空卷重建、备份哈希逐字符吻合）。
- 修复 1（完整性）：补 `FDP_VER_PYTHON=3.14` 并加真实消费链——compose 传入容器 +
  bootstrap 断言 `python3 --version` 前缀匹配；断言逻辑已在当前容器实跑通过
  （`PY_ASSERT_OK`），端到端行使将在 Inc-5 空卷重建完成。
- 修复 2（可追溯性）：ADR-0001 批准来源改为注明会话批准的仓库载体（本 ADR 引述 +
  本日志），计划文档按边界不入库的事实显式说明。
- 修复 3（提交卫生）：`.gitignore` 增加 `.trae/`，防止候选提交误纳会话技术夹。
- 验证：`bash -n env.sh` 通过；`docker compose config --quiet` 通过；三处修复后
  无需重跑环境（审查方已确认运行证据独立有效）。

## 可重复人工验收

1. `docker compose ps`：四服务 healthy/Up，bench 映射 `127.0.0.1:8000/9000`。
2. 容器内 `bench --site dev.localhost list-apps` 应列出 frappe 16.31.0 与
   erpnext 16.32.3。
3. 容器内两上游 `git status --short` 为空、`git rev-parse HEAD` 等于
   `env/dev/versions.env` 的两个候选 SHA。
4. 容器内 `nohup bench start &` 后，宿主机
   `curl -s http://127.0.0.1:8000/api/method/ping` 返回 `{"message":"pong"}`。
5. 用 `env/dev/.env` 的新 root 密码执行 `select 1` 成功；用旧值
   `synora_dev_root` / `admin` 均被拒绝。
