#!/usr/bin/env bash
# Synora Phase 1 开发环境脚本（Inc-1 / P1.1）
# 用法: env.sh <up|down|reset|resolve|bootstrap|start|bash|app-install|app-test|seed|cleanup|info>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
DEV="$ROOT/env/dev"
cd "$DEV"

_set_env() {
  set -a
  . ./versions.env
  if [ -f ./.env ]; then
    . ./.env
  else
    echo "[env] env/dev/.env 缺失，请先: cp env/dev/.env.example env/dev/.env" >&2
    exit 1
  fi
  set +a
  export FRAPPE_SITE="${FRAPPE_SITE:-dev.localhost}"
}

_require_bootstrap_credentials() {
  local required
  for required in MYSQL_ROOT_PASSWORD MYSQL_USER MYSQL_PASSWORD ADMIN_PASSWORD; do
    if [ -z "${!required:-}" ]; then
      echo "[env] bootstrap 前必须在 env/dev/.env 中显式设置 $required" >&2
      exit 1
    fi
  done
}

# 从上游 version-16 解析候选 SHA，仅回填空值
_resolve_ref() {
  set -- "$1" "$2" "$3"   # $1=var名 $2=repo $3=ref
  local head
  head="$(git ls-remote "https://github.com/frappe/$2.git" "$3" | awk '{print $1}')"
  if [ -z "${head:-}" ]; then echo "[env] 解析 $1 失败(repo=$2/ref=$3)" >&2; exit 1; fi
  local cur="$(grep -E "^${1}=.*" versions.env || true)"
  if [ -z "${cur:-}" ] || [ -z "${cur#*=}" ]; then
    sed -i.bak -E "s|^${1}=.*|${1}=${head}|" versions.env && rm -f versions.env.bak
    echo "[env] $1=$head"
  else
    echo "[env] $1 已存在: ${cur#*=}"
  fi
}

do_resolve() {
  _resolve_ref FDP_REV_FRAPPE frappe "$FDP_REF_FRAPPE"
  _resolve_ref FDP_REV_ERP_NEXT erpnext "$FDP_REF_ERP_NEXT"
}

_bootstrap() {
  _set_env
  _require_bootstrap_credentials
  do_resolve
  # resolve 可能回填 versions.env，重新加载后再传入容器。
  _set_env
  docker compose -f docker-compose.yml up -d --build --wait --remove-orphans
  # bench init（raw SHA 不传 --frappe-branch，仅用参考分支；之后 detached checkout 精确 SHA）
  if ! docker compose -f docker-compose.yml exec -T bench test -d /home/frappe/bench/apps/frappe; then
    docker compose -f docker-compose.yml exec -T bench \
      bench init bench --frappe-branch "$FDP_REF_FRAPPE" --skip-redis-config-generation
  fi
  docker compose -f docker-compose.yml exec -T bench bash -lc '
    set -euo pipefail
    cd /home/frappe/bench
    if [ ! -d apps/erpnext ]; then
      bench get-app erpnext --branch "$FDP_REF_ERP_NEXT"
    fi
    cd apps/frappe && git fetch --depth=1 origin "$FDP_REV_FRAPPE" && git checkout -q "$FDP_REV_FRAPPE"
    cd ../erpnext && git fetch --depth=1 origin "$FDP_REV_ERP_NEXT" && git checkout -q "$FDP_REV_ERP_NEXT"
    cd /home/frappe/bench && bench setup requirements && bench build
    # yarn 按 package.json 解析时自动改写上游 banking/yarn.lock（间接依赖）；
    # 候选环境要求 checkout 与候选 SHA 完全一致，构建后显式恢复原状并在此记录。
    git -C apps/erpnext checkout -- banking/yarn.lock
  '
  docker compose -f docker-compose.yml exec -T bench bash -lc '
    set -euo pipefail
    cd /home/frappe/bench
    python3 --version | grep -q "Python $FDP_VER_PYTHON." \
      || { echo "[env] 容器 Python 版本与 FDP_VER_PYTHON 不符" >&2; exit 1; }
    test "$(cd apps/frappe  && git rev-parse HEAD)" = "$FDP_REV_FRAPPE"
    test "$(cd apps/erpnext && git rev-parse HEAD)" = "$FDP_REV_ERP_NEXT"
    echo "[env] 上游 HEAD 与候选 SHA 匹配"
    if [ -n "$(git -C apps/frappe status --porcelain)" ] || [ -n "$(git -C apps/erpnext status --porcelain)" ]; then
      echo "[env] 上游 checkout 被构建改写，拒绝继续" >&2
      git -C apps/frappe status --short >&2
      git -C apps/erpnext status --short >&2
      exit 1
    fi
    bench set-config -g db_host mariadb
    bench set-config -g redis_cache redis://redis-cache:6379
    bench set-config -g redis_queue redis://redis-queue:6379
    bench set-config -g redis_socketio redis://redis-queue:6379
    sed -i.bak -E "/^(redis(_cache|_queue|_socketio)?|redis-(cache|queue|socketio)):/d" Procfile
    rm -f Procfile.bak
  '
  docker compose -f docker-compose.yml exec -T bench bash -lc '
    set -euo pipefail
    cd /home/frappe/bench
    if [ ! -d "sites/$FRAPPE_SITE" ]; then
      # Frappe 的交互提示依次读取 DB root 与 Administrator 密码；不要把秘密放进 argv/bench.log。
      printf "%s\n%s\n" "$MYSQL_ROOT_PASSWORD" "$ADMIN_PASSWORD" \
        | bench new-site "$FRAPPE_SITE" --db-host mariadb
    fi
    bench --site "$FRAPPE_SITE" set-config db_host mariadb
    bench --site "$FRAPPE_SITE" set-config redis_cache redis://redis-cache:6379
    bench --site "$FRAPPE_SITE" set-config redis_queue redis://redis-queue:6379
    bench --site "$FRAPPE_SITE" set-config redis_socketio redis://redis-queue:6379
    bench --site "$FRAPPE_SITE" set-config developer_mode 1
    bench --site "$FRAPPE_SITE" install-app erpnext
    bench --site "$FRAPPE_SITE" migrate
    test "$(bench --site "$FRAPPE_SITE" execute frappe.db.exists --args "[\"Role\",\"Analytics\"]")" = "Analytics"
    test "$(bench --site "$FRAPPE_SITE" execute frappe.db.exists --args "[\"Operation\",\"Assembly\"]")" = "Assembly"
    echo "[env] site 已创建并安装 erpnext"
  '
  echo "[env] bootstrap 完成"
}

