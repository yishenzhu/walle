#!/usr/bin/env bash
#
set -euo pipefail

PROJ_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OBS_DIR="$PROJ_ROOT/observability"
OBS_COMPOSE="$OBS_DIR/docker-compose.yaml"

OBS_PID=""
OBS_STARTED=false

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
  --with-obs     同步启动可观测性容器 (otel-collector / tempo / mimir / grafana)
  --obs-only     仅启动可观测性容器，不启动 agent
  --help         显示此帮助信息

示例:
  ./scripts/run.sh              # 仅启动 agent
  ./scripts/run.sh --with-obs   # agent + 可观测性
  ./scripts/run.sh --obs-only   # 仅启动可观测性
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
    OBS_STARTED=true
}

stop_obs() {
    if [ "$OBS_STARTED" = false ]; then
        return
    fi
    log_step "停止可观测性容器 ..."
    cd "$OBS_DIR"
    docker compose down
    cd "$PROJ_ROOT"
    log_info "可观测性容器已停止"
}

cleanup() {
    echo ""
    log_warn "正在清理 ..."
    stop_obs
    log_info "再见！"
}

# ── 启动 agent ────────────────────────────────────────
start_agent() {
    log_step "启动 walle agent ..."
    cd "$PROJ_ROOT"
    # 项目根目录有 __init__.py 是一个包 (walle)，
    # 将其父目录加入 PYTHONPATH，使相对导入 (from .xxx) 正常工作
    exec env PYTHONPATH="$PROJ_ROOT/..${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -m walle.main
}

# ── 主流程 ────────────────────────────────────────────
main() {
    local with_obs=false
    local obs_only=false

    # 解析参数
    while [ $# -gt 0 ]; do
        case "$1" in
            --with-obs)   with_obs=true ;;
            --obs-only)   obs_only=true ;;
            --help|-h)    usage ;;
            *)
                log_error "未知参数: $1"
                usage
                ;;
        esac
        shift
    done

    # 仅启动可观测性
    if [ "$obs_only" = true ]; then
        start_obs
        log_info "可观测性容器已在后台运行。"
        log_info "停止请运行: cd $OBS_DIR && docker compose down"
        exit 0
    fi

    # 同步启动可观测性
    if [ "$with_obs" = true ]; then
        start_obs
        # 注册退出钩子，agent 退出时自动停容器
        trap cleanup EXIT
    fi

    # 启动 agent
    start_agent
}

main "$@"
