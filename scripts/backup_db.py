"""
backup_db.py — Online SQLite backup with 14-day retention.

Uses sqlite3's .backup() API (the official online backup mechanism) rather
than filesystem copy. This is safe even while the DB is open and being
written to under WAL mode.

Usage:
    python scripts/backup_db.py          # run manually
    # Also runs daily via APScheduler (daily_backup job, 02:00)

Backups are placed in backups/ (gitignored) with timestamped filenames.
After creating the backup, old backups beyond 14 days are deleted.
"""

import os
import sys
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

# Allow running standalone from the project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# --- Configuration ---
DB_PATH = os.path.join("data", "contractor_os.db")
BACKUP_DIR = Path("backups")
RETENTION_DAYS = 14


def run_backup(db_path: str = DB_PATH, backup_dir: Path = BACKUP_DIR) -> str:
    """
    Perform an online backup of the SQLite database.

    Returns the path to the created backup file.
    """
    backup_dir.mkdir(exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"contractor_os_{timestamp}.db"
    backup_path = backup_dir / backup_filename

    # 1. Online backup via sqlite3's .backup() — safe under WAL
    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(str(backup_path))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    # 2. Checkpoint the *backup* so it's fully self-contained (no -wal/-shm needed)
    checkpoint_conn = sqlite3.connect(str(backup_path))
    try:
        checkpoint_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        checkpoint_conn.close()

    # Remove any sidecar files the checkpoint might have left
    for suffix in ["-wal", "-shm"]:
        sidecar = Path(str(backup_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()

    size_mb = backup_path.stat().st_size / (1024 * 1024)
    print(f"[backup] Created: {backup_path} ({size_mb:.2f} MB)")

    # 3. Retention cleanup — delete backups older than RETENTION_DAYS
    cleanup_old_backups(backup_dir)

    return str(backup_path)


def cleanup_old_backups(backup_dir: Path = BACKUP_DIR, retention_days: int = RETENTION_DAYS):
    """Delete backup files older than retention_days based on file mtime."""
    cutoff = time.time() - (retention_days * 86400)
    deleted = 0

    for f in backup_dir.glob("contractor_os_*.db"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            print(f"[backup] Deleted old backup: {f.name}")
            deleted += 1

    if deleted:
        print(f"[backup] Cleaned up {deleted} old backup(s).")
    else:
        print(f"[backup] No backups older than {retention_days} days to clean up.")


if __name__ == "__main__":
    run_backup()
