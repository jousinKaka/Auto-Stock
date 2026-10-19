from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = Path(__file__).resolve().parent.parent

class TossConfig(BaseSettings):
    """Toss Securities OpenAPI Configuration."""
    
    toss_client_id: str = Field(default="", description="Toss OpenAPI Client ID")
    toss_client_secret: str = Field(default="", description="Toss OpenAPI Client Secret")
    toss_account_seq: str = Field(default="", description="Toss Account Sequence")
    
    toss_base_url: str = Field(default="https://openapi.tossinvest.com", description="REST Base URL")
    toss_ws_url: str = Field(default="wss://openapi-ws.tossinvest.com/ws/v1", description="WebSocket URL")
    
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    @property
    def is_configured(self) -> bool:
        """Check if essential credentials are provided."""
        return bool(self.toss_client_id and self.toss_client_secret and self.toss_account_seq)

toss_config = TossConfig()

