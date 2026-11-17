#!/bin/sh
# 备份循环：定期把数据卷打包到 /backup，只保留最近 N 份。
set -eu

INTERVAL="${BACKUP_INTERVAL:-86400}"
KEEP="${BACKUP_KEEP:-7}"

trap 'exit 0' TERM INT
mkdir -p /backup

while true; do
    stamp=$(date +%Y%m%d-%H%M%S)
    target="/backup/walle-$stamp.tar.gz"
    if tar czf "$target.part" -C /data .; then
        mv "$target.part" "$target"
        chmod 644 "$target"
        echo "backup ok: $target"
    else
        rm -f "$target.part"
        echo "backup failed: $target" >&2
    fi

    ls -1t /backup/walle-*.tar.gz 2>/dev/null | tail -n "+$((KEEP + 1))" | while read -r old; do
        rm -f "$old"
        echo "removed: $old"
    done

    sleep "$INTERVAL"
done
