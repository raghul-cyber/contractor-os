"""
verify_sqlite_hardening.py — Acceptance criteria verification.

Checks all five hardening criteria:
  1. PRAGMA journal_mode=WAL active
  2. PRAGMA foreign_keys=ON enforced (FK violation actually raises)
  3. Concurrent writes: 20 simultaneous inserts, zero "database is locked"
  4. Backup produces a valid, queryable database file
  5. Old backups beyond 14 days are cleaned up

Usage:
    cd ContractorOS
    python scripts/verify_sqlite_hardening.py
"""

import asyncio
import os
import sys
import sqlite3
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PASS = "[PASS]"
FAIL = "[FAIL]"
results = []


def report(name, passed, detail=""):
    status = PASS if passed else FAIL
    results.append((name, passed))
    print(f"  {status} {name}")
    if detail:
        print(f"      {detail}")


async def test_wal_mode():
    """1. PRAGMA journal_mode=WAL confirmed active."""
    from app.core.db import verify_pragmas
    pragmas = await verify_pragmas()
    journal = pragmas["journal_mode"]
    passed = str(journal).lower() == "wal"
    report(
        "WAL mode active",
        passed,
        f"journal_mode={journal!r}, synchronous={pragmas['synchronous']}, "
        f"busy_timeout={pragmas['busy_timeout']}, foreign_keys={pragmas['foreign_keys']}"
    )


async def test_foreign_keys():
    """2. FK violation raises a real error, not silent success."""
    from app.core.db import get_session
    from app.core.models import OutreachSequence

    fk_error_raised = False
    try:
        async with get_session() as session:
            # lead_id=999999 doesn't exist — should raise FK violation
            bad_seq = OutreachSequence(
                lead_id=999999,
                sequence_type="test_fk_check",
                subject="Test",
                body="Test",
                status="draft",
            )
            session.add(bad_seq)
            await session.commit()
    except Exception as exc:
        # Walk the exception chain looking for IntegrityError or FOREIGN KEY
        check = exc
        while check is not None:
            exc_str = str(check).upper()
            if "FOREIGN KEY" in exc_str or "INTEGRITY" in type(check).__name__.upper():
                fk_error_raised = True
                break
            check = getattr(check, "__cause__", None) or getattr(check, "__context__", None)
            if check is exc:
                break

    report(
        "Foreign key enforcement",
        fk_error_raised,
        "FK violation correctly raised" if fk_error_raised else "FK violation was SILENTLY accepted — foreign_keys pragma not working!"
    )


async def test_concurrent_writes():
    """3. 20 concurrent async inserts with zero 'database is locked' errors."""
    from app.core.db import get_session
    from app.modules.hunter.dedup import insert_lead_if_new

    errors = []
    lock_errors = []

    async def insert_one(i):
        try:
            async with get_session() as session:
                raw = {
                    "company_name": f"ConcurrentTest{i}",
                    "website": f"https://concurrent-test-{i}-{int(time.time())}.com",
                    "source": "concurrency_test",
                }
                await insert_lead_if_new(session, raw)
                await session.commit()
        except Exception as exc:
            exc_str = str(exc)
            if "database is locked" in exc_str.lower():
                lock_errors.append(exc_str)
            else:
                errors.append(exc_str)

    # Fire 20 concurrent tasks
    tasks = [asyncio.create_task(insert_one(i)) for i in range(20)]
    await asyncio.gather(*tasks)

    passed = len(lock_errors) == 0
    detail = f"20 concurrent inserts: {len(lock_errors)} lock errors, {len(errors)} other errors"
    if lock_errors:
        detail += f"\n      Lock errors: {lock_errors[:3]}"
    report("Concurrent writes (20 tasks)", passed, detail)

    # Cleanup test data
    try:
        from sqlalchemy import text
        from app.core.db import engine
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM leads WHERE source = 'concurrency_test'"))
    except Exception:
        pass  # best-effort cleanup


def test_backup_validity():
    """4. backup_db produces a valid, openable backup file."""
    from scripts.backup_db import run_backup

    backup_path = run_backup()
    exists = Path(backup_path).exists()

    if not exists:
        report("Backup validity", False, f"Backup file not created at {backup_path}")
        return

    # Verify by opening with sqlite3 and running a query
    valid = False
    detail = ""
    try:
        conn = sqlite3.connect(backup_path)
        cursor = conn.execute("SELECT count(*) FROM leads;")
        count = cursor.fetchone()[0]
        conn.close()
        valid = True
        detail = f"Backup at {backup_path} — leads table has {count} rows"
    except Exception as exc:
        detail = f"Failed to query backup: {exc}"

    report("Backup validity", valid, detail)

    # Cleanup the test backup
    try:
        Path(backup_path).unlink()
    except Exception:
        pass


def test_backup_retention():
    """5. Old backups beyond 14 days are actually cleaned up."""
    from scripts.backup_db import cleanup_old_backups, BACKUP_DIR

    BACKUP_DIR.mkdir(exist_ok=True)

    # Create fake old backup files with mtime > 14 days ago
    old_files = []
    for i in range(3):
        fake_name = f"contractor_os_20200101_00000{i}.db"
        fake_path = BACKUP_DIR / fake_name
        fake_path.write_text("fake")
        # Set mtime to 30 days ago
        old_time = time.time() - (30 * 86400)
        os.utime(str(fake_path), (old_time, old_time))
        old_files.append(fake_path)

    # Also create a "recent" fake backup that should NOT be deleted
    recent_name = "contractor_os_20990101_000000.db"
    recent_path = BACKUP_DIR / recent_name
    recent_path.write_text("recent")

    # Run cleanup
    cleanup_old_backups(BACKUP_DIR, retention_days=14)

    # Verify old files deleted
    old_deleted = all(not f.exists() for f in old_files)
    recent_kept = recent_path.exists()
    passed = old_deleted and recent_kept

    detail = f"Old files deleted: {old_deleted}, Recent file kept: {recent_kept}"
    report("Backup retention (14-day)", passed, detail)

    # Cleanup
    if recent_path.exists():
        recent_path.unlink()


async def main():
    print()
    print("=" * 60)
    print("  ContractorOS — SQLite Hardening Verification")
    print("=" * 60)
    print()

    await test_wal_mode()
    await test_foreign_keys()
    await test_concurrent_writes()
    test_backup_validity()
    test_backup_retention()

    print()
    print("-" * 60)
    passed = sum(1 for _, p in results if p)
    total = len(results)
    print(f"  Results: {passed}/{total} passed")
    if passed == total:
        print(f"  {PASS} All acceptance criteria met!")
    else:
        print(f"  {FAIL} Some criteria failed — review above.")
    print("-" * 60)
    print()

    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
