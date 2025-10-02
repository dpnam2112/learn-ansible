#!/usr/bin/env bash
set -euo pipefail

# --- Config (env-overridable) ---
BASE_DIR="${BASE_DIR:-/backups/mysql}"
MONTH="${MONTH:-$(date +%Y-%m)}"
BACKUP_DIR="${BASE_DIR}/${MONTH}/backup"

XTRABACKUP_BIN="${XTRABACKUP_BIN:-/usr/bin/xtrabackup}"
MYSQL_OPTION_FILE="${MYSQL_OPTION_FILE:-/etc/mysql/my.cnf}"   # contains [client] user, password, host, port
PARALLEL="${PARALLEL:-4}"
EXTRA_OPTS="${EXTRA_OPTS:-}"   # e.g. "--compress --compress-threads=2"

timestamp_iso() {
  # e.g. 2025-10-02T10-35-12Z (avoid ':' for cross-platform filesystems)
  date -u +"%Y-%m-%dT%H-%M-%SZ"
}

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "Required binary not found: $1" >&2; exit 1; }
}

main() {
  require "${XTRABACKUP_BIN}"
  mkdir -p "${BACKUP_DIR}"

  local ts dir
  ts="$(timestamp_iso)"
  dir="${BACKUP_DIR}/full-${ts}"

  echo "Starting FULL backup -> ${dir}"
  # Nice/ionice so prod stays happy
  ionice -c2 -n7 nice -n 19 \
  "${XTRABACKUP_BIN}" \
    --defaults-file="${MYSQL_OPTION_FILE}" \
    --backup \
    --target-dir="${dir}" \
    --parallel="${PARALLEL}" \
    ${EXTRA_OPTS}

  echo "Full backup completed: ${dir}"
  if [[ -f "${dir}/xtrabackup_binlog_info" ]]; then
    echo "Captured binlog pointer:"
    cat "${dir}/xtrabackup_binlog_info"
  else
    echo "Note: xtrabackup_binlog_info not found (unexpected for InnoDB with binlog enabled)."
  fi
}

main "$@"

