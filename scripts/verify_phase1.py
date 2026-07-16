import os
import sys
import time
import json
import yaml
import sqlite3
import subprocess

def main():
    # 1. Delete data/contractor_os.db if it exists
    db_path = "data/contractor_os.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    print("Check 1 & 2: Running init_db.py twice...")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath(".")
    
    res1 = subprocess.run([sys.executable, "scripts/init_db.py"], capture_output=True, text=True, env=env)
    if res1.returncode != 0:
        print(f"init_db.py failed on first run:\n{res1.stderr}")
        sys.exit(1)

    res2 = subprocess.run([sys.executable, "scripts/init_db.py"], capture_output=True, text=True, env=env)
    if res2.returncode != 0:
        print(f"init_db.py failed on second run (idempotency):\n{res2.stderr}")
        sys.exit(1)

    print("PASS: init_db.py is stable and idempotent.")

    # 3. Check SQLite for tables
    print("Check 3: Inspecting sqlite file for 7 tables...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    required_tables = {"leads", "outreach_sequences", "email_events", "pipeline", "activity_log", "llm_calls", "runs"}
    missing = required_tables - tables
    if missing:
        print(f"Missing tables: {missing}")
        sys.exit(1)
    print("PASS: All 7 required tables exist.")

    # 4. Check `leads` table for columns, nullability, and unique constraint
    print("Check 4: Verifying 'leads' table schema and constraints...")
    cur.execute("PRAGMA table_info(leads)")
    columns = {row[1]: {'notnull': row[3], 'pk': row[5]} for row in cur.fetchall()}
    required_cols = {
        "id": {"notnull": 1, "pk": 1},
        "company_name": {"notnull": 1, "pk": 0},
        "domain": {"notnull": 1, "pk": 0},
        "website": {"notnull": 0, "pk": 0},
        "email": {"notnull": 0, "pk": 0},
        "phone": {"notnull": 0, "pk": 0},
        "industry": {"notnull": 0, "pk": 0},
        "location": {"notnull": 0, "pk": 0},
        "size_range": {"notnull": 0, "pk": 0},
        "source": {"notnull": 1, "pk": 0},
        "status": {"notnull": 1, "pk": 0},
        "profile_json": {"notnull": 0, "pk": 0},
        "decision_maker_name": {"notnull": 0, "pk": 0},
        "decision_maker_email": {"notnull": 0, "pk": 0},
        "decision_maker_title": {"notnull": 0, "pk": 0},
        "fit_score": {"notnull": 0, "pk": 0},
        "created_at": {"notnull": 1, "pk": 0},
        "updated_at": {"notnull": 1, "pk": 0},
    }
    for col, rules in required_cols.items():
        if col not in columns:
            print(f"Missing column {col} in leads")
            sys.exit(1)
        if columns[col]['notnull'] != rules['notnull']:
            print(f"Column {col} nullability mismatch: expected NOT NULL={rules['notnull']} got {columns[col]['notnull']}")
            sys.exit(1)
        if columns[col]['pk'] != rules['pk']:
            print(f"Column {col} pk mismatch")
            sys.exit(1)

    # Unique constraint check on domain
    try:
        cur.execute("INSERT INTO leads (company_name, domain, source) VALUES ('Acme', 'acme.com', 'manual_csv')")
        conn.commit()
        cur.execute("INSERT INTO leads (company_name, domain, source) VALUES ('Acme2', 'acme.com', 'manual_csv')")
        conn.commit()
        print("FAILED: Inserted two rows with same domain successfully!")
        sys.exit(1)
    except sqlite3.IntegrityError:
        conn.rollback()
    print("PASS: leads table schema and unique constraint verified.")

    # 5. Config watcher script
    print("Check 5: Testing config watcher hot-reload...")
    from app.core.config_loader import get_config, start_config_watcher
    start_config_watcher()
    cfg = get_config()
    old_batch = cfg.system.batch_size
    new_batch = 50 if old_batch == 10 else 10

    # Wait for the watcher to initialize before making changes
    time.sleep(2)

    with open("config/system.yaml", "r") as f:
        sys_content = f.read()

    import re
    sys_content = re.sub(r'batch_size:\s*\d+', f'batch_size: {new_batch}', sys_content)

    with open("config/system.yaml", "w") as f:
        f.write(sys_content)

    reloaded = False
    for _ in range(50):
        time.sleep(0.1)
        if get_config().system.batch_size == new_batch:
            reloaded = True
            break

    if not reloaded:
        print("FAILED: Config did not reload within 5 seconds.")
        sys.exit(1)

    print("PASS: Config watcher hot-reloaded successfully.")

    # 6. Logger structured JSON tests
    print("Check 6: Testing structured logger output...")
    from app.core.logger import get_logger
    test_logger = get_logger("test_module")
    test_logger.info("Test info message", extra={"user_id": 123})
    test_logger.warning("Test warning", extra={"warn_code": "W01"})
    test_logger.error("Test error", extra={"is_fatal": True})
    test_logger.info("Another info")
    test_logger.error("Another error")

    with open("logs/app.log", "r") as f:
        lines = f.readlines()
        
    test_lines = lines[-5:]
    for line in test_lines:
        try:
            obj = json.loads(line)
            required_keys = {"timestamp", "module", "event", "level"}
            if not required_keys.issubset(obj.keys()):
                print(f"FAILED: Log line missing required keys. Found keys: {obj.keys()}")
                sys.exit(1)
        except Exception as e:
            print(f"FAILED: Log line is not valid JSON. Error: {e}\nLine: {line}")
            sys.exit(1)
    print("PASS: JSON structured logging verified.")

    # 7. YAML loading and validation
    print("Check 7: Validating YAML configs...")
    try:
        with open("config/profile.yaml", "r") as f:
            profile = yaml.safe_load(f)
        assert 'name' in profile['freelancer']
        assert 'services' in profile
        assert len(profile['services']) >= 4
        
        with open("config/targets.yaml", "r") as f:
            targets = yaml.safe_load(f)
        assert 'sectors' in targets['targeting']
        assert 'pain_signals' in targets['targeting']
        
        with open("config/outreach.yaml", "r") as f:
            outreach = yaml.safe_load(f)
        assert 'max_follow_ups' in outreach
        assert 'daily_send_limit' in outreach
        assert 'imap_poll_interval_minutes' in outreach['reply_detection']
        
        with open("config/system.yaml", "r") as f:
            system = yaml.safe_load(f)
        assert 'batch_size' in system['system']
        assert 'dry_run' in system['system']['outreach']
        
        print("PASS: All YAML configs are valid and contain required fields.")
        
    except Exception as e:
        print(f"FAILED: YAML validation failed with error: {e}")
        sys.exit(1)

    print("\n--- SUMMARY ---")
    print("1. init_db.py clean run -> PASS")
    print("2. init_db.py idempotency -> PASS")
    print("3. SQLite tables exist -> PASS")
    print("4. SQLite leads schema/constraints -> PASS")
    print("5. Config watcher hot-reload -> PASS")
    print("6. Structured JSON logging -> PASS")
    print("7. YAML structure/content -> PASS")

if __name__ == "__main__":
    main()
