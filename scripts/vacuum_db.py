"""
vacuum_db.py — Manual database maintenance script.

Runs VACUUM and PRAGMA optimize on the ContractorOS database.

*** WARNING: Run this ONLY when the server is STOPPED. ***

VACUUM rebuilds the entire database file, reclaiming unused space and
defragmenting. It requires exclusive access to the database — running it
while the FastAPI server or APScheduler are active will block all writes
for the entire duration and may cause jobs to fail.

PRAGMA optimize runs lightweight analysis to update internal statistics
that help SQLite's query planner make better decisions.

Recommended: run manually during a maintenance window, roughly once a week.

Usage:
    # Stop the server first!
    python scripts/vacuum_db.py
"""

import os
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DB_PATH = os.path.join("data", "contractor_os.db")


def run_vacuum(db_path: str = DB_PATH):
    """Run VACUUM and PRAGMA optimize on the database."""
    if not Path(db_path).exists():
        print(f"[vacuum] Database not found: {db_path}")
        sys.exit(1)

    size_before = Path(db_path).stat().st_size

    print("=" * 60)
    print("  ContractorOS — Database Maintenance")
    print("=" * 60)
    print()
    print("  WARNING: Ensure the server is STOPPED before running this.")
    print("  VACUUM requires exclusive access to the database.")
    print()

    conn = sqlite3.connect(db_path)
    try:
        print("[vacuum] Running VACUUM... (this may take a moment)")
        conn.execute("VACUUM;")
        print("[vacuum] VACUUM complete.")

        print("[vacuum] Running PRAGMA optimize...")
        conn.execute("PRAGMA optimize;")
        print("[vacuum] PRAGMA optimize complete.")
    finally:
        conn.close()

    size_after = Path(db_path).stat().st_size
    saved = size_before - size_after
    print()
    print(f"  Before: {size_before / 1024:.1f} KB")
    print(f"  After:  {size_after / 1024:.1f} KB")
    print(f"  Saved:  {saved / 1024:.1f} KB")
    print()
    print("  Done. You can restart the server now.")


if __name__ == "__main__":
    run_vacuum()
