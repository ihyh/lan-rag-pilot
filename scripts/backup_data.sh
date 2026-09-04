#!/usr/bin/env bash
set -euo pipefail
umask 077

# 备份 SQLite 一致性副本和上传文件，不停止业务容器、不删除业务卷。
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

BACKUP_DIR="${1:-$PROJECT_DIR/backups}"
mkdir -p -- "$BACKUP_DIR"
BACKUP_DIR="$(CDPATH= cd -- "$BACKUP_DIR" && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$BACKUP_DIR/rag_data_$STAMP.tgz"
CONTAINER_ID="$(docker compose ps -q rag)"

if [ -z "$CONTAINER_ID" ]; then
  echo "未找到 rag 容器；请先执行 docker compose up -d" >&2
  exit 1
fi

docker compose exec -T rag python - <<'PY'
from pathlib import Path
import shutil
import sqlite3
import tarfile

data = Path('/rag/data')
work = Path('/tmp/rag-backup')
archive_path = Path('/tmp/rag-backup.tgz')
shutil.rmtree(work, ignore_errors=True)
archive_path.unlink(missing_ok=True)
work.mkdir(parents=True)

source = sqlite3.connect(data / 'rag.db')
target = sqlite3.connect(work / 'rag.db')
try:
    source.backup(target)
finally:
    target.close()
    source.close()

with tarfile.open(archive_path, 'w:gz') as archive:
    archive.add(work / 'rag.db', arcname='rag.db')
    uploads = data / 'uploads'
    if uploads.exists():
        archive.add(uploads, arcname='uploads')

shutil.rmtree(work, ignore_errors=True)
print('container backup ready')
PY

docker cp "$CONTAINER_ID:/tmp/rag-backup.tgz" "$ARCHIVE"
docker compose exec -T rag python -c "from pathlib import Path; Path('/tmp/rag-backup.tgz').unlink(missing_ok=True)"

sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
echo "备份完成: $ARCHIVE"
echo "校验文件: $ARCHIVE.sha256"
