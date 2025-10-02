#!/usr/bin/env python3
"""
beancart_backup.py
------------------
Simple CLI tool for managing MySQL backups (InnoDB-only) with XtraBackup.

Supports:
- Full backups
- Incremental backups
- Restore prepare
- Restore copy-back
- Apply binlogs for PITR
- Verify markers

Author: NamDP exercise
"""

import argparse
import subprocess
import sys
import os
import logging
from datetime import datetime

# -------------------------
# Config (adjust as needed)
# -------------------------
MYSQL_USER = "xbk"
MYSQL_PASSWORD = "yourpassword"   # recommend moving to my.cnf or env
ENCRYPT_KEY_FILE = "/root/xbk.key"

BACKUP_ROOT = "/mnt/mysql-backup/mysql_prod"
BASE_DIR = os.path.join(BACKUP_ROOT, "base")
INCR_DIR = os.path.join(BACKUP_ROOT, "incr")
BINLOG_DIR = os.path.join(BACKUP_ROOT, "binlog")
DATADIR = "/var/lib/mysql"

# xtrabackup performance knobs
PARALLEL = "4"
THROTTLE = "100"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# -------------------------
# Helpers
# -------------------------
def run_cmd(cmd, check=True):
    """Run a shell command safely with logging."""
    logging.info("Running: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=check)
    except subprocess.CalledProcessError as e:
        logging.error("Command failed: %s", e)
        sys.exit(1)


def timestamp():
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")

# -------------------------
# Backup commands
# -------------------------
def backup_full(args):
    outdir = os.path.join(BASE_DIR, timestamp())
    os.makedirs(outdir, exist_ok=True)
    cmd = [
        "xtrabackup", "--backup",
        f"--user={MYSQL_USER}", f"--password={MYSQL_PASSWORD}",
        f"--target-dir={outdir}",
        "--encrypt=AES256", f"--encrypt-key-file={ENCRYPT_KEY_FILE}",
        f"--parallel={PARALLEL}", f"--throttle={THROTTLE}"
    ]
    run_cmd(cmd)
    logging.info("Full backup complete: %s", outdir)


def backup_incr(args):
    if not args.base:
        logging.error("You must specify --base (last backup directory)")
        sys.exit(1)
    outdir = os.path.join(INCR_DIR, timestamp())
    os.makedirs(outdir, exist_ok=True)
    cmd = [
        "xtrabackup", "--backup",
        f"--user={MYSQL_USER}", f"--password={MYSQL_PASSWORD}",
        f"--target-dir={outdir}",
        f"--incremental-basedir={args.base}",
        "--encrypt=AES256", f"--encrypt-key-file={ENCRYPT_KEY_FILE}",
        f"--parallel={PARALLEL}", f"--throttle={THROTTLE}"
    ]
    run_cmd(cmd)
    logging.info("Incremental backup complete: %s", outdir)

# -------------------------
# Restore commands
# -------------------------
def restore_prepare(args):
    if not args.base:
        logging.error("You must specify --base (backup dir to prepare)")
        sys.exit(1)
    cmd = [
        "xtrabackup", "--prepare",
        "--apply-log-only",
        f"--target-dir={args.base}",
        f"--encrypt-key-file={ENCRYPT_KEY_FILE}"
    ]
    run_cmd(cmd)
    logging.info("Prepare (redo-only) done")

    # final apply
    cmd_final = [
        "xtrabackup", "--prepare",
        f"--target-dir={args.base}",
        f"--encrypt-key-file={ENCRYPT_KEY_FILE}"
    ]
    run_cmd(cmd_final)
    logging.info("Final prepare complete")


def restore_copyback(args):
    if not args.base:
        logging.error("You must specify --base (backup dir to copy)")
        sys.exit(1)
    logging.warning("This will overwrite %s. Ensure MySQL is stopped!", DATADIR)
    cmd = [
        "xtrabackup", "--copy-back",
        f"--target-dir={args.base}",
        f"--encrypt-key-file={ENCRYPT_KEY_FILE}"
    ]
    run_cmd(cmd)
    run_cmd(["chown", "-R", "mysql:mysql", DATADIR])
    logging.info("Copy-back complete. Datadir restored.")


def restore_apply_binlog(args):
    if not args.stop_time:
        logging.error("You must provide --stop-time (YYYY-MM-DD HH:MM:SS)")
        sys.exit(1)
    cmd = [
        "mysqlbinlog",
        f"--stop-datetime={args.stop_time}",
        os.path.join(BINLOG_DIR, "mysql-bin.*")
    ]
    # Pipe into mysql
    p1 = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["mysql", f"--user={MYSQL_USER}", f"--password={MYSQL_PASSWORD}"], stdin=p1.stdout)
    p1.stdout.close()
    p2.communicate()
    logging.info("Binlogs applied up to %s", args.stop_time)


def restore_verify(args):
    queries = [
        "SELECT * FROM beancart.markers ORDER BY ts DESC LIMIT 5;",
        "SELECT COUNT(*) FROM beancart.orders;",
        "SELECT status, COUNT(*) FROM beancart.orders GROUP BY 1;"
    ]
    for q in queries:
        run_cmd(["mysql", f"--user={MYSQL_USER}", f"--password={MYSQL_PASSWORD}", "-e", q], check=False)

# -------------------------
# CLI parser
# -------------------------
def main():
    parser = argparse.ArgumentParser(description="BeanCart MySQL Backup Tool (InnoDB only)")
    subparsers = parser.add_subparsers(dest="command")

    # backup full
    sp_full = subparsers.add_parser("backup-full", help="Run a full backup")
    sp_full.set_defaults(func=backup_full)

    # backup incr
    sp_incr = subparsers.add_parser("backup-incr", help="Run an incremental backup")
    sp_incr.add_argument("--base", required=True, help="Base dir (full or incr) for incremental")
    sp_incr.set_defaults(func=backup_incr)

    # restore prepare
    sp_prep = subparsers.add_parser("restore-prepare", help="Prepare a backup set (apply logs)")
    sp_prep.add_argument("--base", required=True, help="Backup dir to prepare")
    sp_prep.set_defaults(func=restore_prepare)

    # restore copyback
    sp_copy = subparsers.add_parser("restore-copyback", help="Copy back prepared backup into datadir")
    sp_copy.add_argument("--base", required=True, help="Backup dir to restore from")
    sp_copy.set_defaults(func=restore_copyback)

    # restore apply-binlog
    sp_bin = subparsers.add_parser("restore-apply-binlog", help="Apply binlogs up to stop time")
    sp_bin.add_argument("--stop-time", required=True, help="Stop time (YYYY-MM-DD HH:MM:SS)")
    sp_bin.set_defaults(func=restore_apply_binlog)

    # restore verify
    sp_verify = subparsers.add_parser("restore-verify", help="Run SQL verification queries")
    sp_verify.set_defaults(func=restore_verify)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()

