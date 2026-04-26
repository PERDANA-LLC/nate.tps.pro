"""
Configuration management for Options Detective.
Uses environment variables with Pydantic Settings.
"""
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings with environment variable loading."""
    
    # Schwab API
    schwab_client_id: str = Field(..., alias="SCHWAB_CLIENT_ID")
    schwab_client_secret: str = Field(..., alias="SCHWAB_CLIENT_SECRET")
    schwab_callback_url: str = Field(
        default="http://localhost:8000/callback",
        alias="SCHWAB_CALLBACK_URL"
    )
    schwab_auth_mode: str = Field(
        default="sandbox",
        alias="SCHWAB_AUTH_MODE"
    )  # sandbox or live
    
    # Database
    database_url: str = Field(
        default="sqlite:///./options_detective.db",
        alias="DATABASE_URL"
    )
    
    # Trading Parameters
    paper_trading_mode: bool = Field(
        default=True,
        alias="PAPER_TRADING_MODE"
    )
    initial_balance: float = Field(
        default=10000.0,
        alias="INITIAL_BALANCE"
    )
    max_position_size: float = Field(
        default=0.05,
        alias="MAX_POSITION_SIZE"
    )  # 5% of portfolio per position
    max_daily_loss: float = Field(
        default=0.02,
        alias="MAX_DAILY_LOSS"
    )  # 2% daily loss limit
    risk_free_rate: float = Field(
        default=0.05,
        alias="RISK_FREE_RATE"
    )
    
    # Discord
    discord_webhook_url: Optional[str] = Field(
        default=None,
        alias="DISCORD_WEBHOOK_URL"
    )
    
    # App Settings
    secret_key: str = Field(default="dev-secret-change-in-production")
    debug: bool = Field(default=True)
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    
    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
