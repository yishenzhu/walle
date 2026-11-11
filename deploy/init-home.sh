#!/bin/sh
# 初始化 WALLE_HOME：建目录、补默认 agents/skills 与配置。只补缺失文件，不覆盖已有内容。
set -eu

: "${WALLE_HOME:?WALLE_HOME 未设置}"
TEMPLATES="${WALLE_TEMPLATES:-$(cd "$(dirname "$0")/.." && pwd)/templates}"

mkdir -p "$WALLE_HOME/data/canvas" "$WALLE_HOME/logs" "$WALLE_HOME/.agent"

if [ -f "$TEMPLATES/conf.default.yaml" ] && [ ! -f "$WALLE_HOME/conf.yaml" ]; then
    cp "$TEMPLATES/conf.default.yaml" "$WALLE_HOME/conf.yaml"
fi

src="$TEMPLATES/agent"
dst="$WALLE_HOME/.agent"
[ -d "$src" ] || exit 0

digest=$(cd "$src" && find . -type f | sort | sha1sum | cut -d' ' -f1)
if [ -f "$dst/.templates" ] && [ "$(cat "$dst/.templates")" = "$digest" ]; then
    exit 0
fi

cd "$src"
find . -type f | while read -r file; do
    relative=${file#./}
    if [ ! -e "$dst/$relative" ]; then
        mkdir -p "$dst/$(dirname "$relative")"
        cp "$file" "$dst/$relative"
        echo "default installed: $dst/$relative"
    fi
done
printf '%s\n' "$digest" > "$dst/.templates"
