import sqlite3
import os

def run_migration():
    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "contractor_os.db")
    
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}. Skipping migration.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check if sending_identity exists in outreach_sequences
        cursor.execute("PRAGMA table_info(outreach_sequences)")
        columns = [col[1] for col in cursor.fetchall()]
        if "sending_identity" not in columns:
            print("Adding sending_identity column to outreach_sequences...")
            cursor.execute("ALTER TABLE outreach_sequences ADD COLUMN sending_identity TEXT")
        else:
            print("sending_identity column already exists in outreach_sequences.")
            
        # Check if sending_identity exists in email_events
        cursor.execute("PRAGMA table_info(email_events)")
        columns = [col[1] for col in cursor.fetchall()]
        if "sending_identity" not in columns:
            print("Adding sending_identity column to email_events...")
            cursor.execute("ALTER TABLE email_events ADD COLUMN sending_identity TEXT")
        else:
            print("sending_identity column already exists in email_events.")

        conn.commit()
        print("Migration complete.")
    except Exception as e:
        print(f"Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    run_migration()
