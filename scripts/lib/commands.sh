#!/usr/bin/env bash
# run.sh 子命令与旧参数兼容。

usage() {
    cat <<EOF
walle 控制台

用法:
  $(basename "$0") <命令> [参数...]

启动 / 停止:
  up | start          后台启动 agent + Web + 可观测性
  down | stop [目标]  停止全部；目标: agent / web / obs / all
  restart [目标]      重启全部或单个服务（默认 all）
  status              查看服务状态与会话列表

对话:
  agent               前台启动 agent（默认同时启动可观测性）
  cli [参数...]       确保 agent 后启动 CLI 客户端
  web [参数...]       确保 agent 后前台启动 Web（支持 --host / --port）
  attach <会话ID>     恢复已有会话
  list                列出会话
  open                在浏览器打开 Web

日志 / 运维:
  logs [目标] [-f]    查看日志；目标: agent（默认）/ web / obs
  obs [up|down|status|logs]  可观测性容器操作
  doctor              检查 Python / 配置 / Docker / 端口

开发:
  test [pytest 参数...]       运行测试（默认 tests/）
  lint [ruff 参数...]         静态检查（默认 .）
  fmt  [ruff 参数...]         格式化（默认 .）

其他:
  help                显示本帮助

示例:
  ./scripts/run.sh up                     # 一次性后台起全套
  ./scripts/run.sh web --port 8081        # 前台起 Web（自定义端口）
  ./scripts/run.sh cli --agent learning   # CLI 新会话使用 learning agent
  ./scripts/run.sh logs agent -f          # 跟踪 agent 日志
  ./scripts/run.sh restart web            # 按上次 host/port 重启 Web
  ./scripts/run.sh test -k course         # 只跑课程相关测试

兼容旧参数:
  --no-obs  --obs-only  --stop  --stop-agent  --stop-web  --stop-obs
  --test  --cli  --web  --attach <id>  --status  --help
EOF
}

# ── 服务命令 ──────────────────────────────────────────

cmd_start() {
    local with_obs=true with_web=true host="$WEB_HOST_DEFAULT" port="$WEB_PORT_DEFAULT"
    while [ $# -gt 0 ]; do
        case "$1" in
            --no-obs) with_obs=false; shift ;;
            --no-web) with_web=false; shift ;;
            --host) [ $# -ge 2 ] || die "--host 需要参数"; host="$2"; shift 2 ;;
            --host=*) host="${1#*=}"; shift ;;
            --port) [ $# -ge 2 ] || die "--port 需要参数"; port="$2"; shift 2 ;;
            --port=*) port="${1#*=}"; shift ;;
            --help | -h) usage; exit 0 ;;
            *) die "start 未知参数: $1（帮助: ./scripts/run.sh help）" ;;
        esac
    done
    if [ "$with_obs" = true ]; then
        start_obs
    fi
    start_agent_bg
    if [ "$with_web" = true ]; then
        start_web_bg --host "$host" --port "$port"
    fi
    echo
    show_status
    echo
    info "停止全部: ./scripts/run.sh down"
}

cmd_stop() {
    local targets=("$@")
    if [ "${#targets[@]}" -eq 0 ]; then
        targets=(all)
    fi
    local target
    for target in "${targets[@]}"; do
        case "$target" in
            all) stop_all ;;
            agent) stop_agent ;;
            web) stop_web ;;
            obs) stop_obs ;;
            *) die "stop 未知目标: $target（可选: agent / web / obs / all）" ;;
        esac
    done
}

cmd_restart() {
    local target="${1:-all}"
    [ $# -gt 0 ] && shift
    case "$target" in
        all)
            stop_all
            cmd_start
            ;;
        agent)
            stop_agent
            start_agent_bg
            ;;
        web)
            stop_web
            start_web_bg --host "$(web_host)" --port "$(web_port)"
            ;;
        obs)
            stop_obs
            start_obs
            ;;
        *) die "restart 未知目标: $target（可选: all / agent / web / obs）" ;;
    esac
}

