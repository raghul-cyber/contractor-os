import threading
from pathlib import Path
import yaml
from pydantic import BaseModel
from typing import List, Optional
from watchfiles import watch
from .logger import get_logger

logger = get_logger(__name__)
CONFIG_DIR = Path("config")

# Pydantic Models for Configuration
class ServiceConfig(BaseModel):
    name: str
    description: str
    price_range: str
    delivery: str

class ProfileConfig(BaseModel):
    name: str
    title: str
    portfolio_url: str
    linkedin: str
    email: str

class ProfileRoot(BaseModel):
    freelancer: ProfileConfig
    services: List[ServiceConfig]
    value_proposition: str
    past_results: List[str]

class EmployeeRange(BaseModel):
    min: int
    max: int

class TargetingConfig(BaseModel):
    sectors: List[str]
    company_types: List[str]
    locations: List[str]
    decision_maker_titles: List[str]
    employee_range: EmployeeRange
    pain_signals: List[str]

class TargetsRoot(BaseModel):
    targeting: TargetingConfig

class SequenceConfig(BaseModel):
    follow_up_intervals_days: List[int]

class ReplyDetectionConfig(BaseModel):
    imap_poll_interval_minutes: int

class NotificationsConfig(BaseModel):
    telegram_bot_token: str
    telegram_chat_id: str
    discord_webhook: str

class OutreachRoot(BaseModel):
    sequence: SequenceConfig
    max_follow_ups: int
    daily_send_limit: int
    send_window_hours: List[int]
    randomize_send_time: bool
    reply_detection: ReplyDetectionConfig
    positive_keywords: List[str]
    negative_keywords: List[str]
    notifications: NotificationsConfig

class ProfilerSystemConfig(BaseModel):
    min_fit_score: float
    concurrent_scrapes: int

class CraftSystemConfig(BaseModel):
    require_manual_approval: bool

class OutreachSystemConfig(BaseModel):
    dry_run: bool

class HunterSystemConfig(BaseModel):
    use_paid_leadscraper: bool
    leadscraper_actor_id: str

class SystemConfig(BaseModel):
    batch_size: int
    cycle_interval_hours: int
    profiler: ProfilerSystemConfig
    craft: CraftSystemConfig
    outreach: OutreachSystemConfig
    hunter: HunterSystemConfig

class SystemRoot(BaseModel):
    system: SystemConfig

class AppConfig(BaseModel):
    profile: ProfileRoot
    targets: TargetsRoot
    outreach: OutreachRoot
    system: SystemConfig

# Global config state
_live_config: Optional[AppConfig] = None
_watcher_thread: Optional[threading.Thread] = None

def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def load_config() -> AppConfig:
    profile_data = _load_yaml(CONFIG_DIR / "profile.yaml")
    targets_data = _load_yaml(CONFIG_DIR / "targets.yaml")
    outreach_data = _load_yaml(CONFIG_DIR / "outreach.yaml")
    system_data = _load_yaml(CONFIG_DIR / "system.yaml")
    
    return AppConfig(
        profile=ProfileRoot(**profile_data),
        targets=TargetsRoot(**targets_data),
        outreach=OutreachRoot(**outreach_data),
        system=SystemRoot(**system_data).system
    )

def get_config() -> AppConfig:
    global _live_config
    if _live_config is None:
        _live_config = load_config()
    return _live_config

def _watch_config_sync():
    global _live_config
    logger.info("Starting config watcher thread on config/ directory")
    for changes in watch(CONFIG_DIR):
        logger.info(f"Config files changed: {changes}, reloading config")
        try:
            new_config = load_config()
            _live_config = new_config
            logger.info("Config successfully reloaded")
        except Exception as e:
            logger.error(f"Failed to reload config: {e}")

def start_config_watcher():
    """Starts the config watcher in a background thread."""
    global _watcher_thread
    if _watcher_thread is not None and _watcher_thread.is_alive():
        return
    _watcher_thread = threading.Thread(target=_watch_config_sync, daemon=True)
    _watcher_thread.start()
