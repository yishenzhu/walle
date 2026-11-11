#!/bin/sh
# 容器入口：需要时初始化 WALLE_HOME，再把控制权交回 CMD。
set -eu

if [ "${WALLE_INIT:-0}" = "1" ]; then
    here=$(cd "$(dirname "$0")" && pwd)
    "$here/init-home.sh"
fi

exec "$@"
