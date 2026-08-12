#!/usr/bin/env bash
#
set -euo pipefail

PROJ_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OBS_DIR="$PROJ_ROOT/observability"
OBS_COMPOSE="$OBS_DIR/docker-compose.yaml"

# ── venv 自动选择 ────────────────────────────────────
# 优先使用项目 .venv，未激活 venv 时也能正常运行
VENV_PYTHON="$PROJ_ROOT/.venv/bin/python3"
if [ -x "$VENV_PYTHON" ]; then
    PY="$VENV_PYTHON"
else
    PY="python3"
fi

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
  --no-obs       不启动可观测性容器，仅启动 agent
  --obs-only     仅启动可观测性容器，不启动 agent
  --stop-obs     停止可观测性容器
  --stop         停止常驻 agent 服务端
  --test         运行测试，不启动 agent
  --cli          启动 agent 服务端并自动连接一个 CLI 客户端
  --help         显示此帮助信息

示例:
  ./scripts/run.sh                    # 启动 agent + 可观测性
  ./scripts/run.sh --no-obs           # 仅启动 agent
  ./scripts/run.sh --cli              # 服务端 + CLI 客户端（一键对话）
  ./scripts/run.sh --stop             # 停止常驻 agent 服务端
  ./scripts/run.sh --obs-only         # 仅启动可观测性
  ./scripts/run.sh --stop-obs         # 停止可观测性
  ./scripts/run.sh --test             # 运行测试
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
    log_info "使用 Python: $PY"
    # 项目根目录有 __init__.py 是一个包 (walle)，
    # 将其父目录加入 PYTHONPATH，使相对导入 (from .xxx) 正常工作
    env PYTHONPATH="$PROJ_ROOT/..${PYTHONPATH:+:$PYTHONPATH}" \
        "$PY" -m walle.main
}

stop_agent() {
    # 按进程名精确匹配 walle.main（setsid 起的服务端 PID 会 fork，不能用 PID 文件）
    local pids
    pids=$(pgrep -f "python.* -m walle.main" || true)
    if [ -n "$pids" ]; then
        log_step "停止 agent 服务端 (pid $pids) ..."
        kill $pids 2>/dev/null
        log_info "agent 服务端已停止"
    else
        log_info "agent 服务端未在运行"
    fi
}

# ── 启动 CLI 客户端 ──────────────────────────────────
start_cli_client() {
    log_step "启动 CLI 交互客户端（连接服务端）..."
    cd "$PROJ_ROOT"
    env PYTHONPATH="$PROJ_ROOT/..${PYTHONPATH:+:$PYTHONPATH}" \
        "$PY" -m walle.channel.cli
}

# ── 端口检测 ─────────────────────────────────────────
# 用 ss 读内核监听表（不发起连接，永不挂起）——/dev/tcp 会因 SYN 黑洞挂起 2 分钟
port_in_use() {
    ss -tln 2>/dev/null | awk '{print $4}' | grep -q ":$1$"
}

# ── 运行测试 ──────────────────────────────────────────
run_tests() {
    log_step "运行测试 ..."
    cd "$PROJ_ROOT"
    "$PY" -m pytest tests/ -v
}

# ── 主流程 ────────────────────────────────────────────
main() {
    local no_obs=false
    local obs_only=false
    local stop_obs_flag=false
    local stop_agent_flag=false
    local test_only=false
    local cli=false

    # 解析参数
    while [ $# -gt 0 ]; do
        case "$1" in
            --no-obs)      no_obs=true ;;
            --obs-only)    obs_only=true ;;
            --stop-obs)    stop_obs_flag=true ;;
            --stop)        stop_agent_flag=true ;;
            --test)        test_only=true ;;
            --cli)         cli=true ;;
            --help|-h)     usage ;;
            *)
                log_error "未知参数: $1"
                usage
                ;;
        esac
        shift
    done

    # 停止常驻 agent 服务端
    if [ "$stop_agent_flag" = true ]; then
        stop_agent
        exit 0
    fi

    # 运行测试
    if [ "$test_only" = true ]; then
        run_tests
        exit 0
    fi

    # 停止可观测性
    if [ "$stop_obs_flag" = true ]; then
        stop_obs
        exit 0
    fi

    # 仅启动可观测性
    if [ "$obs_only" = true ]; then
        start_obs
        log_info "可观测性容器已在后台运行。"
        log_info "停止请运行: $0 --stop-obs"
        exit 0
    fi

    # 默认启动可观测性，除非 --no-obs
    if [ "$no_obs" = false ]; then
        start_obs
    fi

    # --cli：服务端若未运行则后台常驻起一个（setsid 脱离终端与信号，
    # Ctrl+C 客户端不波及服务端）；等就绪后前台连客户端（一键对话）
    if [ "$cli" = true ]; then
        if ! port_in_use 8899; then
            setsid env PYTHONPATH="$PROJ_ROOT/..${PYTHONPATH:+:$PYTHONPATH}" \
                "$PY" -m walle.main >/dev/null 2>&1 < /dev/null &
            log_info "agent 已在后台启动（常驻，--stop 停止）"
            for _ in $(seq 1 100); do
                if port_in_use 8899; then
                    break
                fi
                sleep 0.2
            done
            if ! port_in_use 8899; then
                log_error "agent 未在 20 秒内就绪，请检查日志: logs/agent.log"
                exit 1
            fi
        else
            log_info "agent 已在运行，直接连接"
        fi
        start_cli_client
        exit 0
    fi

    # 启动 agent（前台）
    start_agent
}

main "$@"
