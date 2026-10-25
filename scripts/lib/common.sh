#!/usr/bin/env bash
# run.sh 公共工具：路径、Python、日志、端口与进程。

: "${PROJECT_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SCRIPTS_DIR="$PROJECT_ROOT/scripts"
LOG_DIR="$PROJECT_ROOT/logs"
STATE_DIR="$LOG_DIR/.run"
mkdir -p "$LOG_DIR" "$STATE_DIR"

AGENT_PORT=8899
WEB_HOST_DEFAULT=127.0.0.1
WEB_PORT_DEFAULT=8080
: "${READY_TIMEOUT:=20}"

VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python3"
if [ -n "${WALLE_PYTHON:-}" ]; then
    PY="$WALLE_PYTHON"
elif [ -x "$VENV_PYTHON" ]; then
    PY="$VENV_PYTHON"
else
    PY="python3"
fi

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' CYAN='' NC=''
fi

info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step() { echo -e "${CYAN}[STEP]${NC}  $*"; }
die() { error "$*"; exit 1; }

pythonpath() {
    printf '%s/..%s' "$PROJECT_ROOT" "${PYTHONPATH:+:$PYTHONPATH}"
}

run_py() {
    (cd "$PROJECT_ROOT" && env PYTHONPATH="$(pythonpath)" "$PY" "$@")
}

exec_py() {
    cd "$PROJECT_ROOT"
    export PYTHONPATH
    PYTHONPATH="$(pythonpath)"
    exec "$PY" "$@"
}

# 后台常驻：脱离终端信号；stdout/stderr 落到日志文件。
run_py_background() {
    local log="$1"
    shift
    mkdir -p "$(dirname "$log")"
    if command -v setsid >/dev/null 2>&1; then
        (cd "$PROJECT_ROOT" && exec setsid env PYTHONPATH="$(pythonpath)" \
            "$PY" "$@" >>"$log" 2>&1 </dev/null) &
    else
        (cd "$PROJECT_ROOT" && exec nohup env PYTHONPATH="$(pythonpath)" \
            "$PY" "$@" >>"$log" 2>&1 </dev/null) &
    fi
    echo $!
}

port_in_use() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -tln 2>/dev/null | awk '{print $4}' | grep -q ":$port$"
    elif command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    else
        timeout 1 bash -c "echo >/dev/tcp/127.0.0.1/$port" >/dev/null 2>&1
    fi
}

wait_for_port() {
    local port="$1"
    local timeout="${2:-$READY_TIMEOUT}"
    local tries=$((timeout * 10))
    local i
    for ((i = 0; i < tries; i++)); do
        if port_in_use "$port"; then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

# 只匹配当前仓库启动的 walle 进程，避免误杀其他 checkout。
project_pids() {
    local module="$1"
    local pattern="python[0-9.]*[[:space:]]+.*-m[[:space:]]+${module//./\\.}([[:space:]]|$)"
    local pid cwd
    for pid in $(pgrep -f "$pattern" 2>/dev/null || true); do
        [ "$pid" = "$$" ] && continue
        if [ -r "/proc/$pid/cwd" ]; then
            cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)
            if [ "$cwd" = "$PROJECT_ROOT" ]; then
                echo "$pid"
            fi
        else
            echo "$pid"
        fi
    done
}

stop_pids() {
    local label="$1"
    shift
    local pids=("$@")
    if [ "${#pids[@]}" -eq 0 ]; then
        info "$label 未在运行"
        return 0
    fi
    step "停止 $label (pid ${pids[*]}) ..."
    kill "${pids[@]}" 2>/dev/null || true
    local i pid alive
    for ((i = 0; i < 50; i++)); do
        alive=()
        for pid in "${pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                alive+=("$pid")
            fi
        done
        [ "${#alive[@]}" -eq 0 ] && break
        sleep 0.1
    done
    if [ "${#alive[@]}" -gt 0 ]; then
        warn "$label 未在 5 秒内退出，强制结束: ${alive[*]}"
        kill -9 "${alive[@]}" 2>/dev/null || true
    fi
    info "$label 已停止"
}

# Web 监听地址记录：restart / open / status 复用最近一次配置。
save_web_addr() {
    local host="$1" port="$2"
    printf '%s\n' "$host" >"$STATE_DIR/web.host"
    printf '%s\n' "$port" >"$STATE_DIR/web.port"
}

web_host() {
    if [ -f "$STATE_DIR/web.host" ]; then
        cat "$STATE_DIR/web.host"
    else
        printf '%s' "$WEB_HOST_DEFAULT"
    fi
}

web_port() {
    if [ -f "$STATE_DIR/web.port" ]; then
        cat "$STATE_DIR/web.port"
    else
        printf '%s' "$WEB_PORT_DEFAULT"
    fi
}

web_url() {
    local host port
    host=$(web_host)
    port=$(web_port)
    case "$host" in
        0.0.0.0 | :: | "[::]") host="127.0.0.1" ;;
    esac
    printf 'http://%s:%s' "$host" "$port"
}

parse_web_addr() {
    PARSED_WEB_HOST="$WEB_HOST_DEFAULT"
    PARSED_WEB_PORT="$WEB_PORT_DEFAULT"
    while [ $# -gt 0 ]; do
        case "$1" in
            --host)
                [ $# -ge 2 ] || die "--host 需要参数"
                PARSED_WEB_HOST="$2"
                shift 2
                ;;
            --host=*)
                PARSED_WEB_HOST="${1#*=}"
                shift
                ;;
            --port)
                [ $# -ge 2 ] || die "--port 需要参数"
                PARSED_WEB_PORT="$2"
                shift 2
                ;;
            --port=*)
                PARSED_WEB_PORT="${1#*=}"
                shift
                ;;
            *)
                shift
                ;;
        esac
    done
}
