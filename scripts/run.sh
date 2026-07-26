#!/usr/bin/env bash
#
set -euo pipefail

PROJ_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OBS_DIR="$PROJ_ROOT/observability"
OBS_COMPOSE="$OBS_DIR/docker-compose.yaml"

# ── 颜色 ──────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${CYAN}[STEP]${NC}  $*"; }

# ── 帮助 ──────────────────────────────────────────────
usage() {
    cat <<EOF
walle 启动脚本

用法:
  $(basename "$0") [选项]

选项:
  --with-obs     启动可观测性容器后再启动 agent (容器不会自动关闭)
  --obs-only     仅启动可观测性容器，不启动 agent
  --stop-obs     停止可观测性容器
  --help         显示此帮助信息

示例:
  ./scripts/run.sh              # 仅启动 agent
  ./scripts/run.sh --with-obs   # agent + 可观测性
  ./scripts/run.sh --obs-only   # 仅启动可观测性
  ./scripts/run.sh --stop-obs   # 停止可观测性
EOF
    exit 0
}

check_docker() {
    if ! command -v docker &>/dev/null; then
        log_error "未找到 docker，请先安装 Docker"
        exit 1
    fi
    if ! docker compose version &>/dev/null; then
        log_error "Docker Compose 插件不可用，请升级 Docker"
        exit 1
    fi
}

# ── 可观测性 ──────────────────────────────────────────
start_obs() {
    check_docker

    if [ ! -f "$OBS_COMPOSE" ]; then
        log_error "未找到 Docker Compose 文件: $OBS_COMPOSE"
        exit 1
    fi

    log_step "启动可观测性容器 (otel-collector / tempo / mimir / grafana) ..."
    cd "$OBS_DIR"

    docker compose up -d
    log_info "可观测性容器已启动"
    log_info "  Grafana: http://localhost:3000  (admin/admin)"
    log_info "  Tempo:   http://localhost:3200"
    log_info "  Mimir:   http://localhost:9009"

    cd "$PROJ_ROOT"
}

stop_obs() {
    check_docker

    if [ ! -f "$OBS_COMPOSE" ]; then
        log_error "未找到 Docker Compose 文件: $OBS_COMPOSE"
        exit 1
    fi

    log_step "停止可观测性容器 ..."
    cd "$OBS_DIR"
    docker compose down
    cd "$PROJ_ROOT"
    log_info "可观测性容器已停止"
}

# ── 启动 agent ────────────────────────────────────────
start_agent() {
    log_step "启动 walle agent ..."
    cd "$PROJ_ROOT"
    # 项目根目录有 __init__.py 是一个包 (walle)，
    # 将其父目录加入 PYTHONPATH，使相对导入 (from .xxx) 正常工作
    env PYTHONPATH="$PROJ_ROOT/..${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -m walle.main
}

# ── 主流程 ────────────────────────────────────────────
main() {
    local with_obs=false
    local obs_only=false
    local stop_obs_flag=false

    # 解析参数
    while [ $# -gt 0 ]; do
        case "$1" in
            --with-obs)    with_obs=true ;;
            --obs-only)    obs_only=true ;;
            --stop-obs)    stop_obs_flag=true ;;
            --help|-h)     usage ;;
            *)
                log_error "未知参数: $1"
                usage
                ;;
        esac
        shift
    done

    # 停止可观测性
    if [ "$stop_obs_flag" = true ]; then
        stop_obs
        exit 0
    fi

    # 仅启动可观测性
    if [ "$obs_only" = true ]; then
        start_obs
        log_info "可观测性容器已在后台运行。"
        log_info "停止请运行: cd $OBS_DIR && docker compose down"
        exit 0
    fi

    # 启动可观测性（容器在 agent 退出后保持不变）
    if [ "$with_obs" = true ]; then
        start_obs
    fi

    # 启动 agent
    start_agent
}

main "$@"
