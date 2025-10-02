# dbbak — Minimal MySQL backup CLI (MVP)

A tiny, readable CLI that automates **full + incremental physical backups with XtraBackup** and **point‑in‑time recovery (PITR)** via **`mysqlbinlog | mysql`**. Artifacts are stored on an **NFS mount** with simple, machine‑sortable names and minimal JSON metadata.

> **Philosophy:** keep it boring, predictable, and 3 AM‑friendly. Dry‑run first; flip `--exec` only when ready.

---

## Features

* **Full** backups (produce both *redo‑only* and *final* prepared variants)
* **Incremental** backups chained to a full
* **Binlog shipping** (copy + gzip + index) for PITR
* **One‑command restore**: prepare → (optional) PITR to rounded time → finalize
* **Minimal metadata**: `manifest.json` and `binlogs/index.json`
* **Dry‑run by default**; add `--exec` to actually run commands

---

## Requirements

* Python 3.11+
* System tools:

  * `xtrabackup` (Percona XtraBackup)
  * `mysqlbinlog` and `mysql` (from MySQL/Percona client)
  * A MySQL **option file** for credentials (e.g., `~/.my.cnf`)
* An **NFS mount** for backup storage (e.g., `/mnt/backup/mysql`)

---

## Install (with `uv`)

```bash
# from project root
uv pip install -e .
# or directly run the script without install
python -m src.dbbak --help
```

This project exposes a single console script `dbbak` when installed.

---

## Configuration

Place a minimal YAML file at `~/.dbbak.yml` (or pass `--config-path` to commands).

```yaml
# ~/.dbbak.yml
mysql_option_file: /home/backup/.my.cnf
nfs_root: /mnt/backup/mysql
xtrabackup: /usr/bin/xtrabackup
mysqlbinlog: /usr/bin/mysqlbinlog
mysqldump: /usr/bin/mysqldump  # not used in MVP commands but kept for future

# behavior
pitr_round_minutes: 15         # 2:41 -> 2:30 (floor)
compress: true

# optional: where to read live binlogs from when shipping
binlog_source_dir: /var/lib/mysql  # directory containing mysql-bin.*
```

**Credentials:** put them in `~/.my.cnf` (never on CLI). Example:

```ini
[client]
user=backup
password=yourStrongPassword
host=127.0.0.1
port=3306
```

---

## Directory layout (NFS target)

```
/mnt/backup/mysql/
  manifest.json
  full_2025-10-02T02-00Z/
    raw/
    prepared-redo/
    prepared-final/
    meta.json
  incr_2025-10-03T02-00Z_from_2025-10-02T02-00Z/
    raw/
    meta.json
  binlogs/
    index.json
    binlog_2025-10-02T14-00Z_mysql-bin.000123.gz
    binlog_2025-10-02T14-05Z_mysql-bin.000124.gz
```

* **ISO 8601 UTC** timestamps in names for lexicographic sorting.
* Incrementals include their **parent full timestamp** in the name.

---

## Commands

All commands are **dry‑run** unless you add `--exec`.

### `dbbak init`

Create the NFS layout, `manifest.json`, and `binlogs/index.json` if missing.

### `dbbak backup-full [--exec]`

Take a physical full backup with XtraBackup.

* Writes to `full_<TS>/raw/`.
* Produces two prepared variants:

  * `prepared-redo/` — **redo‑only** (kept open to accept incrementals/PITR)
  * `prepared-final/` — **redo+undo** (clean, startable snapshot)
* Records `meta.json` and appends to `manifest.json`.

### `dbbak backup-incr [--base-ts ISOZ] [--exec]`

Take an incremental backup based on the latest (or specified) full.

* Writes to `incr_<TS>_from_<BASE>/raw/`.
* Records `meta.json` and appends to `manifest.json`.
* (MVP applies incrementals during **restore**, not during backup, to keep artifacts immutable.)

### `dbbak ship-binlogs [--exec]`

Copy **new** binlog files from `binlog_source_dir` to `binlogs/`, gzip them, and update `binlogs/index.json`.

* Uses file names and mtimes; for full correctness you can later parse with `mysqlbinlog --verbose`.

### `dbbak restore [--time "YYYY-MM-DD HH:MM"] [--no-pitr] [--workdir PATH] [--exec]`

End‑to‑end restore:

1. Pick the **latest full** backup before the target time (defaults to *now*),
   then **floor** to `pitr_round_minutes` (e.g., 15 min).
2. Copy its `prepared-redo/` to a **working datadir**.
3. Apply all **incrementals** up to target using `--apply-log-only`.
4. If PITR is enabled (default), run `mysqlbinlog --start/--stop-datetime | mysql` up to the floored time.
5. Finalize with a clean prepare (redo+undo) so MySQL can start on the datadir.

### `dbbak list`

Quickly list full backups, their incrementals, and the binlog time window from the index.

---

## Usage examples

### 1) Initialize the backup store

```bash
dbbak init --config-path ~/.dbbak.yml
```

### 2) Run a full backup (dry‑run first, then execute)

```bash
dbbak backup-full
# looks good?
dbbak backup-full --exec
```

### 3) Run a daily incremental

```bash
dbbak backup-incr --exec
```

### 4) Ship binlogs (every couple of minutes)

```bash
dbbak ship-binlogs --exec
```