cmd_agent() {
    local with_obs=true
    while [ $# -gt 0 ]; do
        case "$1" in
            --no-obs) with_obs=false; shift ;;
            --help | -h) usage; exit 0 ;;
            *) die "agent 未知参数: $1" ;;
        esac
    done
    if [ "$with_obs" = true ]; then
        start_obs
    fi
    run_agent_foreground
}

cmd_web() {
    ensure_agent_bg
    run_web_foreground "$@"
}

cmd_cli() {
    ensure_agent_bg
    step "启动 CLI 交互客户端 ..."
    exec_py -m walle.channel.cli "$@"
}

cmd_attach() {
    [ $# -ge 1 ] && [ -n "$1" ] || die "attach 需要会话 ID，如: ./scripts/run.sh attach cli-xxx"
    require_agent
    step "连接会话 $1 ..."
    exec_py -m walle.channel.cli --attach "$1"
}

cmd_list() {
    list_sessions
}

cmd_status() {
    show_status
    if port_in_use "$AGENT_PORT"; then
        echo
        list_sessions
    fi
}

cmd_open() {
    local url
    url=$(web_url)
    local port
    port=$(web_port)
    if ! port_in_use "$port"; then
        die "Web 未运行（先执行: ./scripts/run.sh up 或 ./scripts/run.sh web）"
    fi
    info "打开 $url"
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$url" >/dev/null 2>&1 &
    elif command -v open >/dev/null 2>&1; then
        open "$url"
    else
        warn "未找到 xdg-open / open，请手动访问: $url"
    fi
}

cmd_logs() {
    local target="agent"
    local follow=false
    local lines=100
    while [ $# -gt 0 ]; do
        case "$1" in
            agent | web | obs) target="$1"; shift ;;
            -f | --follow) follow=true; shift ;;
            -n | --lines) [ $# -ge 2 ] || die "-n 需要行数"; lines="$2"; shift 2 ;;
            --lines=*) lines="${1#*=}"; shift ;;
            --help | -h) usage; exit 0 ;;
            *) die "logs 未知参数: $1" ;;
        esac
    done
    if [ "$target" = "obs" ]; then
        require_compose
        local args=(logs --tail="$lines")
        [ "$follow" = true ] && args+=(-f)
        compose "${args[@]}"
        return
    fi
    local file
    if [ "$target" = "web" ]; then
        file="$WEB_LOG"
    else
        file="$AGENT_LOG"
    fi
    [ -f "$file" ] || die "日志不存在: $file"
    local args=(-n "$lines")
    [ "$follow" = true ] && args+=(-f)
    tail "${args[@]}" "$file"
}

cmd_obs() {
    local action="${1:-up}"
    [ $# -gt 0 ] && shift
    case "$action" in
        up | start) start_obs ;;
        down | stop) stop_obs ;;
        status) obs_status ;;
        logs)
            require_compose
            compose logs "$@"
            ;;
        *) die "obs 未知操作: $action（可选: up / down / status / logs）" ;;
    esac
}

# ── 开发命令 ──────────────────────────────────────────