do_reset() {
  _set_env
  # 破坏性重置门禁：仅限本项目明确声明的容器与具名卷
  if [ "${COMPOSE_PROJECT_NAME:-}" != "synora_phase1_dev" ]; then
    echo "[env] 项目名确认失败，拒绝重置" >&2; exit 1
  fi
  if [ "${CONFIRM_RESET:-}" != "synora_phase1_dev" ]; then
    echo "[env] 需要 CONFIRM_RESET=synora_phase1_dev，拒绝重置" >&2; exit 1
  fi
  if [ "${CONFIRM_BACKUP_EXPORTED:-}" != "synora_phase1_dev" ]; then
    echo "[env] 备份仍在 bench_work 卷内；外导并验证后设置 CONFIRM_BACKUP_EXPORTED=synora_phase1_dev" >&2
    exit 1
  fi
  docker compose -f docker-compose.yml down --volumes --remove-orphans
}

# 主数据 seed/cleanup（P1.2）：拷入容器后经 bench console 执行（bench execute 仅接受已安装 app 的模块）
do_seed() {
  _set_env
  local which="$1"
  local cid
  local output
  local success_marker
  case "$which" in
    seed) success_marker="SEED-OK" ;;
    cleanup) success_marker="CLEANUP-OK" ;;
    *) echo "[env] 未知主数据操作: $which" >&2; exit 1 ;;
  esac
  cid="$(docker compose -f docker-compose.yml ps -q bench)"
  [ -n "$cid" ] || { echo "[env] bench 容器未运行" >&2; exit 1; }
  docker compose -f docker-compose.yml exec -T bench mkdir -p /tmp/synora_seed
  docker cp "$DEV/seed/seed.py" "$cid:/tmp/synora_seed/seed.py"
  docker cp "$DEV/seed/cleanup.py" "$cid:/tmp/synora_seed/cleanup.py"
  output="$(docker compose -f docker-compose.yml exec -T bench bash -lc \
    "cd /home/frappe/bench && echo 'exec(open(\"/tmp/synora_seed/$which.py\").read(), globals()); run()' | bench --site \"$FRAPPE_SITE\" console")"
  printf '%s\n' "$output"
  if ! grep -qxF "$success_marker" <<<"$output"; then
    echo "[env] $which 未输出成功标记 $success_marker" >&2
    return 1
  fi
}

