#!/usr/bin/env bash
# Walle 容器化部署：启停、日志、备份、恢复、升级。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKUP_DIR="${WALLE_BACKUP_DIR:-$ROOT/backups}"
VOLUME_NAME="${WALLE_VOLUME:-walle_walle-home}"
IMAGE="${WALLE_IMAGE:-walle:0.1.0}"

usage() {
    cat <<'EOF'
用法: ./scripts/deploy.sh <命令>

  up              构建并后台启动全部服务
  down            停止服务（保留数据卷）
  down -v         停止服务并删除数据卷
  ps              服务状态
  logs [服务]     跟踪日志（默认 web）
  restart [服务]  重启（默认全部）
  backup          打包数据卷到 backups/
  restore <文件>  用备份覆盖数据卷（需确认）
  upgrade         拉取代码后重新构建启动
  auth <用户> <密码>  生成基础认证配置（追加到 .env）
EOF
}

die() { echo "错误: $*" >&2; exit 1; }

volume() {
    if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
        echo "$VOLUME_NAME"
        return
    fi
    docker volume ls -q --filter label=com.docker.compose.volume=walle-home | head -n1
}

cmd_up() {
    docker compose up -d --build
    docker compose ps
}

cmd_logs() {
    docker compose logs -f --tail=100 "${1:-web}"
}

cmd_backup() {
    local vol target
    vol=$(volume)
    [ -n "$vol" ] || die "找不到数据卷"
    mkdir -p "$BACKUP_DIR"
    target="$BACKUP_DIR/walle-$(date +%Y%m%d-%H%M%S).tar.gz"
    docker run --rm --user "$(id -u):$(id -g)" --entrypoint sh \
        -v "$vol":/data:ro \
        -v "$BACKUP_DIR":/backup \
        "$IMAGE" -c "tar czf /backup/$(basename "$target") -C /data ."
    echo "$target"
}

cmd_restore() {
    local file="${1:-}" vol dir
    [ -n "$file" ] || die "用法: deploy.sh restore <备份文件>"
    [ -f "$file" ] || die "备份文件不存在: $file"
    vol=$(volume)
    [ -n "$vol" ] || die "找不到数据卷"
    dir=$(cd "$(dirname "$file")" && pwd)

    read -r -p "将用 $file 覆盖当前数据，继续? [y/N] " answer
    [ "$answer" = "y" ] || die "已取消"

    docker compose stop agent web
    docker run --rm --user 0:0 --entrypoint sh \
        -v "$vol":/data \
        -v "$dir":/backup:ro \
        "$IMAGE" -c "find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} +; tar xzf /backup/$(basename "$file") -C /data; chown -R 10001:10001 /data"
    docker compose start agent web
    echo "已恢复: $file"
}

cmd_upgrade() {
    git pull --ff-only
    docker compose up -d --build
    docker image prune -f
}

cmd_auth() {
    local user="${1:-admin}" password="${2:-}" hash
    [ -n "$password" ] || die "用法: deploy.sh auth <用户名> <密码>"
    docker compose build caddy >/dev/null
    hash=$(docker compose run --rm --no-deps --entrypoint caddy caddy \
        hash-password --plaintext "$password" | tr -d '\r')
    printf 'WALLE_AUTH_USER=%s\nWALLE_AUTH_HASH=%s\n' "$user" "$hash"
}

case "${1:-}" in
    up) cmd_up ;;
    down) shift; docker compose down "$@" ;;
    ps | status) docker compose ps ;;
    logs) shift; cmd_logs "$@" ;;
    restart) shift; docker compose restart "$@" ;;
    backup) cmd_backup ;;
    restore) shift; cmd_restore "$@" ;;
    upgrade) cmd_upgrade ;;
    auth) shift; cmd_auth "$@" ;;
    help | -h | --help) usage ;;
    *) usage; exit 1 ;;
esac
