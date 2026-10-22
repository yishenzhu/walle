#!/usr/bin/env bash
#
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OBS_DIR="$PROJECT_ROOT/observability"
OBS_COMPOSE="$OBS_DIR/docker-compose.yaml"

# ── venv 自动选择 ────────────────────────────────────
# 优先使用项目 .venv，未激活 venv 时也能正常运行
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python3"
if [ -x "$VENV_PYTHON" ]; then
    PY="$VENV_PYTHON"
else
    PY="python3"
fi

# 统一 Python 调用：项目根目录有 __init__.py 是一个包 (walle)，
# 将其父目录加入 PYTHONPATH，使相对导入 (from .xxx) 正常工作
run_py() {
    env PYTHONPATH="$PROJECT_ROOT/..${PYTHONPATH:+:$PYTHONPATH}" "$PY" "$@"
}

run_py_detached() {
    setsid env PYTHONPATH="$PROJECT_ROOT/..${PYTHONPATH:+:$PYTHONPATH}" \
        "$PY" "$@" >/dev/null 2>&1 < /dev/null &
}

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
  --stop         停止全部服务（agent / Web / 可观测性）
  --stop-agent   仅停止 agent 服务端
  --stop-web     仅停止 Web 服务
  --stop-obs     仅停止可观测性容器
  --test         运行测试，不启动 agent
  --cli          启动 agent 服务端并自动连接一个 CLI 客户端
  --web          启动 agent 服务端并开启 Web 对话通道
  --attach <id>  恢复（attach）已有会话
  --status       检查 agent / Web 状态并列出会话
  --help         显示此帮助信息

示例:
  ./scripts/run.sh                    # 启动 agent + 可观测性
  ./scripts/run.sh --no-obs           # 仅启动 agent
  ./scripts/run.sh --cli              # 服务端 + CLI 客户端（一键对话）
  #   --cli 之后的参数原样转给客户端，例如：./scripts/run.sh --cli --agent <name>
  ./scripts/run.sh --web              # 服务端 + Web 通道（一键开启）
  #   --web 之后的参数原样转给 Web 服务，例如：./scripts/run.sh --web --port 8081
  ./scripts/run.sh --attach cli-xxx   # 恢复会话
  ./scripts/run.sh --status           # 服务端 / Web 状态
  ./scripts/run.sh --stop             # 停止全部服务
  ./scripts/run.sh --stop-web         # 只停 Web（--stop-agent / --stop-obs 同理）
  ./scripts/run.sh --stop-web --stop-obs  # 停止项可自由组合
  ./scripts/run.sh --obs-only         # 仅启动可观测性
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

require_compose() {
    check_docker
    if [ ! -f "$OBS_COMPOSE" ]; then
        log_error "未找到 Docker Compose 文件: $OBS_COMPOSE"
        exit 1
    fi
}

compose() {
    docker compose -f "$OBS_COMPOSE" "$@"
}

# ── 可观测性 ──────────────────────────────────────────
start_obs() {
    require_compose

    log_step "启动可观测性容器 (otel-collector / tempo / mimir / grafana) ..."
    compose up -d
    log_info "可观测性容器已启动"
    log_info "  Grafana: http://localhost:3000  (admin/admin)"
    log_info "  Tempo:   http://localhost:3200"
    log_info "  Mimir:   http://localhost:9009"
}

stop_obs() {
    require_compose

    log_step "停止可观测性容器 ..."
    if compose down; then
        log_info "可观测性容器已停止"
    else
        log_warn "可观测性容器停止失败（Docker 可能未运行）"
    fi
}

# ── 启动 agent ────────────────────────────────────────
start_agent() {
    log_step "启动 walle agent ..."
    cd "$PROJECT_ROOT"
    log_info "使用 Python: $PY"
    run_py -m walle.main
}

