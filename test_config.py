import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.core.config_loader import get_config, start_config_watcher
from app.core.logger import get_logger

logger = get_logger("test_config")

def main():
    logger.info("Starting config test")
    start_config_watcher()
    cfg = get_config()
    print(f"Initial batch_size: {cfg.system.batch_size}")
    
    # Wait for the watcher to kick in
    time.sleep(2)
    
    # Write a new value to system.yaml
    with open("config/system.yaml", "r") as f:
        content = f.read()
    
    new_content = content.replace("batch_size: 10", "batch_size: 25")
    
    with open("config/system.yaml", "w") as f:
        f.write(new_content)
        
    print("Modified system.yaml, waiting for reload...")
    
    # Wait for reload
    time.sleep(3)
    
    cfg = get_config()
    print(f"New batch_size: {cfg.system.batch_size}")
    
    if cfg.system.batch_size == 25:
        print("CONFIG RELOAD SUCCESS")
    else:
        print("CONFIG RELOAD FAILED")
        sys.exit(1)

    # Now verify the log file has valid JSON
    with open("logs/app.log", "r") as f:
        import json
        for i, line in enumerate(f):
            try:
                json.loads(line)
            except Exception as e:
                print(f"JSON Parse error on line {i}: {e}")
                sys.exit(1)
        print("LOG VERIFICATION SUCCESS: All log lines are valid JSON")

if __name__ == "__main__":
    main()
