import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = Path(__file__).resolve().parent.parent

class StrategySettings(BaseSettings):
    """Infinite Buying Method V4.0 Strategy Settings."""
    
    # Target Asset
    target_ticker: str = Field(default="SOXL", description="Target 3x leveraged ETF ticker")
    
    # Division & Capital
    divisions: int = Field(default=40, description="Total number of divisions (recommended 40 for SOXL)")
    target_profit_rate: float = Field(default=0.20, description="Target profit rate for SOXL (20%)")
    total_budget_usd: float = Field(default=10000.0, description="Total allocated budget in USD")
    
    # Execution Safeguards
    large_number_ratio: float = Field(default=1.12, description="Max upper limit for LOC buy orders (prev close * 1.12)")
    ladder_levels_front: int = Field(default=7, description="Number of downward ladder buy orders in first half")
    ladder_levels_back: int = Field(default=8, description="Number of downward ladder buy orders in second half")
    
    # Storage Paths
    data_dir: Path = BASE_DIR / "data"
    reports_dir: Path = BASE_DIR / "data" / "reports"
    history_file: Path = BASE_DIR / "data" / "trade_history.json"
    
    # Telegram Notifications
    telegram_bot_token: str = Field(default="", description="Telegram Bot API Token")
    telegram_chat_id: str = Field(default="", description="Telegram Chat ID")
    
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = StrategySettings()

# Ensure directories exist
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.reports_dir.mkdir(parents=True, exist_ok=True)

