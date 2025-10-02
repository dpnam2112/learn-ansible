#!/usr/bin/env bash
set -euo pipefail

# --- Config (env-overridable) ---
BASE_DIR="${BASE_DIR:-/backups/mysql}"     # can be an NFS mount
MONTH="${MONTH:-$(date +%Y-%m)}"           # override if needed (e.g., MONTH=2025-11)
BACKUP_DIR="${BASE_DIR}/${MONTH}/backup"
BINLOG_DIR="${BACKUP_DIR}/binlogs"

main() {
  mkdir -p "${BINLOG_DIR}"
  echo "Created:"
  echo "  ${BACKUP_DIR}"
  echo "  ${BINLOG_DIR}"
}

main "$@"