ensure_agent() {
    # 服务端若未运行则后台常驻起一个（setsid 脱离终端与信号，
    # Ctrl+C 只停当前前台客户端/Web，不波及服务端）
    if port_in_use 8899; then
        log_info "agent 已在运行，直接连接"
        return 0
    fi
    run_py_detached -m walle.main
    log_info "agent 已在后台启动（常驻，--stop 停止）"
    for _ in $(seq 1 100); do
        if port_in_use 8899; then
            return 0
        fi
        sleep 0.2
    done
    log_error "agent 未在 20 秒内就绪，请检查日志: logs/agent.log"
    exit 1
}

stop_agent() {
    # 按进程名精确匹配 walle.main（setsid 起的服务端 PID 会 fork，不能用 PID 文件）
    local pids
    pids=$(pgrep -f "python.* -m walle.main" || true)
    if [ -n "$pids" ]; then
        log_step "停止 agent 服务端 (pid $pids) ..."
        if kill $pids 2>/dev/null; then
            log_info "agent 服务端已停止"
        else
            log_warn "部分 agent 进程无法停止: $pids"
        fi
    else
        log_info "agent 服务端未在运行"
    fi
}

# ── 启动 Web 通道 ─────────────────────────────────────
start_web() {
    # 参数原样转给 walle.channel.web（--host / --port 等由 Web 入口自己解析）
    local args=("$@")
    local host="127.0.0.1"
    local port="8080"
    local i=0
    while [ "$i" -lt "${#args[@]}" ]; do
        case "${args[$i]}" in
            --host) i=$((i + 1)); host="${args[$i]:-$host}" ;;
            --port) i=$((i + 1)); port="${args[$i]:-$port}" ;;
            --host=*) host="${args[$i]#*=}" ;;
            --port=*) port="${args[$i]#*=}" ;;
        esac
        i=$((i + 1))
    done
    log_step "启动 Web 对话通道 ..."
    if port_in_use "$port"; then
        log_info "Web 通道已在运行: http://$host:$port（重启请先 --stop）"
        return 0
    fi
    log_info "Web 对话: http://$host:$port（Ctrl+C 停止；agent 用 --stop 停止）"
    cd "$PROJECT_ROOT"
    run_py -m walle.channel.web "${args[@]}"
}

stop_web() {
    local pids
    pids=$(pgrep -f "python.* -m walle.channel.web" || true)
    if [ -n "$pids" ]; then
        log_step "停止 Web 服务 (pid $pids) ..."
        if kill $pids 2>/dev/null; then
            log_info "Web 服务已停止"
        else
            log_warn "部分 Web 进程无法停止: $pids"
        fi
    else
        log_info "Web 服务未在运行"
    fi
}

stop_all() {
    stop_agent
    stop_web
    if command -v docker &>/dev/null \
        && docker compose version &>/dev/null \
        && [ -f "$OBS_COMPOSE" ]; then
        stop_obs
    else
        log_info "Docker 或可观测性配置不可用，跳过容器清理"
    fi
}

# ── 启动 CLI 客户端 ──────────────────────────────────
start_cli_client() {
    log_step "启动 CLI 交互客户端（连接服务端）..."
    cd "$PROJECT_ROOT"
    # ⚠️ 参数**原样转给客户端**（`--cli` 之后写什么就传什么）—— 于是客户端以后新增开关
    #    （--agent / --model / …）这里都不必再改：加一个 if 就等于每加一个开关改一次脚本。
    run_py -m walle.channel.cli "$@"
}

# ── 会话管理 ──────────────────────────────────────────
require_server() {
    if ! port_in_use 8899; then
        log_error "agent 服务端未在运行，请先启动（./scripts/run.sh --no-obs、--cli 或 --web）"
        exit 1
    fi
}

list_sessions() {
    require_server
    log_step "运行中的会话 ..."
    cd "$PROJECT_ROOT"
    run_py -m walle.channel.cli --list
}

attach_session() {
    local sid="$1"
    require_server
    log_step "连接会话 $sid ..."
    cd "$PROJECT_ROOT"
    run_py -m walle.channel.cli --attach "$sid"
}

