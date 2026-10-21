import pytest

from agents.agent8_sheets_sync import (
    GoogleSheetsAccountSyncAgent,
    GoogleSheetsSyncError,
    GoogleSheetsWriter,
    parse_ticker_rows,
)
from config.google_sheets_config import GoogleSheetsConfig
from services.toss_client import TossSecuritiesClient


class FakeTossClient(TossSecuritiesClient):
    def __init__(self, holdings=None, cash="500.00", prices=None):
        super().__init__(client_id="id", client_secret="secret", account_seq="1")
        self.holdings = holdings or []
        self.cash = cash
        self.prices = prices or {}

    def get_holdings(self):
        return {"items": self.holdings}

    def get_buying_power(self, currency="USD"):
        return {"cashBuyingPower": self.cash}

    def get_current_price(self, symbol):
        return self.prices.get(symbol, 0)


class FakeSheetsWriter:
    def __init__(self):
        self.updates = []

    def batch_update_values(self, data):
        self.updates.append(data)


def make_config(**overrides):
    values = {
        "enabled": True,
        "spreadsheet_id": "sheet-id",
        "worksheet_gid": 22630165,
        "service_account_file": "unused-in-tests.json",
        "ticker_rows": "SOXL:5,NVDL:6,SOXX:7",
        "cash_row": 10,
    }
    values.update(overrides)
    return GoogleSheetsConfig(**values)


def test_parse_ticker_rows_rejects_duplicate_tickers_and_rows():
    with pytest.raises(ValueError, match="Duplicate ticker"):
        parse_ticker_rows("SOXL:5,soxl:6")

    with pytest.raises(ValueError, match="Duplicate row"):
        parse_ticker_rows("SOXL:5,NVDL:5")

    with pytest.raises(ValueError, match="Invalid ticker or row"):
        parse_ticker_rows("SOXL:4")

    with pytest.raises(ValueError, match="Invalid ticker or row"):
        parse_ticker_rows("CASH:11")


def test_sync_writes_ticker_values_returns_cash_and_clears_empty_rows():
    client = FakeTossClient(
        holdings=[
            {"symbol": "SOXL", "quantity": "10", "averagePurchasePrice": "100.00"},
            {"symbol": "NVDL", "quantity": "2", "averagePrice": "50.00"},
        ],
        prices={"SOXL": 110, "NVDL": 40},
    )
    writer = FakeSheetsWriter()

    result = GoogleSheetsAccountSyncAgent(
        client=client,
        config=make_config(),
        sheets_writer=writer,
    ).sync()

    assert result.endswith("보유 종목 2개, 예수금 포함.")
    updates = writer.updates[0]
    by_range = {item["range"]: item["values"][0] for item in updates}

    assert by_range["A5:F5"] == ["SOXL", 110, 100, 10, 1100, 0.6547619]
    assert by_range["J5"] == [0.1]
    assert by_range["A6:F6"] == ["NVDL", 40, 50, 2, 80, 0.04761905]
    assert by_range["J6"] == [-0.2]
    assert by_range["A7:F7"] == ["", "", "", "", "", ""]
    assert by_range["J7"] == [""]
    assert by_range["A10:F10"] == ["예수금", "", "", "", 500, 0.29761905]
    assert by_range["J10"] == [""]
    assert all(":G" not in item["range"] and ":I" not in item["range"] for item in updates)


def test_sync_fails_before_sheet_write_when_new_ticker_is_unmapped():
    client = FakeTossClient(
        holdings=[{"symbol": "TQQQ", "quantity": "1", "averagePurchasePrice": "50"}],
        prices={"TQQQ": 60},
    )
    writer = FakeSheetsWriter()
    agent = GoogleSheetsAccountSyncAgent(
        client=client,
        config=make_config(),
        sheets_writer=writer,
    )

    with pytest.raises(GoogleSheetsSyncError, match="No Google Sheets row is configured"):
        agent.sync()

    assert writer.updates == []


def test_sync_fails_without_a_valid_average_price():
    client = FakeTossClient(
        holdings=[{"symbol": "SOXL", "quantity": "1", "averagePurchasePrice": "0"}],
        prices={"SOXL": 60},
    )
    writer = FakeSheetsWriter()
    agent = GoogleSheetsAccountSyncAgent(
        client=client,
        config=make_config(),
        sheets_writer=writer,
    )

    with pytest.raises(GoogleSheetsSyncError, match="positive average purchase price"):
        agent.sync()

    assert writer.updates == []


def test_sync_rejects_cash_row_collision_before_toss_requests():
    client = FakeTossClient()
    agent = GoogleSheetsAccountSyncAgent(
        client=client,
        config=make_config(cash_row=5),
        sheets_writer=FakeSheetsWriter(),
    )

    with pytest.raises(GoogleSheetsSyncError, match="must not overlap a ticker row"):
        agent.sync()


def test_writer_resolves_gid_and_quotes_worksheet_title():
    class FakeRequest:
        def __init__(self, result):
            self.result = result

        def execute(self):
            return self.result

    class FakeValues:
        def batchUpdate(self, **kwargs):
            self.request = kwargs
            return FakeRequest({})

    class FakeSpreadsheets:
        def __init__(self):
            self.values_api = FakeValues()

        def get(self, **kwargs):
            return FakeRequest({
                "sheets": [
                    {"properties": {"sheetId": 22630165, "title": "동주(키움)"}},
                ]
            })

        def values(self):
            return self.values_api

    class FakeService:
        def __init__(self):
            self.spreadsheets_api = FakeSpreadsheets()

        def spreadsheets(self):
            return self.spreadsheets_api

    writer = GoogleSheetsWriter.__new__(GoogleSheetsWriter)
    writer.config = make_config()
    writer.service = FakeService()

    writer.batch_update_values([{"range": "A5:F5", "values": [["SOXL"]]}])

    request = writer.service.spreadsheets_api.values_api.request
    assert request["spreadsheetId"] == "sheet-id"
    assert request["body"]["valueInputOption"] == "USER_ENTERED"
    assert request["body"]["data"][0]["range"] == "'동주(키움)'!A5:F5"