### 5) Restore to **now** (floored to 15 min)

```bash
# creates a temp working datadir and prints its path when done
dbbak restore --exec
# start MySQL on that dir
mysqld --datadir=/path/printed/by/restore --port=3307 --socket=/tmp/restore.sock &
```

### 6) Restore to a specific time, e.g., 2025‑10‑02 14:41 (floors to 14:30)

```bash
dbbak restore --time "2025-10-02 14:41" --exec
```

### 7) Restore to latest incremental **without** binlogs

```bash
dbbak restore --no-pitr --exec
```

---

## Cron integration (Asia/Ho_Chi_Minh assumed)

> Adjust minutes to avoid peak loads and align with maintenance windows.

```cron
# Full backup every 14 days at 02:00
0 2 */14 * * dbbak backup-full --exec >> /var/log/dbbak.log 2>&1

# Incremental backup daily at 02:00 (non-full days)
0 2 * * *    dbbak backup-incr --exec >> /var/log/dbbak.log 2>&1

# Binlog shipping every 2 minutes
*/2 * * * *  dbbak ship-binlogs --exec >> /var/log/dbbak.log 2>&1

# Optional: health/verify jobs can be added later
```

---

## Metadata JSON schemas

The MVP uses **simple JSON** structures. They’re intentionally minimal; evolve as needed.

### `manifest.json`

* **Type:** array of entries
* **Entries:**

```json
{
  "type": "full" | "incr",
  "ts": "2025-10-02T02-00Z",
  "base": null | "2025-10-02T02-00Z",  
  "size": 123456  
}
```

* `type`: backup kind
* `ts`: backup timestamp (ISOZ), matches directory name
* `base`: for incrementals, the **parent full** timestamp; `null` for fulls
* `size`: (optional) total uncompressed size in bytes if you record it

**Example:**

```json
[
  { "type": "full", "ts": "2025-10-02T02-00Z", "base": null, "size": 934857392 },
  { "type": "incr", "ts": "2025-10-03T02-00Z", "base": "2025-10-02T02-00Z", "size": 34857392 }
]
```

### `full_.../meta.json`

```json
{
  "type": "full",
  "ts": "2025-10-02T02-00Z",
  "paths": {
    "raw": "raw/",
    "prepared_redo": "prepared-redo/",
    "prepared_final": "prepared-final/"
  },
  "xtrabackup": {
    "cmd": "xtrabackup --backup ...",
    "lsn_start": 123456789,
    "lsn_end": 123999999
  }
}
```

* Add `gtid_executed` / `server_uuid` later if you want stricter checks.

### `incr_.../meta.json`

```json
{
  "type": "incr",
  "ts": "2025-10-03T02-00Z",
  "base": "2025-10-02T02-00Z",
  "paths": { "raw": "raw/" },
  "xtrabackup": {
    "cmd": "xtrabackup --backup --incremental ...",
    "lsn_from": 123999999,
    "lsn_to": 124123456
  }
}
```

### `binlogs/index.json`

* **Type:** object with `files` array
* **File entry:**

```json
{
  "name": "mysql-bin.000123",
  "stored_as": "binlog_2025-10-02T14-00Z_mysql-bin.000123.gz",
  "size": 1048576,
  "first_event_time": "2025-10-02 14:00:00",
  "last_event_time":  "2025-10-02 14:05:00"
}
```

* Times are UTC strings compatible with `--start-datetime/--stop-datetime`.

**Example:**

```json
{
  "files": [
    {
      "name": "mysql-bin.000123",
      "stored_as": "binlog_2025-10-02T14-00Z_mysql-bin.000123.gz",
      "size": 1048576,
      "first_event_time": "2025-10-02 14:00:00",
      "last_event_time":  "2025-10-02 14:05:00"
    },
    {
      "name": "mysql-bin.000124",
      "stored_as": "binlog_2025-10-02T14-05Z_mysql-bin.000124.gz",
      "size": 1258291,
      "first_event_time": "2025-10-02 14:05:01",
      "last_event_time":  "2025-10-02 14:10:00"
    }
  ]
}
```

---

## PITR behavior & rounding

* If you request `--time "2025-10-02 14:41"` and `pitr_round_minutes: 15`, the CLI targets **14:30**.
* Rationale: avoids edge cases when the newest binlog is still being written; aligns to predictable windows.

---

## Safety notes

* Run backups on a **replica** if possible.
* Ensure NFS is exported with `noexec,nodev,nosuid` and restricted to trusted subnets.
* Verify you have the required MySQL privileges for the backup user.
* Keep clocks in sync (NTP) so timestamps and rounding behave as expected.

---

## Troubleshooting

* **`No full backup found before target time`**: run `dbbak list`, ensure there’s a `full_...` older than your target.
* **`No binlogs to apply`**: confirm `binlog_source_dir` is set and `ship-binlogs` runs; check `binlogs/index.json`.
* **Restore too slow for 30‑min RTO**: prepare fulls in advance (keep a recent `prepared-redo/` fresh), ship binlogs more frequently, or restore on faster storage.

---

## Roadmap ideas (optional)

* Parse `xtrabackup_info` for exact LSN/GTID and stricter binlog windows
* `backup logical` and table‑level restore helpers
* SQLite manifest for richer queries
* I/O throttling and maintenance‑window aware scheduling

