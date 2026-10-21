"""Agent 8: Synchronize the Toss account snapshot to Google Sheets."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Protocol

from config.google_sheets_config import GoogleSheetsConfig, google_sheets_config
from services.toss_client import TossSecuritiesClient


class GoogleSheetsSyncError(RuntimeError):
    """Raised when the account snapshot cannot be safely written to Sheets."""


class SheetsWriter(Protocol):
    def batch_update_values(self, data: List[Dict[str, Any]]) -> None: ...


def parse_ticker_rows(value: str) -> Dict[str, int]:
    """Parse comma-separated TICKER:ROW pairs while rejecting ambiguous mappings."""
    rows: Dict[str, int] = {}
    used_rows: set[int] = set()

    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            symbol, row_text = entry.rsplit(":", 1)
            symbol = symbol.strip().upper()
            row = int(row_text.strip())
        except ValueError as exc:
            raise ValueError(
                f"Invalid GOOGLE_SHEETS_TICKER_ROWS entry {entry!r}; expected TICKER:ROW."
            ) from exc
        if not symbol or symbol in {"CASH", "예수금"} or row < 5:
            raise ValueError(f"Invalid ticker or row in GOOGLE_SHEETS_TICKER_ROWS: {entry!r}.")
        if symbol in rows:
            raise ValueError(f"Duplicate ticker in GOOGLE_SHEETS_TICKER_ROWS: {symbol}.")
        if row in used_rows:
            raise ValueError(f"Duplicate row in GOOGLE_SHEETS_TICKER_ROWS: {row}.")
        rows[symbol] = row
        used_rows.add(row)

    if not rows:
        raise ValueError("GOOGLE_SHEETS_TICKER_ROWS must map each ticker to a sheet row.")
    return rows


class GoogleSheetsWriter:
    """Minimal Google Sheets API writer using a service account."""

    def __init__(self, config: GoogleSheetsConfig):
        credential_path = Path(config.service_account_file).expanduser()
        if not credential_path.is_file():
            raise GoogleSheetsSyncError(
                "Google service-account credentials file does not exist: "
                f"{credential_path}"
            )

        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials.from_service_account_file(
            str(credential_path),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self.config = config
        self.service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def batch_update_values(self, data: List[Dict[str, Any]]) -> None:
        spreadsheet = self.service.spreadsheets().get(
            spreadsheetId=self.config.spreadsheet_id,
            fields="sheets.properties(sheetId,title)",
        ).execute()
        matching_sheets = [
            item["properties"]["title"]
            for item in spreadsheet.get("sheets", [])
            if item.get("properties", {}).get("sheetId") == self.config.worksheet_gid
        ]
        if len(matching_sheets) != 1:
            raise GoogleSheetsSyncError(
                f"Could not uniquely find worksheet gid {self.config.worksheet_gid}."
            )

        title = matching_sheets[0].replace("'", "''")
        sheet_data = [
            {**item, "range": f"'{title}'!{item['range']}"}
            for item in data
        ]
        self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=self.config.spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": sheet_data},
        ).execute()


class GoogleSheetsAccountSyncAgent:
    """Write current Toss holdings, cash, and per-position returns to the sheet."""

    def __init__(
        self,
        client: TossSecuritiesClient | None = None,
        config: GoogleSheetsConfig | None = None,
        sheets_writer: SheetsWriter | None = None,
    ):
        self.client = client or TossSecuritiesClient()
        self.config = config or google_sheets_config
        self.sheets_writer = sheets_writer

    def sync(self) -> str:
        if not self.config.is_configured:
            raise GoogleSheetsSyncError(
                "Google Sheets sync is enabled but its spreadsheet, credentials, "
                "ticker rows, or cash row are not configured."
            )
        if not self.client.is_configured:
            raise GoogleSheetsSyncError(
                "Google Sheets sync requires configured Toss Securities API credentials."
            )

        try:
            row_map = parse_ticker_rows(self.config.ticker_rows)
            data = self._build_updates(row_map)
            writer = self.sheets_writer or GoogleSheetsWriter(self.config)
            writer.batch_update_values(data)
        except GoogleSheetsSyncError:
            raise
        except Exception as exc:
            raise GoogleSheetsSyncError(
                f"Agent 8 could not synchronize the Toss account snapshot: {exc}"
            ) from exc

        active_count = sum(
            1 for item in data
            if item["range"].startswith("A")
            and item["values"][0][0] not in ("", "예수금")
        )
        return f"Google Sheets 계좌 현황 동기화 완료: 보유 종목 {active_count}개, 예수금 포함."

    def _build_updates(self, row_map: Dict[str, int]) -> List[Dict[str, Any]]:
        if self.config.cash_row < 5 or self.config.cash_row in row_map.values():
            raise GoogleSheetsSyncError(
                "GOOGLE_SHEETS_CASH_ROW must be 5 or greater and must not overlap a ticker row."
            )

        holdings_response = self.client.get_holdings()
        items = holdings_response.get("items")
        if not isinstance(items, list):
            raise GoogleSheetsSyncError("Toss holdings response has no valid items list.")

        positions: Dict[str, Dict[str, float]] = {}
        for item in items:
            if not isinstance(item, dict):
                raise GoogleSheetsSyncError("Toss holdings response contains an invalid item.")
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                raise GoogleSheetsSyncError("Toss holding is missing its ticker symbol.")
            quantity = self._number(item.get("quantity"), f"{symbol} quantity")
            if quantity < 0:
                raise GoogleSheetsSyncError(f"Toss returned negative {symbol} quantity.")
            if quantity == 0:
                continue
            if symbol in positions:
                raise GoogleSheetsSyncError(f"Toss returned duplicate holding rows for {symbol}.")
            if symbol not in row_map:
                raise GoogleSheetsSyncError(
                    f"No Google Sheets row is configured for held ticker {symbol}; "
                    "update GOOGLE_SHEETS_TICKER_ROWS before syncing."
                )

            raw_average = item.get("averagePurchasePrice", item.get("averagePrice"))
            average_price = self._number(raw_average, f"{symbol} average purchase price")
            if average_price <= 0:
                raise GoogleSheetsSyncError(
                    f"Toss did not provide a positive average purchase price for {symbol}."
                )
            current_price = self._number(
                self.client.get_current_price(symbol),
                f"{symbol} current price",
            )
            if current_price <= 0:
                raise GoogleSheetsSyncError(
                    f"Toss did not provide a positive current price for {symbol}."
                )
            positions[symbol] = {
                "quantity": quantity,
                "average_price": average_price,
                "current_price": current_price,
                "market_value": quantity * current_price,
            }

        buying_power = self.client.get_buying_power(currency="USD")
        cash_value = buying_power.get("cashBuyingPower", buying_power.get("usdCash"))
        cash = self._number(cash_value, "USD cash buying power")
        if cash < 0:
            raise GoogleSheetsSyncError("Toss returned negative USD cash buying power.")

        total_equity = cash + sum(position["market_value"] for position in positions.values())
        if not math.isfinite(total_equity) or total_equity <= 0:
            raise GoogleSheetsSyncError("Toss account total value must be positive.")

        updates: List[Dict[str, Any]] = []
        for symbol, row in sorted(row_map.items(), key=lambda pair: pair[1]):
            position = positions.get(symbol)
            if position is None:
                values = ["", "", "", "", "", ""]
                return_pct = ""
            else:
                current_price = position["current_price"]
                average_price = position["average_price"]
                market_value = position["market_value"]
                values = [
                    symbol,
                    round(current_price, 4),
                    round(average_price, 4),
                    position["quantity"],
                    round(market_value, 2),
                    round(market_value / total_equity, 8),
                ]
                return_pct = round(current_price / average_price - 1.0, 8)

            updates.extend([
                {"range": f"A{row}:F{row}", "values": [values]},
                {"range": f"J{row}", "values": [[return_pct]]},
            ])

        updates.extend([
            {
                "range": f"A{self.config.cash_row}:F{self.config.cash_row}",
                "values": [["예수금", "", "", "", round(cash, 2), round(cash / total_equity, 8)]],
            },
            {"range": f"J{self.config.cash_row}", "values": [[""]]},
        ])
        return updates

    @staticmethod
    def _number(value: Any, label: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise GoogleSheetsSyncError(f"Toss returned an invalid {label}: {value!r}.") from exc
        if not math.isfinite(result):
            raise GoogleSheetsSyncError(f"Toss returned a non-finite {label}: {value!r}.")
        return result
