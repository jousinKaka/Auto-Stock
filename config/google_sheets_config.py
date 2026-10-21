from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.settings import BASE_DIR


class GoogleSheetsConfig(BaseSettings):
    """Configuration for Agent 8's Google Sheets account snapshot sync."""

    enabled: bool = Field(default=False, alias="GOOGLE_SHEETS_ENABLED")
    spreadsheet_id: str = Field(default="", alias="GOOGLE_SHEETS_SPREADSHEET_ID")
    worksheet_gid: int = Field(default=22630165, alias="GOOGLE_SHEETS_WORKSHEET_GID")
    service_account_file: str = Field(default="", alias="GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE")
    ticker_rows: str = Field(default="", alias="GOOGLE_SHEETS_TICKER_ROWS")
    cash_row: int = Field(default=10, alias="GOOGLE_SHEETS_CASH_ROW")

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def is_configured(self) -> bool:
        return bool(
            self.enabled
            and self.spreadsheet_id
            and self.service_account_file
            and self.ticker_rows
            and self.cash_row > 0
        )


google_sheets_config = GoogleSheetsConfig()
