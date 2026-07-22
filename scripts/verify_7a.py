"""
verify_7a.py — Thorough verification of SQLite hardening (Update 7A).

Executes real checks against the live database:
  1. PRAGMA journal_mode + foreign_keys on live engine connection
  2. FK enforcement: insert child row with bogus lead_id → IntegrityError
  3. Concurrency: 20 concurrent insert_lead_if_new tasks, all succeed, all rows exist
  4. Backup with in-progress WAL: write row, don't checkpoint, backup, verify
  5. Retention cleanup: 15 old + 2 recent fake backups, only 2 survive
"""

import asyncio
import os
import sys
import sqlite3
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

passed_count = 0
failed_count = 0


def report(name, ok, detail=""):
    global passed_count, failed_count
    tag = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed_count += 1
    else:
        failed_count += 1
    print(f"  {tag} {name}")
    if detail:
        for line in detail.strip().splitlines():
            print(f"        {line}")
    print()


# ─────────────────────────────────────────────────────────────
# 1. PRAGMA verification on live engine connection
# ─────────────────────────────────────────────────────────────
async def test_1_pragmas():
    from sqlalchemy import text
    from app.core.db import engine

    async with engine.connect() as conn:
        jm = (await conn.execute(text("PRAGMA journal_mode;"))).scalar()
        fk = (await conn.execute(text("PRAGMA foreign_keys;"))).scalar()
        sync = (await conn.execute(text("PRAGMA synchronous;"))).scalar()
        bt = (await conn.execute(text("PRAGMA busy_timeout;"))).scalar()

    jm_ok = str(jm).lower() == "wal"
    fk_ok = int(fk) == 1
    ok = jm_ok and fk_ok

    report(
        "1. PRAGMAs on live engine connection",
        ok,
        f"journal_mode={jm!r} ({'OK' if jm_ok else 'EXPECTED wal'})\n"
        f"foreign_keys={fk!r} ({'OK' if fk_ok else 'EXPECTED 1'})\n"
        f"synchronous={sync!r}\n"
        f"busy_timeout={bt!r}",
    )


# ─────────────────────────────────────────────────────────────
# 2. FK enforcement: insert child with non-existent parent
# ─────────────────────────────────────────────────────────────
async def test_2_fk_enforcement():
    from app.core.db import get_session
    from app.core.models import OutreachSequence

    got_integrity_error = False
    exc_detail = ""

    try:
        async with get_session() as session:
            bad = OutreachSequence(
                lead_id=999999,  # does not exist
                sequence_type="test_fk_verify",
                subject="FK test",
                body="FK test body",
                status="draft",
            )
            session.add(bad)
            await session.flush()  # flush forces the INSERT immediately
            await session.commit()
    except Exception as exc:
        # Walk the exception chain
        check = exc
        while check is not None:
            tn = type(check).__name__
            es = str(check)
            if "integrity" in tn.lower() or "FOREIGN KEY" in es.upper():
                got_integrity_error = True
                exc_detail = f"{tn}: {es[:200]}"
                break
            prev = check
            check = getattr(check, "__cause__", None) or getattr(check, "__context__", None)
            if check is prev:
                break

        if not got_integrity_error:
            exc_detail = f"Got exception but not FK: {type(exc).__name__}: {str(exc)[:200]}"

    report(
        "2. FK enforcement (insert with bogus lead_id)",
        got_integrity_error,
        exc_detail if got_integrity_error else "Insert was silently accepted — FK enforcement NOT working!",
    )


