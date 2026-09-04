#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
  echo "用法: bash scripts/restore_check.sh /path/to/rag_data_*.tgz" >&2
  exit 2
fi

CHECK_DIR="$(mktemp -d -t rag-restore-check.XXXXXX)"
trap 'rm -rf -- "$CHECK_DIR"' EXIT

python3 - "$ARCHIVE" "$CHECK_DIR" <<'PY'
import os
import sqlite3
import sys
import tarfile
from pathlib import Path

archive_path = Path(sys.argv[1]).resolve()
root = Path(sys.argv[2]).resolve()

with tarfile.open(archive_path, 'r:gz') as archive:
    members = archive.getmembers()
    for member in members:
        if member.issym() or member.islnk():
            raise SystemExit(f'拒绝符号链接条目: {member.name}')
        target = (root / member.name).resolve()
        if os.path.commonpath((str(root), str(target))) != str(root):
            raise SystemExit(f'拒绝越界条目: {member.name}')
    archive.extractall(root)

db_path = root / 'rag.db'
if not db_path.is_file():
    raise SystemExit('备份中缺少 rag.db')

db = sqlite3.connect(db_path)
try:
    integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
    if integrity != 'ok':
        raise SystemExit(f'SQLite integrity_check 失败: {integrity}')
    users = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    documents = db.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
    chunks = db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
finally:
    db.close()

uploads = root / 'uploads'
upload_files = sum(1 for item in uploads.rglob('*') if item.is_file()) if uploads.exists() else 0
print(f'restore check ok: users={users}, documents={documents}, chunks={chunks}, upload_files={upload_files}')
PY