# P2P 测试用户初始化（Inc-3）：Administrator 仅在此建命名用户+角色，密码经环境变量注入
do_p2p_users() {
  _set_env
  [ -n "${SYNORA_P2P_USER_PWD:-}" ] || { echo "[env] .env 缺少 SYNORA_P2P_USER_PWD" >&2; exit 1; }
  local cid
  cid="$(docker compose -f docker-compose.yml ps -q bench)"
  [ -n "$cid" ] || { echo "[env] bench 容器未运行" >&2; exit 1; }
  docker compose -f docker-compose.yml exec -T bench mkdir -p /tmp/synora_seed
  docker cp "$DEV/seed/p2p_users.py" "$cid:/tmp/synora_seed/p2p_users.py"
  local output
  output="$(docker compose -f docker-compose.yml exec -T -e SYNORA_P2P_USER_PWD bench bash -lc \
    "cd /home/frappe/bench && echo 'exec(open(\"/tmp/synora_seed/p2p_users.py\").read(), globals()); run()' | bench --site \"$FRAPPE_SITE\" console")"
  printf '%s\n' "$output"
  if ! grep -qxF "P2P-USERS-OK" <<<"$output"; then
    echo "[env] p2p-users 未输出成功标记 P2P-USERS-OK" >&2
    return 1
  fi
}

# 人工 P2P 运行器（Inc-3）：纯 HTTP 模拟人工 MR→PO→PR→PI + 失败用例
do_p2p_run() {
  _set_env
  [ -n "${SYNORA_P2P_USER_PWD:-}" ] || { echo "[env] .env 缺少 SYNORA_P2P_USER_PWD" >&2; exit 1; }
  local cid
  cid="$(docker compose -f docker-compose.yml ps -q bench)"
  [ -n "$cid" ] || { echo "[env] bench 容器未运行" >&2; exit 1; }
  docker compose -f docker-compose.yml exec -T bench mkdir -p /tmp/synora_p2p
  docker cp "$DEV/p2p/p2p_run.py" "$cid:/tmp/synora_p2p/p2p_run.py"
  docker compose -f docker-compose.yml exec -T -e SYNORA_P2P_USER_PWD bench bash -lc \
    "cd /home/frappe/bench && env/bin/python /tmp/synora_p2p/p2p_run.py"
}

do_app_install() {
  _set_env
  local app="synora_agentic_erp"
  docker compose -f docker-compose.yml exec -T bench bash -lc \
    "set -euo pipefail
     cd /home/frappe/bench
     if [ -e apps/$app ] || [ -L apps/$app ]; then
       link_target=\"\$(readlink \"apps/$app\")\"
       if [ ! -L apps/$app ] || [ \"\$link_target\" != \"/workspace/synora\" ]; then
         echo \"[env] apps/$app 已存在但不是 /workspace/synora soft-link，拒绝继续\" >&2
         exit 1
       fi
     else
       bench get-app --soft-link /workspace/synora
     fi
     if ! bench --site \"$FRAPPE_SITE\" list-apps | awk '{print \$1}' | grep -qxF '$app'; then
       bench --site \"$FRAPPE_SITE\" install-app '$app'
     fi
     bench --site \"$FRAPPE_SITE\" migrate
     echo '[env] $app installed'"
}

do_app_test() {
  do_app_install
  docker compose -f docker-compose.yml exec -T bench bash -lc \
    "set -euo pipefail
     cd /home/frappe/bench
     bench --site \"$FRAPPE_SITE\" set-config allow_tests 1
     restore_tests() {
       bench --site \"$FRAPPE_SITE\" set-config allow_tests 0 >/dev/null
     }
     trap restore_tests EXIT
     bench --site \"$FRAPPE_SITE\" run-tests --app synora_agentic_erp"
}

case "${1:-}" in
  up) _set_env; docker compose -f docker-compose.yml up -d --wait "${@:2}" ;;
  down) _set_env; docker compose -f docker-compose.yml down --remove-orphans ;;
  reset) do_reset ;;
  resolve) _set_env; do_resolve ;;
  bootstrap) _bootstrap ;;
  start) _set_env; docker compose -f docker-compose.yml exec bench bash -lc 'cd /home/frappe/bench && exec bench start' ;;
  bash) _set_env; docker compose -f docker-compose.yml exec -T bench bash -lc "${2:-echo 'no cmd'}" ;;
  app-install) do_app_install ;;
  app-test) do_app_test ;;
  seed) do_seed seed ;;
  cleanup) do_seed cleanup ;;
  p2p-users) do_p2p_users ;;
  p2p-run) do_p2p_run ;;
  info) _set_env; env | grep -E '^(FDP_|COMPOSE_|MYSQL_|FRAPPE_|ADMIN_)' | sed 's/=.*/=<set>/' ;;
  *) echo "用法: env.sh <up|down|reset|resolve|bootstrap|start|bash|app-install|app-test|seed|cleanup|p2p-users|p2p-run|info>" >&2; exit 1 ;;
esac
