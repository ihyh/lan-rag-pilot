#!/usr/bin/env bash
set -euo pipefail

# 安装/更新每日备份任务；只修改当前 Ubuntu 用户的 crontab。
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="${1:-$PROJECT_DIR/backups}"
RUN_AT="${2:-02:30}"

if [[ ! "$RUN_AT" =~ ^([01][0-9]|2[0-3]):([0-5][0-9])$ ]]; then
  echo "时间格式应为 HH:MM，例如 02:30" >&2
  exit 2
fi
mkdir -p -- "$BACKUP_DIR"
BACKUP_DIR="$(CDPATH= cd -- "$BACKUP_DIR" && pwd)"
HOUR="${BASH_REMATCH[1]}"
MINUTE="${BASH_REMATCH[2]}"
MARKER="# rag-pilot daily backup"
CRON_LINE="$MINUTE $HOUR * * * cd '$PROJECT_DIR' && /usr/bin/env bash '$PROJECT_DIR/scripts/backup_data.sh' '$BACKUP_DIR' >> '$BACKUP_DIR/cron.log' 2>&1 $MARKER"

CURRENT="$(crontab -l 2>/dev/null || true)"
CRON_TMP="$(mktemp)"
printf '%s\n' "$CURRENT" | grep -vF "$MARKER" > "$CRON_TMP" || true
printf '%s\n' "$CRON_LINE" >> "$CRON_TMP"
crontab "$CRON_TMP"
rm -f -- "$CRON_TMP"

echo "已安装每日备份任务: $RUN_AT"
echo "$CRON_LINE"