# ─────────────────────────────────────────────────────────────
# 3. Concurrency stress test: 20 concurrent inserts
# ─────────────────────────────────────────────────────────────
async def test_3_concurrency():
    from app.core.db import get_session, engine
    from app.modules.hunter.dedup import insert_lead_if_new
    from sqlalchemy import text, select
    from app.core.models import Lead

    ts = int(time.time() * 1000)
    test_source = f"concurrency_verify_{ts}"
    domains = [f"stress-{i}-{ts}.example.com" for i in range(20)]

    lock_errors = []
    other_errors = []
    successes = []

    async def insert_one(i):
        try:
            async with get_session() as session:
                raw = {
                    "company_name": f"StressTest{i}",
                    "website": f"https://{domains[i]}",
                    "source": test_source,
                }
                result = await insert_lead_if_new(session, raw)
                await session.commit()
                successes.append(i)
        except Exception as exc:
            es = str(exc).lower()
            if "database is locked" in es:
                lock_errors.append(f"Task {i}: {exc}")
            else:
                other_errors.append(f"Task {i}: {type(exc).__name__}: {str(exc)[:150]}")

    tasks = [asyncio.create_task(insert_one(i)) for i in range(20)]
    await asyncio.gather(*tasks)

    # Verify all 20 rows actually exist
    async with get_session() as session:
        result = await session.execute(
            select(Lead).where(Lead.source == test_source)
        )
        rows = result.scalars().all()
        row_count = len(rows)
        inserted_domains = {r.domain for r in rows}

    all_present = row_count == 20
    zero_lock = len(lock_errors) == 0
    ok = zero_lock and all_present

    detail_lines = [
        f"Successes: {len(successes)}/20",
        f"Lock errors: {len(lock_errors)}",
        f"Other errors: {len(other_errors)}",
        f"Rows in DB: {row_count}/20",
    ]
    if lock_errors:
        detail_lines.append(f"Lock error samples: {lock_errors[:3]}")
    if other_errors:
        detail_lines.append(f"Other error samples: {other_errors[:3]}")
    if not all_present:
        missing = set(domains) - inserted_domains
        detail_lines.append(f"Missing domains: {list(missing)[:5]}")

    report("3. Concurrency (20 concurrent inserts)", ok, "\n".join(detail_lines))

    # Cleanup
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DELETE FROM leads WHERE source = '{test_source}'"))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 4. Backup with in-progress WAL
# ─────────────────────────────────────────────────────────────
async def test_4_backup_with_wal():
    from app.core.db import get_session, engine
    from app.core.models import Lead, ActivityLog
    from sqlalchemy import text
    from scripts.backup_db import run_backup

    ts = int(time.time() * 1000)
    marker_domain = f"wal-test-{ts}.example.com"
    marker_source = f"wal_backup_test_{ts}"

    # Write a row WITHOUT checkpointing — leaves data in WAL
    async with get_session() as session:
        session.add(Lead(
            company_name="WAL Backup Test",
            domain=marker_domain,
            source=marker_source,
            status="RAW",
        ))
        await session.commit()

    # Do NOT run wal_checkpoint — data is still in the WAL file

    # Backup immediately
    backup_path = run_backup()

    # Open backup independently and verify the row is there
    ok = False
    detail = ""
    try:
        conn = sqlite3.connect(backup_path)
        cursor = conn.execute(
            "SELECT company_name, domain, source FROM leads WHERE domain = ?",
            (marker_domain,),
        )
        row = cursor.fetchone()

        # Also run a general integrity check
        integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
        table_count = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        conn.close()

        if row and row[1] == marker_domain:
            ok = True
            detail = (
                f"Backup file: {backup_path}\n"
                f"Found marker row: company={row[0]!r}, domain={row[1]!r}, source={row[2]!r}\n"
                f"integrity_check={integrity!r}, tables={table_count}"
            )
        else:
            detail = f"Marker row NOT found in backup (domain={marker_domain})"
    except Exception as exc:
        detail = f"Failed to read backup: {type(exc).__name__}: {exc}"

    report("4. Backup with in-progress WAL", ok, detail)

    # Cleanup
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DELETE FROM leads WHERE source = '{marker_source}'"))
        Path(backup_path).unlink(missing_ok=True)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 5. Retention cleanup: 15 old + 2 recent, only 2 survive
# ─────────────────────────────────────────────────────────────
def test_5_retention():
    from scripts.backup_db import cleanup_old_backups

    test_dir = Path("backups") / "_retention_test"
    test_dir.mkdir(parents=True, exist_ok=True)

    # Create 15 fake old backups (mtime 30 days ago)
    old_files = []
    for i in range(15):
        p = test_dir / f"contractor_os_2020010{i:02d}_000000.db"
        p.write_text(f"fake old {i}")
        old_time = time.time() - (30 * 86400)
        os.utime(str(p), (old_time, old_time))
        old_files.append(p)

    # Create 2 recent backups (mtime now)
    recent_files = []
    for i in range(2):
        p = test_dir / f"contractor_os_2099010{i}_000000.db"
        p.write_text(f"fake recent {i}")
        recent_files.append(p)

    # Run cleanup
    cleanup_old_backups(test_dir, retention_days=14)

    # Check results
    old_gone = sum(1 for f in old_files if not f.exists())
    recent_kept = sum(1 for f in recent_files if f.exists())

    ok = old_gone == 15 and recent_kept == 2

    report(
        "5. Retention cleanup (15 old + 2 recent)",
        ok,
        f"Old deleted: {old_gone}/15\nRecent kept: {recent_kept}/2",
    )

    # Cleanup test dir
    for f in test_dir.glob("*"):
        f.unlink()
    test_dir.rmdir()


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
async def main():
    print()
    print("=" * 64)
    print("  ContractorOS -- SQLite Hardening Verification (7A)")
    print("=" * 64)
    print()

    await test_1_pragmas()
    await test_2_fk_enforcement()
    await test_3_concurrency()
    await test_4_backup_with_wal()
    test_5_retention()

    print("-" * 64)
    total = passed_count + failed_count
    print(f"  Results: {passed_count}/{total} passed, {failed_count} failed")
    if failed_count == 0:
        print("  All verification checks passed!")
    else:
        print("  Some checks FAILED -- review above.")
    print("-" * 64)
    print()

    return failed_count == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
