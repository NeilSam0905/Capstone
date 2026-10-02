"""
backup.py - copies of ustore.db, so a deleted or damaged database file does
not take every tally entry, stock count, closure and event with it.

Everything staff enter lives in that one file. The only other copy used to be
the encrypted vault, which is only as recent as the last `vault.py lock` plus a
git commit - days or weeks, depending on who last remembered.

    python backend/backup.py             take a backup now
    python backend/backup.py --list      list the backups
    python backend/backup.py --restore backups/ustore-20261002-230000.db

serve.py (the daily-use server) also takes one a day on its own; see
BackupSchedule.

Copies are made with SQLite's online backup API, which is safe while the
backend is running and writing - a plain file copy of a database in WAL mode
can miss everything still in the -wal file. Each copy is checked with
`PRAGMA integrity_check` before it is kept. The last KEEP copies are kept, in
backups/ at the repo root (gitignored: plain store data, like ustore.db).

A restore saves the current database as a backup first, so a restore can be
undone the same way.
"""
import argparse
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "ustore.db"
BACKUP_DIR = ROOT / "backups"
KEEP = 14
PREFIX = "ustore-"


class BackupError(Exception):
    pass


def _copy(src_path, dst_path):
    """SQLite online backup of src into a new file at dst, then an integrity
    check of the copy. The copy is written under a temporary name and only
    renamed into place once it checks out."""
    tmp = dst_path.with_name(dst_path.name + ".partial")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(src_path, timeout=30)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    if result != "ok":
        tmp.unlink(missing_ok=True)
        raise BackupError(f"the copy failed its integrity check ({result}); nothing was kept")
    tmp.replace(dst_path)
    return dst_path


def backups(backup_dir=BACKUP_DIR):
    """Existing backups, newest first."""
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob(f"{PREFIX}*.db"), reverse=True)


def take(db_path=DB_PATH, backup_dir=BACKUP_DIR, keep=KEEP, label=""):
    """Back up db_path now and prune to the newest `keep`. Returns the new file."""
    if not Path(db_path).exists():
        raise BackupError(f"{db_path} does not exist - nothing to back up")
    backup_dir.mkdir(parents=True, exist_ok=True)
    name = f"{PREFIX}{datetime.now().strftime('%Y%m%d-%H%M%S')}{('-' + label) if label else ''}.db"
    path = _copy(Path(db_path), backup_dir / name)
    for old in backups(backup_dir)[keep:]:
        old.unlink(missing_ok=True)
    return path


def restore(backup_path, db_path=DB_PATH, backup_dir=BACKUP_DIR):
    """Replace the database's contents with a backup's, after backing up the
    current database (labelled "before-restore"). Returns that safety copy."""
    backup_path = Path(backup_path)
    if not backup_path.exists():
        raise BackupError(f"{backup_path} does not exist")
    check = sqlite3.connect(backup_path)
    try:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError(f"{backup_path} failed its integrity check - not restored")
    finally:
        check.close()
    safety = take(db_path, backup_dir, keep=KEEP + 1, label="before-restore") if Path(db_path).exists() else None
    src = sqlite3.connect(backup_path)
    try:
        dst = sqlite3.connect(db_path, timeout=30)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return safety


class BackupSchedule:
    """Takes a backup once a day while the server runs: at start-up if the
    newest backup is more than a day old (or there is none), then every
    `interval` seconds. A failure is logged and retried at the next interval;
    it never stops the server."""

    def __init__(self, interval=24 * 3600, log=print, db_path=DB_PATH, backup_dir=BACKUP_DIR):
        self.interval, self.log = interval, log
        self.db_path, self.backup_dir = db_path, backup_dir
        self._stop = threading.Event()

    def _due(self):
        newest = backups(self.backup_dir)
        return not newest or time.time() - newest[0].stat().st_mtime >= self.interval

    def _run(self):
        while not self._stop.is_set():
            if self._due():
                try:
                    self.log(f"backup: saved {take(self.db_path, self.backup_dir).name}")
                except (BackupError, sqlite3.Error, OSError) as exc:
                    self.log(f"backup: FAILED - {exc}")
            self._stop.wait(min(self.interval, 3600))     # re-check hourly

    def start(self):
        threading.Thread(target=self._run, name="ustore-backup", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()


def main():
    ap = argparse.ArgumentParser(description="Back up or restore ustore.db.")
    ap.add_argument("--list", action="store_true", help="list the backups")
    ap.add_argument("--restore", metavar="FILE", help="restore this backup (the current database is backed up first)")
    args = ap.parse_args()
    try:
        if args.list:
            for b in backups():
                print(f"{b.name}  {b.stat().st_size / 1e6:.1f} MB")
            return
        if args.restore:
            safety = restore(args.restore)
            print(f"Restored {args.restore}." + (f" The database before it was saved as {safety.name}." if safety else ""))
            return
        print(f"Saved {take()}")
    except BackupError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