cmd_test() {
    step "运行测试 ..."
    if [ $# -eq 0 ]; then
        run_py -m pytest tests/
    else
        run_py -m pytest "$@"
    fi
}

cmd_lint() {
    step "运行 ruff check ..."
    if [ $# -eq 0 ]; then
        run_py -m ruff check .
    else
        run_py -m ruff check "$@"
    fi
}

cmd_fmt() {
    step "运行 ruff format ..."
    if [ $# -eq 0 ]; then
        run_py -m ruff format .
    else
        run_py -m ruff format "$@"
    fi
}

cmd_doctor() {
    local failed=0
    step "walle 环境自检 ..."

    if [ -x "$VENV_PYTHON" ]; then
        info "Python: $PY（项目 .venv）"
    else
        warn "未找到 .venv，使用系统 Python: $PY"
    fi

    if run_py -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
        info "Python 版本: $("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
    else
        error "Python 版本低于 3.12（pyproject 要求 >=3.12）"
        failed=1
    fi

    if run_py -c 'import walle' >/dev/null 2>&1; then
        info "包导入: walle OK"
    else
        error "无法导入 walle，请检查 PYTHONPATH / 安装: pip install -e \".[dev]\""
        failed=1
    fi

    if run_py -c 'import pytest' >/dev/null 2>&1; then
        info "pytest: OK"
    else
        warn "pytest 未安装：pip install -e \".[dev]\""
    fi

    if run_py -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
        info "Web 依赖: fastapi / uvicorn OK"
    else
        warn "Web 依赖缺失：pip install fastapi uvicorn"
    fi

    [ -f "$PROJECT_ROOT/conf.yaml" ] && info "配置: conf.yaml" \
        || warn "缺少 conf.yaml（cp conf.yaml.example conf.yaml）"
    [ -f "$PROJECT_ROOT/.env" ] && info "环境变量: .env" \
        || warn "缺少 .env（cp .env.example .env 并填写 API Key）"

    if compose_available; then
        info "Docker / Compose: OK"
    else
        warn "Docker / Compose 不可用，可观测性需 --no-obs"
    fi

    if port_in_use "$AGENT_PORT"; then
        info "端口 $AGENT_PORT: agent 正在运行"
    else
        info "端口 $AGENT_PORT: 空闲"
    fi
    local port
    port=$(web_port)
    if port_in_use "$port"; then
        info "端口 $port: Web 正在运行（$(web_url)）"
    else
        info "端口 $port: 空闲"
    fi

    if [ "$failed" -ne 0 ]; then
        die "自检未通过"
    fi
    info "自检通过"
}

# ── 旧参数兼容 ────────────────────────────────────────

legacy_main() {
    local no_obs=false obs_only=false
    local stop_agent_flag=false stop_web_flag=false stop_obs_flag=false stop_all_flag=false
    local test_only=false status_flag=false attach_flag=false
    local attach_id=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --no-obs) no_obs=true ;;
            --obs-only) obs_only=true ;;
            --stop) stop_all_flag=true ;;
            --stop-agent) stop_agent_flag=true ;;
            --stop-web) stop_web_flag=true ;;
            --stop-obs) stop_obs_flag=true ;;
            --test) test_only=true ;;
            --status) status_flag=true ;;
            --attach)
                attach_flag=true
                shift
                attach_id="${1:-}"
                ;;
            --cli)
                shift
                cmd_cli "$@"
                return
                ;;
            --web)
                shift
                cmd_web "$@"
                return
                ;;
            --help | -h)
                usage
                return
                ;;
            *)
                error "未知参数: $1"
                usage
                exit 1
                ;;
        esac
        shift || true
    done

    if [ "$stop_all_flag" = true ]; then
        stop_all
        return
    fi
    if [ "$stop_agent_flag" = true ] || [ "$stop_web_flag" = true ] || [ "$stop_obs_flag" = true ]; then
        [ "$stop_agent_flag" = true ] && stop_agent
        [ "$stop_web_flag" = true ] && stop_web
        [ "$stop_obs_flag" = true ] && stop_obs
        return
    fi
    if [ "$attach_flag" = true ]; then
        cmd_attach "$attach_id"
        return
    fi
    if [ "$status_flag" = true ]; then
        cmd_status
        return
    fi
    if [ "$test_only" = true ]; then
        cmd_test
        return
    fi
    if [ "$obs_only" = true ]; then
        start_obs
        info "可观测性容器已在后台运行（停止: ./scripts/run.sh stop obs）"
        return
    fi

    if [ "$no_obs" = false ]; then
        start_obs
    fi
    run_agent_foreground
}
