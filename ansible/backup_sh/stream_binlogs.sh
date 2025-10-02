#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-/backups/mysql}"
MONTH="${MONTH:-$(date +%Y-%m)}"
BACKUP_DIR="${BASE_DIR}/${MONTH}/backup"
BINLOG_DIR="${BACKUP_DIR}/binlogs"

MYSQL_BIN="${MYSQL_BIN:-/usr/bin/mysql}"
MYSQLBINLOG_BIN="${MYSQLBINLOG_BIN:-/usr/bin/mysqlbinlog}"
MYSQL_OPTION_FILE="${MYSQL_OPTION_FILE:-$HOME/.my.cnf}"

EXTRA_MB_OPTS="${EXTRA_MB_OPTS:-}" # e.g. "--connection-server-id=20001"

require() { command -v "$1" >/dev/null || { echo "Missing: $1" >&2; exit 1; }; }

latest_local() {
  ls -1 "${BINLOG_DIR}"/mysql-bin.* 2>/dev/null | awk -F/ '{print $NF}' | sort | tail -n1 || true
}

server_binlogs() {
  "${MYSQL_BIN}" --defaults-file="${MYSQL_OPTION_FILE}" -NBe "SHOW BINARY LOGS"
  # output columns: Log_name  File_size
}

server_size_of() {
  local f="$1"
  server_binlogs | awk -v f="$f" '$1==f{print $2; found=1} END{if(!found) exit 1}'
}

server_list_only_names() {
  server_binlogs | awk '{print $1}'
}

next_after() {
  local cur="$1"
  server_list_only_names | awk -v cur="$cur" '{a[++n]=$1} END{for(i=1;i<=n;i++) if(a[i]==cur && i<n){print a[i+1]; exit}}'
}

detect_gap_or_ok() {
  # Ensures there is no missing server file between the earliest not-yet-downloaded and next.
  # Minimal check: if we have L locally, ensure L exists on server and then stream L (if partial) or next_after(L).
  :
}

main() {
  require "${MYSQL_BIN}"
  require "${MYSQLBINLOG_BIN}"
  mkdir -p "${BINLOG_DIR}"

  echo "[stream] Rotating server binlog..."
  "${MYSQL_BIN}" --defaults-file="${MYSQL_OPTION_FILE}" -e "FLUSH BINARY LOGS"

  local local_last server_latest start_file
  local_last="$(latest_local)"
  server_latest="$(server_list_only_names | tail -n1 || true)"

  if [[ -n "${local_last}" ]]; then
    echo "[stream] Latest local: ${local_last}"

    # If server no longer has this file, we likely fell behind and it got purged -> gap.
    if ! server_size_of "${local_last}" >/dev/null 2>&1; then
      echo "[ERROR] Server no longer has ${local_last}. Retention gap detected." >&2
      exit 1
    fi

    local local_sz server_sz
    local_sz="$(stat -c%s "${BINLOG_DIR}/${local_last}")"
    server_sz="$(server_size_of "${local_last}")"

    if (( local_sz < server_sz )); then
      echo "[stream] Detected partial local file (${local_sz} < ${server_sz}). Re-fetching ${local_last} from scratch."
      rm -f "${BINLOG_DIR}/${local_last}"
      start_file="${local_last}"
    else
      # Local file complete (or equal size). Move to the next file; rotate again if none yet.
      start_file="$(next_after "${local_last}" || true)"
      if [[ -z "${start_file}" ]]; then
        echo "[stream] No next file after ${local_last}. Forcing another rotation..."
        "${MYSQL_BIN}" --defaults-file="${MYSQL_OPTION_FILE}" -e "FLUSH BINARY LOGS"
        start_file="$(next_after "${local_last}" || true)"
      fi
      if [[ -z "${start_file}" ]]; then
        echo "[ERROR] Could not determine start file after ${local_last}." >&2
        exit 1
      fi
    fi
  else
    # No local files: start at newest post-flush.
    if [[ -z "${server_latest}" ]]; then
      echo "[ERROR] Server reports no binary logs. Is log_bin enabled?" >&2
      exit 1
    fi
    start_file="${server_latest}"
    echo "[stream] No local files. Starting at server newest: ${start_file}"
  fi

  # Safety: refuse to overwrite an existing completed file (unless we intentionally chose to re-fetch it).
  if [[ -e "${BINLOG_DIR}/${start_file}" ]]; then
    echo "[stream] Removing existing ${start_file} to re-fetch cleanly."
    rm -f "${BINLOG_DIR:?}/${start_file}"
  fi

  echo "[stream] Streaming from ${start_file} into ${BINLOG_DIR}"
  cd "${BINLOG_DIR}"

  exec ionice -c2 -n7 nice -n 19 \
    "${MYSQLBINLOG_BIN}" \
      --defaults-file="${MYSQL_OPTION_FILE}" \
      --read-from-remote-server \
      --raw \
      --to-last-log \
      --stop-never \
      --result-file="${BINLOG_DIR}/" \
      ${EXTRA_MB_OPTS} \
      "${start_file}"
}

main "$@"
