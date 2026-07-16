import csv
import json
from app.modules.hunter.dedup import insert_lead_if_new

async def import_from_csv(path: str, session) -> dict:
    """
    Reads a CSV (or JSON list) with company_name, website/domain, etc.
    Normalizes and calls insert_lead_if_new per row.
    Returns counts.
    """
    inserted = 0
    skipped = 0
    total = 0
    
    if path.endswith('.json'):
        with open(path, mode='r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("JSON import requires a top-level list of dictionaries.")
            for row in data:
                total += 1
                row['source'] = 'manual_json'
                if await insert_lead_if_new(session, row):
                    inserted += 1
                else:
                    skipped += 1
    else:
        # Default to CSV
        with open(path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                row['source'] = 'manual_csv'
                if await insert_lead_if_new(session, row):
                    inserted += 1
                else:
                    skipped += 1
                
    return {"inserted": inserted, "skipped_duplicates": skipped, "rows_read": total}
