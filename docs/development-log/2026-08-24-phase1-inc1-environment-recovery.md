# 2026-08-24 Phase 1 Inc-1 环境卡点审核与恢复

## 完成内容

- 只读核对了两小时内产生的未提交文件、Bench 命令日志、Docker 服务、site 配置、上游 SHA 和安装状态。
- 确认 `new-site --install-app erpnext` 进程挂起超过 30 分钟，且期间又并发执行了多次 `new-site` / `install-app`。
- 确认 ERPNext 已被写入 `installed_apps`，但 `Role=Analytics` 和 `Operation=Assembly` 均不存在，证明 ERPNext `after_install` 未完成。
- 修复环境脚本的根因：
  - 容器入口不再自动建 site/安装 ERPNext，避免重启时并发安装；
  - bootstrap 成为建站与安装的唯一责任者；
  - Redis 通过 `bench set-config -g` 在建站前写入 global 配置，建站后再写入 site 配置；
  - 补齐容器内候选 SHA 环境变量、resolve 后重新加载配置、完整状态可重跑的 bootstrap 和正确的 Procfile Redis 项匹配；部分安装会由关键产物断言失败关闭，须按本日志恢复；
  - 增加 Bench 启动命令、本机回环绑定的 8000/9000 端口，并强制显式管理员密码；
  - reset 同时要求项目名、删除确认和卷外备份确认，避免误删现场。
- 独立测试发现原建站命令把数据库 root 与 Administrator 密码放入 CLI 参数，Bench 持久日志已记录明文。修正后的 bootstrap 改由 Frappe 的交互标准输入依次提供两项密码，命令 argv 不再携带凭据；`.env.example` 故意留空并由脚本失败关闭。
- 恢复现有 site：重启 bench 容器终止挂起进程（未删卷/数据库），备份 site，修正 Redis，成功运行 `migrate`，补跑官方 `erpnext.setup.install.after_install`，并恢复 Yarn 自动改写的上游 `banking/yarn.lock`。

## 为什么这样改

原脚本在 `bench new-site --install-app erpnext` 前没有将 Redis 容器地址写入 global 配置，且容器入口与 bootstrap 同时拥有建站责任。安装中途已提交 `installed_apps`，但后续安装钩子未执行，所以盲目重跑 `install-app` 只会报“已安装”，不会补齐钩子。本次先保留现场备份，再以固定上游源码证据确认 `after_install` 从第一个关键产物前就未开始，因此只补跑一次官方钩子，没有清库或重建 site。

## 实际验证

- `bash -n env/dev/scripts/dev/env.sh`：通过。
- `docker compose ... config --quiet`：通过。
- `bench --site dev.localhost backup`：成功；生成 site config 与数据库备份，位于该 site 的 gitignored private backups 目录。该目录属于 `bench_work` 具名卷，不是卷外备份。
- `bench --site dev.localhost migrate`：成功；Frappe/ERPNext DocType 更新到 100%，jobs/fixtures/dashboards/customizations/after_migrate 完成。
- `bench --site dev.localhost execute erpnext.setup.install.after_install`：成功。
- 安装产物：`Role=Analytics`、`Operation=Assembly` 均存在。
- `bench --site dev.localhost list-apps`：Frappe 16.31.0、ERPNext 16.32.3。
- HTTP：`GET /api/method/ping` 返回 `{"message":"pong"}`。
- 按新 Compose 配置只重建 bench 容器（未删卷/数据库）后，实际端口为 `127.0.0.1:8000/9000`，旧入口挂载已移除；恢复 `bench start` 后宿主机回环 HTTP ping 再次返回 `pong`。
- `bench doctor`：恢复前 worker online=1；仅重建 bench 后短时显示 online=2，但进程树只有一个 worker，属于旧 Redis worker 注册尚未过期；site scheduler 当前禁用/不活跃。
- Redis：global 与 site 的 cache/queue/socketio 均指向 Compose Redis 服务。
- 上游：Frappe/ERPNext 均无未提交 diff；HEAD 与 `versions.env` 候选 SHA 一致。
- 实际工具版本：Python 3.14.7、Node 24.19.0、Yarn 1.22.22、Frappe Bench 5.31.0。

## 限制与未完成项

- Inc-1 尚未完成，本次只是保留数据的现场恢复，不是空卷可复跑证据。
- 修正后的 bootstrap 尚未从空卷/空 site 完整重跑；破坏性 reset 需用户单独决定。
- Bench 已按现场版本固定为 5.31.0；Node、MariaDB、Redis 仍是大版本 tag，基础镜像未带 digest，尚未满足完整依赖固定要求。
- ERPNext 候选 SHA 在 `bench setup requirements` 时会自动改写 `banking/yarn.lock` 中两个间接依赖；本次已恢复上游文件，但干净重建前必须决定固定 Yarn 行为或更换候选 SHA，不得隐藏该差异。
- scheduler 当前禁用；是否在 P1.1 启用并验证需在干净重建验收中明确。
- 当前数据库备份仍在 `bench_work` 具名卷内；执行 `reset --volumes` 会一并删除。干净重建前必须先复制到具名卷之外、验证文件可读且大小/校验值符合预期，再设置第二重确认变量；未完成外导不得 reset。
- 旧的 disposable 数据库 root 与 Administrator 凭据已进入 gitignored 的 `bench_work` 持久日志，应视为已泄露。本次保留日志用于现场诊断且不将其提交/外导；继续使用环境前必须轮换两项凭据，或在备份外导并获批后随专用卷一起销毁。不得把含秘密的原日志作为证据文件。
- 本检查点仅记录并保护已恢复现场；独立 Test/Review 结论及提交状态见本日志末尾“检查点审查”。

## 可重复人工验收

1. 运行 `docker compose` 的 `ps`，确认 MariaDB、Redis cache/queue 健康，bench 容器映射 8000/9000。
2. 访问 `http://dev.localhost:8000/api/method/ping`，确认返回 `pong`。
3. 在 bench 容器运行 `bench --site dev.localhost list-apps` 和 `bench doctor`，确认 ERPNext 已安装、worker online。
4. 检查 Frappe/ERPNext `git status --short`，确认上游无 diff。
5. 先将 site 数据库与配置备份复制到具名卷之外并验证，再由用户明确批准删除 `synora_phase1_dev` 专用卷，才可执行空环境 bootstrap 验收。

## 检查点审查

- Ponytail full 审查删除了冗余 hostname、入口包装脚本、重复 home 创建和单用途命令包装；Bench 版本改为现场已验证的 5.31.0 显式固定。
- 独立 Review 识别并要求修复局域网暴露/默认管理员密码、卷内备份可能随 reset 删除、部分安装状态并非自动幂等恢复三项问题；本检查点已据此调整。
- 第一轮独立 Test 因原建站 CLI 泄露凭据判定 `FAIL`；脚本随后改用已核对 Frappe 16 源码顺序的标准输入。空卷 bootstrap 因会破坏现场，明确不属于本次检查点验证范围。
- 修正后第二轮独立 Test 为 `PASS`：脚本/Compose/reset 门禁、实际回环端口、服务健康、HTTP、关键安装产物、Redis、候选 SHA 与上游 clean 均通过；未执行 reset、空卷 bootstrap 或数据库重建。
- 修正后独立 Review 为 `PASS`；提交继续排除 `.trae`、`.env`、site/private、日志和上游 checkout。
