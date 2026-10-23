#!/usr/bin/env bash
# agent / Web / 可观测性生命周期。

OBS_DIR="$PROJECT_ROOT/observability"
OBS_COMPOSE="$OBS_DIR/docker-compose.yaml"
AGENT_LOG="$LOG_DIR/agent.log"
WEB_LOG="$LOG_DIR/web.log"

compose_available() {
    command -v docker >/dev/null 2>&1 \
        && docker compose version >/dev/null 2>&1 \
        && [ -f "$OBS_COMPOSE" ]
}

compose() {
    docker compose -f "$OBS_COMPOSE" "$@"
}

require_compose() {
    command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker"
    docker compose version >/dev/null 2>&1 || die "Docker Compose 插件不可用，请升级 Docker"
    [ -f "$OBS_COMPOSE" ] || die "未找到 Docker Compose 文件: $OBS_COMPOSE"
}

# ── agent ─────────────────────────────────────────────

agent_pids() {
    project_pids "walle.main"
}

start_agent_bg() {
    if port_in_use "$AGENT_PORT"; then
        info "agent 已在运行（端口 $AGENT_PORT）"
        return 0
    fi
    step "后台启动 agent ..."
    local pid
    pid=$(run_py_background "$AGENT_LOG" -m walle.main)
    if ! wait_for_port "$AGENT_PORT" "$READY_TIMEOUT"; then
        error "agent 未在 ${READY_TIMEOUT}s 内就绪，请检查日志: $AGENT_LOG"
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
        exit 1
    fi
    info "agent 已启动（pid $pid，端口 $AGENT_PORT）"
}

ensure_agent_bg() {
    if port_in_use "$AGENT_PORT"; then
        info "agent 已在运行，直接连接"
        return 0
    fi
    start_agent_bg
}

run_agent_foreground() {
    step "启动 walle agent ..."
    info "使用 Python: $PY"
    exec_py -m walle.main
}

stop_agent() {
    stop_pids "agent 服务端" $(agent_pids)
}

require_agent() {
    port_in_use "$AGENT_PORT" \
        || die "agent 服务端未在运行，请先执行: ./scripts/run.sh up"
}

# ── Web ───────────────────────────────────────────────

web_pids() {
    project_pids "walle.channel.web"
}

start_web_bg() {
    local args=("$@")
    parse_web_addr "${args[@]}"
    save_web_addr "$PARSED_WEB_HOST" "$PARSED_WEB_PORT"
    if port_in_use "$PARSED_WEB_PORT"; then
        info "Web 已在运行: http://$PARSED_WEB_HOST:$PARSED_WEB_PORT"
        return 0
    fi
    step "后台启动 Web 通道 ..."
    local pid
    pid=$(run_py_background "$WEB_LOG" -m walle.channel.web "${args[@]}")
    if ! wait_for_port "$PARSED_WEB_PORT" "$READY_TIMEOUT"; then
        error "Web 未在 ${READY_TIMEOUT}s 内就绪，请检查日志: $WEB_LOG"
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
        exit 1
    fi
    info "Web 已启动: http://$PARSED_WEB_HOST:$PARSED_WEB_PORT（pid $pid）"
}

run_web_foreground() {
    local args=("$@")
    parse_web_addr "${args[@]}"
    save_web_addr "$PARSED_WEB_HOST" "$PARSED_WEB_PORT"
    if port_in_use "$PARSED_WEB_PORT"; then
        info "Web 通道已在运行: http://$PARSED_WEB_HOST:$PARSED_WEB_PORT（重启请先 stop web）"
        return 0
    fi
    step "启动 Web 对话通道 ..."
    info "Web 对话: http://$PARSED_WEB_HOST:$PARSED_WEB_PORT（Ctrl+C 只停 Web；agent 用 stop 停止）"
    exec_py -m walle.channel.web "${args[@]}"
}

stop_web() {
    stop_pids "Web 服务" $(web_pids)
}

# ── 可观测性 ──────────────────────────────────────────

start_obs() {
    require_compose
    step "启动可观测性容器 (otel-collector / tempo / mimir / grafana) ..."
    compose up -d
    info "可观测性容器已启动"
    info "  Grafana: http://localhost:3000  (admin/admin)"
    info "  Tempo:   http://localhost:3200"
    info "  Mimir:   http://localhost:9009"
}

stop_obs() {
    if ! compose_available; then
        info "Docker 或可观测性配置不可用，跳过容器停止"
        return 0
    fi
    step "停止可观测性容器 ..."
    if compose down; then
        info "可观测性容器已停止"
    else
        warn "可观测性容器停止失败（Docker 可能未运行）"
    fi
}

obs_status() {
    if ! compose_available; then
        info "可观测性: Docker / compose 配置不可用"
        return 0
    fi
    local services
    services=$(compose ps --services --filter status=running 2>/dev/null | paste -sd, - || true)
    if [ -n "$services" ]; then
        info "可观测性: 运行中（$services）"
    else
        info "可观测性: 未运行"
    fi
}

stop_all() {
    stop_agent
    stop_web
    stop_obs
}

# ── 状态 ──────────────────────────────────────────────

show_status() {
    local pids host port url
    if port_in_use "$AGENT_PORT"; then
        pids=$(agent_pids | paste -sd' ' - || true)
        info "agent: 运行中（端口 $AGENT_PORT${pids:+, pid $pids}）"
        info "  日志: $AGENT_LOG"
    else
        info "agent: 未运行"
    fi

    host=$(web_host)
    port=$(web_port)
    url=$(web_url)
    if port_in_use "$port"; then
        pids=$(web_pids | paste -sd' ' - || true)
        info "Web: 运行中 $url${pids:+（pid $pids）}"
        info "  日志: $WEB_LOG"
    else
        info "Web: 未运行"
    fi

    obs_status
}

list_sessions() {
    require_agent
    step "运行中的会话 ..."
    run_py -m walle.channel.cli --list
}
