import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure logs directory exists
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat() + "Z",
            "module": record.name,
            "event": record.getMessage(),
            "level": record.levelname,
        }
        
        # Add extra fields if they exist in the record
        # Extract fields that are not standard LogRecord attributes
        standard_attrs = {
            'args', 'asctime', 'created', 'exc_info', 'exc_text', 'filename',
            'funcName', 'id', 'levelname', 'levelno', 'lineno', 'module',
            'msecs', 'message', 'msg', 'name', 'pathname', 'process',
            'processName', 'relativeCreated', 'stack_info', 'thread', 'threadName', 'taskName'
        }
        
        for key, value in record.__dict__.items():
            if key not in standard_attrs:
                log_obj[key] = value
                
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
            
        return json.dumps(log_obj)

def get_logger(module_name: str) -> logging.Logger:
    logger = logging.getLogger(module_name)
    
    # If the logger already has handlers, return it to avoid duplication
    if logger.hasHandlers():
        return logger
        
    logger.setLevel(logging.INFO)
    
    formatter = JSONFormatter()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler
    file_handler = logging.FileHandler(LOGS_DIR / "app.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger
