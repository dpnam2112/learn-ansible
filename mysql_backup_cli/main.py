#!/usr/bin/env python3
"""
MySQL Backup CLI

What it does
- Monthly series layout; prepare happens at restore time.
- Full + incremental physical backups via XtraBackup.
- Monthly binlog shipping with gzip + sha256 + index.json.
- One-command restore with optional PITR using mysqlbinlog.
- Dry-run by default (prints what would run). Use --exec to actually run.

Config
- Default path: ~/.dbbak.json (JSON instead of YAML).
- Example config (drop this into ~/.dbbak.json):
{
  "mysql_option_file": "~/my.cnf",
  "nfs_root": "~/backups/mysql",
  "xtrabackup": "/usr/bin/xtrabackup",
  "mysqlbinlog": "/usr/bin/mysqlbinlog",
  "mysqldump": "/usr/bin/mysqldump",
  "pitr_round_minutes": 15,
  "compress": true,
  "binlog_source_dir": "/var/lib/mysql",
  "db_host": "127.0.0.1",
  "db_port": 3306,
  "db_user": null,
  "db_password": null,
  "db_ssl_ca": null
}

Usage examples
  python3 mysql_backup_cli_stdlib.py init
  python3 mysql_backup_cli_stdlib.py list
  python3 mysql_backup_cli_stdlib.py backup-full --exec
  python3 mysql_backup_cli_stdlib.py backup-incr --exec
  python3 mysql_backup_cli_stdlib.py ship-binlogs --flush-first --exec
  python3 mysql_backup_cli_stdlib.py restore --time "2025-10-02 14:30" --exec

On-disk layout
/backup_root
├─ series/
│  ├─ YYYY-MM/
│  │  ├─ base/
│  │  │  ├─ raw/
│  │  │  ├─ xtrabackup_binlog_info
│  │  │  └─ meta.json
│  │  ├─ incr/
│  │  │  └─ <ISOZ>/raw/
│  │  └─ manifest.json
└─ binlogs/
   └─ YYYY-MM/
      ├─ mysql-bin.000001.gz
      └─ index.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ISO_Z = "%Y-%m-%dT%H-%MZ"  # e.g., 2025-10-02T14-30Z
DEFAULT_CONFIG_PATH = Path.home() / ".dbbak.json"

# ------------------------------
# Config
# ------------------------------

@dataclass
class Config:
    mysql_option_file: Path
    nfs_root: Path
    xtrabackup: Path
    mysqlbinlog: Path
    mysqldump: Path
    pitr_round_minutes: int = 15
    compress: bool = True
    binlog_source_dir: Optional[Path] = None  # if None, ship-binlogs is noop

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    db_ssl_ca: Optional[Path] = None

    @staticmethod
    def load(path: Path = DEFAULT_CONFIG_PATH) -> "Config":
        if not path.exists():
            print(f"Config not found at {path}. Create it (JSON). See header for example.")
            sys.exit(1)
        try:
            raw = json.loads(path.read_text("utf-8")) or {}
        except json.JSONDecodeError as e:
            print(f"Invalid JSON in {path}: {e}")
            sys.exit(1)
        try:
            return Config(
                mysql_option_file=Path(raw["mysql_option_file"]).expanduser(),
                nfs_root=Path(raw["nfs_root"]).expanduser(),
                xtrabackup=Path(raw["xtrabackup"]).expanduser(),
                mysqlbinlog=Path(raw["mysqlbinlog"]).expanduser(),
                mysqldump=Path(raw["mysqldump"]).expanduser(),
                pitr_round_minutes=int(raw.get("pitr_round_minutes", 15)),
                compress=bool(raw.get("compress", True)),
                binlog_source_dir=(Path(raw["binlog_source_dir"]).expanduser() if raw.get("binlog_source_dir") else None),
                db_host=raw.get("db_host", "127.0.0.1"),
                db_port=int(raw.get("db_port", 3306)),
                db_user=raw.get("db_user"),
                db_password=raw.get("db_password"),
                db_ssl_ca=(Path(raw["db_ssl_ca"]).expanduser() if raw.get("db_ssl_ca") else None),
            )
        except KeyError as e:
            print(f"Missing required config key: {e}")
            sys.exit(1)

# ------------------------------
# Helpers
# ------------------------------

def now_utc_iso() -> str:
    return dt.datetime.utcnow().strftime(ISO_Z)


def floor_to_minutes(t: dt.datetime, minutes: int) -> dt.datetime:
    if minutes <= 0:
        return t
    minute = (t.minute // minutes) * minutes
    return t.replace(minute=minute, second=0, microsecond=0)


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def write_json(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def shlex_quote(s: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=+-]+", s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


def sh(cmd: List[str], *, exec_: bool, cwd: Optional[Path] = None) -> int:
    printable = " ".join(shlex_quote(c) for c in cmd)
    if cwd:
        printable = f"(cd {cwd} && {printable})"
    print(("EXEC: " if exec_ else "DRY : ") + printable)
    if not exec_:
        return 0
    try:
        return subprocess.call(cmd, cwd=str(cwd) if cwd else None)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1


def copy_tree(src: Path, dst: Path, *, exec_: bool) -> None:
    if exec_:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True)
    print(("EXEC: " if exec_ else "DRY : ") + f"copy_tree {src} -> {dst}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_event_window(cfg: Config, path: Path, *, exec_: bool) -> Tuple[str, str]:
    """Return (first_event_time, last_event_time) in UTC '%Y-%m-%d %H:%M:%S'.
    Fallback to file mtime in dry-run or if parsing fails.
    """
    cmd = [str(cfg.mysqlbinlog), "--base64-output=DECODE-ROWS", "--verbose", str(path)]
    print(("EXEC: " if exec_ else "DRY : ") + " ".join(shlex_quote(c) for c in cmd) + " | (parse timestamps)")
    if not exec_:
        ts = dt.datetime.utcfromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        return ts, ts

    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, bufsize=1)
    except FileNotFoundError:
        ts = dt.datetime.utcfromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        return ts, ts
    first = last = None
    assert p.stdout is not None
    for line in p.stdout:
        if "SET TIMESTAMP=" in line:
            m = re.search(r"SET TIMESTAMP=(\d+)", line)
            if m:
                epoch = int(m.group(1))
                t = dt.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
                first = first or t
                last = t
    p.wait()
    if first is None:
        ts = dt.datetime.utcfromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        first = last = ts
    return first, last


def verify_binlog_checksum(cfg: Config, path: Path, *, exec_: bool) -> bool:
    return sh([str(cfg.mysqlbinlog), "--verify-binlog-checksum", str(path)], exec_=exec_) == 0


def parse_xtrabackup_binlog_info(path: Path) -> Optional[Dict[str, str]]:
    """Parse xtrabackup_binlog_info for GTID or (file,pos)."""
    if not path.exists():
        return None
    txt = path.read_text("utf-8", errors="ignore").strip()
    if not txt:
        return None
    parts = txt.split()
    if len(parts) >= 3:
        gtid_set = " ".join(parts[2:])
        return {"mode": "gtid", "gtid_set": gtid_set}
    if len(parts) >= 2:
        return {"mode": "pos", "file": parts[0], "pos": parts[1]}
    return None

# ------------------------------
# Layout helpers (monthly series)
# ------------------------------

class Layout:
    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def series(self) -> Path:
        return self.root / "series"

    @property
    def binlogs(self) -> Path:
        return self.root / "binlogs"

    def series_dir(self, ym: str) -> Path:
        return self.series / ym

    def base_raw(self, ym: str) -> Path:
        return self.series_dir(ym) / "base" / "raw"

    def base_meta(self, ym: str) -> Path:
        return self.series_dir(ym) / "base" / "meta.json"

    def base_xb_info(self, ym: str) -> Path:
        return self.series_dir(ym) / "base" / "xtrabackup_binlog_info"

    def incr_dir(self, ym: str) -> Path:
        return self.series_dir(ym) / "incr"

    def incr_raw(self, ym: str, ts: str) -> Path:
        return self.incr_dir(ym) / ts / "raw"

    def manifest(self, ym: str) -> Path:
        return self.series_dir(ym) / "manifest.json"

    def binlog_month(self, ym: str) -> Path:
        return self.binlogs / ym

    def binlog_index(self, ym: str) -> Path:
        return self.binlog_month(ym) / "index.json"

# ------------------------------
# Commands
# ------------------------------

def cmd_init(cfg: Config) -> None:
    """Initialize directory layout and minimal files."""
    lay = Layout(cfg.nfs_root)
    ensure_dir(lay.series)
    ensure_dir(lay.binlogs)

    # bootstrap current month index
    ym = dt.datetime.utcnow().strftime("%Y-%m")
    ensure_dir(lay.series_dir(ym))
    ensure_dir(lay.binlog_month(ym))
    if not lay.binlog_index(ym).exists():
        write_json(lay.binlog_index(ym), {"files": []})
    if not lay.manifest(ym).exists():
        write_json(lay.manifest(ym), [])

    print(f"Initialized at {lay.root}")


def cmd_list(cfg: Config) -> None:
    """Quick overview of series and binlog windows."""
    lay = Layout(cfg.nfs_root)
    if not lay.series.exists():
        print("No series found.")
        return

    for sdir in sorted(lay.series.iterdir(), key=lambda p: p.name):
        if not sdir.is_dir():
            continue
        ym = sdir.name
        base_meta = lay.base_meta(ym)
        incr_root = lay.incr_dir(ym)
        incr_ts: List[str] = []
        if incr_root.exists():
            for d in incr_root.iterdir():
                if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}Z", d.name):
                    incr_ts.append(d.name)
        incr_ts.sort()

        print(f"SERIES {ym}:")
        print(f"  base: {'present' if base_meta.exists() else 'missing'}")
        if incr_ts:
            print(f"  incr: {len(incr_ts)} ({incr_ts[0]} .. {incr_ts[-1]})")
        else:
            print("  incr: 0")

        # binlog window
        index = read_json(lay.binlog_index(ym), default={"files": []})
        files = index.get("files", [])
        if files:
            first = files[0].get("first_event_time")
            last = files[-1].get("last_event_time")
            print(f"  binlogs: {len(files)} files, window {first} .. {last}")
        else:
            print("  binlogs: <empty>")


def cmd_backup_full(cfg: Config, series: Optional[str], exec_: bool) -> int:
    """Create a full physical backup into series/<YYYY-MM>/base/raw."""
    lay = Layout(cfg.nfs_root)
    ym = series or dt.datetime.utcnow().strftime("%Y-%m")
    base_raw = lay.base_raw(ym)
    ensure_dir(base_raw)

    rc = sh([
        str(cfg.xtrabackup), "--backup",
        f"--target-dir={base_raw}",
        f"--defaults-file={cfg.mysql_option_file}",
    ], exec_=exec_)
    if rc != 0:
        return rc

    meta = {"type": "full", "ts": now_utc_iso(), "series": ym}
    write_json(lay.base_meta(ym), meta)

    mani = read_json(lay.manifest(ym), default=[])
    mani.append(meta)
    write_json(lay.manifest(ym), mani)

    print(f"Full backup completed into series/{ym}/base/raw")
    return 0


def cmd_backup_incr(cfg: Config, series: Optional[str], exec_: bool) -> int:
    """Create an incremental backup against the series base."""
    lay = Layout(cfg.nfs_root)
    ym = series or dt.datetime.utcnow().strftime("%Y-%m")
    base_raw = lay.base_raw(ym)
    if not base_raw.exists():
        print(f"Base backup missing for series {ym}. Run backup-full first.")
        return 1

    ts = now_utc_iso()
    incr_raw = lay.incr_raw(ym, ts)
    ensure_dir(incr_raw)

    rc = sh([
        str(cfg.xtrabackup), "--backup", "--incremental",
        f"--incremental-basedir={base_raw}",
        f"--target-dir={incr_raw}",
        f"--defaults-file={cfg.mysql_option_file}",
    ], exec_=exec_)
    if rc != 0:
        return rc

    meta = {"type": "incr", "ts": ts, "series": ym}
    mani = read_json(lay.manifest(ym), default=[])
    mani.append(meta)
    write_json(lay.manifest(ym), mani)

    # simple per-incremental meta
    (incr_raw.parent / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    print(f"Incremental backup completed into series/{ym}/incr/{ts}/raw")
    return 0


def cmd_ship_binlogs(cfg: Config, exec_: bool, flush_first: bool) -> int:
    """Copy unseen binlogs from source to binlogs/YYYY-MM, gzip, sha256, index.json."""
    if not cfg.binlog_source_dir:
        print("binlog_source_dir not configured; nothing to ship.")
        return 0

    lay = Layout(cfg.nfs_root)
    ym = dt.datetime.utcnow().strftime("%Y-%m")
    dest_month = lay.binlog_month(ym)
    ensure_dir(dest_month)
    index_path = lay.binlog_index(ym)
    index = read_json(index_path, default={"files": []})

    # optional flush
    if flush_first:
        rc = sh([
            "mysql", f"--defaults-file={cfg.mysql_option_file}", "-e", "FLUSH BINARY LOGS"],
            exec_=exec_
        )
        if rc != 0:
            return rc

    # discover source binlog files (skip .index file)
    src_files = [p for p in sorted(cfg.binlog_source_dir.iterdir())
                 if p.is_file() and re.search(r"\.\d+$", p.name)]

    # map of existing gz names in dest
    existing = {p.name for p in dest_month.glob("*.gz")}

    for src in src_files:
        gz_name = src.name + ".gz"
        dst_gz = dest_month / gz_name

        need_copy = gz_name not in existing
        if need_copy:
            print(("EXEC: " if exec_ else "DRY : ") + f"gzip_copy {src} -> {dst_gz}")
            if exec_:
                with src.open("rb") as fsrc, gzip.open(dst_gz, "wb", compresslevel=6) as fdst:
                    shutil.copyfileobj(fsrc, fdst)

        # verify checksum on source (fast, no unzip)
        ok = verify_binlog_checksum(cfg, src, exec_=exec_)
        if not ok:
            print(f"WARN: checksum verification failed for {src}")

        # parse time window from *source* file
        first, last = parse_event_window(cfg, src, exec_=exec_)
        entry = {
            "file": gz_name,
            "size": src.stat().st_size,
            "sha256": sha256_file(dst_gz) if exec_ and dst_gz.exists() else "",
            "first_event_time": first,
            "last_event_time": last,
        }

        # upsert in index (keep sorted by file)
        files = [f for f in index.get("files", []) if f.get("file") != gz_name]
        files.append(entry)
        files.sort(key=lambda x: x.get("file"))
        index["files"] = files
        write_json(index_path, index)

    print(f"Shipped binlogs into binlogs/{ym}")
    return 0


def cmd_restore(cfg: Config, time_str: Optional[str], workdir: Optional[Path], exec_: bool) -> int:
    """Restore flow: base -> apply incrementals up to time -> finalize -> optional PITR via binlogs."""
    lay = Layout(cfg.nfs_root)

    # resolve target
    target = dt.datetime.utcnow() if not time_str else dt.datetime.strptime(time_str, "%Y-%m-%d %H:%M")
    target = floor_to_minutes(target, cfg.pitr_round_minutes)
    ym = target.strftime("%Y-%m")

    base_raw = lay.base_raw(ym)
    if not base_raw.exists():
        print(f"No base backup for series {ym} (looked at {base_raw}).")
        return 1

    # workdir
    if workdir is None:
        tmp = tempfile.mkdtemp(prefix="dbbak_restore_")
        work = Path(tmp)
    else:
        work = workdir
        ensure_dir(work)

    # seed from base raw
    copy_tree(base_raw, work, exec_=exec_)

    # apply incrementals in order up to target
    incr_root = lay.incr_dir(ym)
    incr_ts: List[str] = []
    if incr_root.exists():
        for d in incr_root.iterdir():
            if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}Z", d.name):
                if dt.datetime.strptime(d.name, ISO_Z) <= target:
                    incr_ts.append(d.name)
    incr_ts.sort()

    for ts in incr_ts:
        incr_raw = lay.incr_raw(ym, ts)
        sh([
            str(cfg.xtrabackup), "--prepare", "--apply-log-only",
            f"--target-dir={work}", f"--incremental-dir={incr_raw}"
        ], exec_=exec_)

    # finalize (redo + undo)
    sh([str(cfg.xtrabackup), "--prepare", f"--target-dir={work}"], exec_=exec_)

    # Optional PITR
    need_pitr = True
    if incr_ts:
        need_pitr = target > dt.datetime.strptime(incr_ts[-1], ISO_Z)

    if need_pitr:
        info = parse_xtrabackup_binlog_info(lay.base_xb_info(ym))
        if info is None:
            print("xtrabackup_binlog_info not found; cannot do PITR.")
        else:
            stop = target.strftime("%Y-%m-%d %H:%M:%S")
            month_dir = lay.binlog_month(ym)
            next_month_dir = lay.binlog_month((target.replace(day=1) + dt.timedelta(days=32)).strftime("%Y-%m"))
            sources: List[Path] = []
            for d in [month_dir, next_month_dir]:
                if d.exists():
                    sources.extend(sorted(d.glob("*.gz")))

            # Build a shell pipeline: gzip -cd | mysqlbinlog ... | mysql
            gz_list = " ".join(shlex_quote(str(p)) for p in sources)
            pipe = (
                f"gzip -cd {gz_list} | "
                f"{shlex_quote(str(cfg.mysqlbinlog))} --base64-output=DECODE-ROWS --verbose --stop-datetime {shlex_quote(stop)} "
            )
            if info["mode"] == "gtid":
                pipe += f" --exclude-gtids {shlex_quote(info['gtid_set'])}"
            elif info["mode"] == "pos":
                pipe += f" --start-position {shlex_quote(info['pos'])}"
            pipe += f" | mysql --defaults-file={shlex_quote(str(cfg.mysql_option_file))}"

            print(("EXEC: " if exec_ else "DRY : ") + pipe)
            if exec_:
                subprocess.call(pipe, shell=True)

    print(f"Restore assembled at {work}")
    return 0

# ------------------------------
# Argparse wiring
# ------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mysql-backup-cli-stdlib",
        description="MySQL backup CLI: full + incremental + PITR (stdlib-only)",
    )
    p.add_argument("--config", dest="config_path", default=str(DEFAULT_CONFIG_PATH), help="Path to JSON config")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Initialize directory layout and minimal files")
    sub.add_parser("list", help="Overview of series and binlog windows")

    p_full = sub.add_parser("backup-full", help="Create a full physical backup")
    p_full.add_argument("--series", default=None, help="Series (YYYY-MM). Default=current UTC month")
    p_full.add_argument("--exec", dest="exec_", action="store_true", help="Actually execute xtrabackup")

    p_incr = sub.add_parser("backup-incr", help="Create an incremental backup against the series base")
    p_incr.add_argument("--series", default=None, help="Series (YYYY-MM). Default=current UTC month")
    p_incr.add_argument("--exec", dest="exec_", action="store_true", help="Actually execute xtrabackup")

    p_ship = sub.add_parser("ship-binlogs", help="Copy unseen binlogs, gzip, sha256, update index.json")
    p_ship.add_argument("--exec", dest="exec_", action="store_true", help="Actually copy + gzip binlogs")
    p_ship.add_argument("--flush-first", action="store_true", help="Run FLUSH BINARY LOGS before shipping")

    p_restore = sub.add_parser("restore", help="Restore and optional PITR")
    p_restore.add_argument("--time", dest="time_str", default=None, help='Target time "YYYY-MM-DD HH:MM"; default=now')
    p_restore.add_argument("--workdir", default=None, help="Restore working datadir (created if missing)")
    p_restore.add_argument("--exec", dest="exec_", action="store_true", help="Run prepare + mysqlbinlog")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = Config.load(Path(args.config_path).expanduser())

    if args.cmd == "init":
        cmd_init(cfg); return 0
    if args.cmd == "list":
        cmd_list(cfg); return 0
    if args.cmd == "backup-full":
        return cmd_backup_full(cfg, args.series, args.exec_)
    if args.cmd == "backup-incr":
        return cmd_backup_incr(cfg, args.series, args.exec_)
    if args.cmd == "ship-binlogs":
        return cmd_ship_binlogs(cfg, args.exec_, args.flush_first)
    if args.cmd == "restore":
        workdir = Path(args.workdir).expanduser() if args.workdir else None
        return cmd_restore(cfg, args.time_str, workdir, args.exec_)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
