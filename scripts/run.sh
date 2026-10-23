#!/usr/bin/env bash
#
# walle 控制台：服务启停、会话、日志与开发命令。
# 详细用法: ./scripts/run.sh help
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/common.sh
source "$PROJECT_ROOT/scripts/lib/common.sh"
# shellcheck source=lib/services.sh
source "$PROJECT_ROOT/scripts/lib/services.sh"
# shellcheck source=lib/commands.sh
source "$PROJECT_ROOT/scripts/lib/commands.sh"

case "${1:-}" in
    "" | -*)
        # 无参数与旧版 --flag 用法保持兼容
        legacy_main "$@"
        ;;
    start | up)
        shift
        cmd_start "$@"
        ;;
    stop | down)
        shift
        cmd_stop "$@"
        ;;
    restart)
        shift
        cmd_restart "$@"
        ;;
    agent | serve)
        shift
        cmd_agent "$@"
        ;;
    web)
        shift
        cmd_web "$@"
        ;;
    cli)
        shift
        cmd_cli "$@"
        ;;
    attach)
        shift
        cmd_attach "$@"
        ;;
    list | sessions)
        shift
        cmd_list "$@"
        ;;
    status)
        shift
        cmd_status "$@"
        ;;
    logs | log)
        shift
        cmd_logs "$@"
        ;;
    obs)
        shift
        cmd_obs "$@"
        ;;
    open)
        shift
        cmd_open "$@"
        ;;
    test)
        shift
        cmd_test "$@"
        ;;
    lint)
        shift
        cmd_lint "$@"
        ;;
    fmt | format)
        shift
        cmd_fmt "$@"
        ;;
    doctor)
        shift
        cmd_doctor "$@"
        ;;
    help | --help | -h)
        usage
        ;;
    *)
        error "未知命令: $1"
        echo
        usage
        exit 1
        ;;
esac
