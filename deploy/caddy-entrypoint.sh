#!/bin/sh
# 未设置 WALLE_AUTH_HASH 时不启用基础认证，Caddyfile 通过 import 引入生成片段。
set -eu

if [ -n "${WALLE_AUTH_HASH:-}" ]; then
    printf 'basic_auth {\n\t%s %s\n}\n' "${WALLE_AUTH_USER:-admin}" "$WALLE_AUTH_HASH" > /tmp/auth.caddy
else
    : > /tmp/auth.caddy
fi

exec /usr/bin/caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