agent_status() {
    if port_in_use 8899; then
        log_info "agent 服务端: 运行中（端口 8899）"
        local pids
        pids=$(pgrep -f "python.* -m walle.main" || true)
        [ -n "$pids" ] && log_info "进程: $pids"
        list_sessions
    else
        log_info "agent 服务端: 未运行"
    fi

    local web_pids
    web_pids=$(pgrep -f "python.* -m walle.channel.web" || true)
    if [ -n "$web_pids" ]; then
        log_info "Web 通道: 运行中（pid $web_pids）"
    else
        log_info "Web 通道: 未运行"
    fi
}

# ── 端口检测 ─────────────────────────────────────────
# 用 ss 读内核监听表（不发起连接，永不挂起）——/dev/tcp 会因 SYN 黑洞挂起 2 分钟
port_in_use() {
    ss -tln 2>/dev/null | awk '{print $4}' | grep -q ":$1$"
}

# ── 运行测试 ──────────────────────────────────────────
run_tests() {
    log_step "运行测试 ..."
    cd "$PROJECT_ROOT"
    run_py -m pytest tests/ -v
}

# ── 主流程 ────────────────────────────────────────────
main() {
    local no_obs=false
    local obs_only=false
    local stop_obs_flag=false
    local stop_web_flag=false
    local stop_agent_flag=false
    local stop_all_flag=false
    local test_only=false
    local cli=false
    local web=false
    local status_flag=false
    local attach_flag=false
    local attach_id=""
    local client_args=()
    local web_args=()

    # 解析参数
    while [ $# -gt 0 ]; do
        case "$1" in
            --no-obs)      no_obs=true ;;
            --obs-only)    obs_only=true ;;
            --stop-obs)    stop_obs_flag=true ;;
            --stop-web)    stop_web_flag=true ;;
            --stop-agent)  stop_agent_flag=true ;;
            --stop)        stop_all_flag=true ;;
            --test)        test_only=true ;;
            --cli)         cli=true; shift; client_args=("$@"); break ;;
            --web)         web=true; shift; web_args=("$@"); break ;;
            --status)      status_flag=true ;;
            --attach)      attach_flag=true; shift; attach_id="${1:-}" ;;
            --help|-h)     usage ;;
            *)
                log_error "未知参数: $1"
                usage
                ;;
        esac
        shift || true   # set -e 下 $#=0 时 shift 返回非零，需兜底（--attach 取参后）
    done

    # 会话管理命令（无需启动服务端/可观测性）
    if [ "$attach_flag" = true ]; then
        if [ -z "$attach_id" ]; then
            log_error "--attach 需要会话 ID 参数，如: $0 --attach cli-xxx"
            exit 1
        fi
        attach_session "$attach_id"
        exit 0
    fi
    if [ "$status_flag" = true ]; then
        agent_status
        exit 0
    fi

    # 停止全部服务（agent / Web / 可观测性）
    if [ "$stop_all_flag" = true ]; then
        stop_all
        exit 0
    fi

    # 细分停止项（可自由组合：--stop-web --stop-obs 等）
    if [ "$stop_agent_flag" = true ] \
        || [ "$stop_web_flag" = true ] \
        || [ "$stop_obs_flag" = true ]; then
        if [ "$stop_agent_flag" = true ]; then stop_agent; fi
        if [ "$stop_web_flag" = true ]; then stop_web; fi
        if [ "$stop_obs_flag" = true ]; then stop_obs; fi
        exit 0
    fi

    # 运行测试
    if [ "$test_only" = true ]; then
        run_tests
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

    # --cli：服务端若未运行则后台常驻起一个；就绪后前台连客户端（一键对话）
    if [ "$cli" = true ]; then
        ensure_agent
        start_cli_client "${client_args[@]}"
        exit 0
    fi

    # --web：服务端若未运行则后台常驻起一个；就绪后前台开 Web 通道
    # （Ctrl+C 只停 Web，agent 继续常驻，--stop 一并停止）
    if [ "$web" = true ]; then
        ensure_agent
        start_web "${web_args[@]}"
        exit 0
    fi

    # 启动 agent（前台）
    start_agent
}

main "$@"
