from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from agents.agent1_journal import DailyExecutionRecord, ExecutionJournalAgent
from agents.agent5_industry import SemiIndustryAgent
from agents.agent6_fundamental import ConstituentsFundamentalAgent
from services import market_data


def test_daily_close_series_preserves_timestamps_and_excludes_incomplete_bar():
    now = datetime(2026, 10, 19, 15, 59, tzinfo=ZoneInfo("America/New_York"))
    data = pd.DataFrame(
        {"Close": [95.0, 99.0]},
        index=pd.to_datetime(["2026-10-16", "2026-10-19"]),
    )

    closes = market_data.extract_daily_close_series(data, limit=None, now=now)

    assert closes.tolist() == [95.0]
    assert closes.index[-1] == pd.Timestamp("2026-10-16")


def test_agent5_reports_actual_trend_metrics_without_trade_recommendation(monkeypatch):
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=2), periods=260)
    prices = pd.Series(range(100, 360), index=dates, dtype=float)
    monkeypatch.setattr(
        "agents.agent5_industry.yf.download",
        lambda *args, **kwargs: pd.DataFrame({"Close": prices}),
    )

    snapshot = SemiIndustryAgent().analyze()
    report = snapshot.to_markdown()

    assert snapshot.soxx_price == 359.0
    assert snapshot.trend_state == "BULLISH_EXPANSION"
    assert snapshot.cycle_phase == "TECHNICAL_TREND_ONLY"
    assert snapshot.data_as_of == dates[-1].strftime("%Y-%m-%d")
    assert "주문 신호를 의미하지 않습니다" in report
    assert "매수 기회" not in report


def test_agent5_surfaces_data_failure_instead_of_using_example_prices(monkeypatch):
    monkeypatch.setattr(
        "agents.agent5_industry.yf.download",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("offline")),
    )

    snapshot = SemiIndustryAgent().analyze()

    assert snapshot.soxx_price is None
    assert snapshot.trend_state == "DATA_UNAVAILABLE"
    assert "ConnectionError: offline" in snapshot.data_quality_note
    assert "산출 불가" in snapshot.to_markdown()


def test_agent6_reports_calendar_estimates_and_labels_watchlist_scope(monkeypatch):
    symbols = [item["symbol"] for item in ConstituentsFundamentalAgent.CORE_COMPANIES]
    dates = pd.date_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=2), periods=3)
    columns = pd.MultiIndex.from_product([["Close"], symbols])
    data = pd.DataFrame(
        [[100.0 + offset + index for index in range(len(symbols))] for offset in range(3)],
        index=dates,
        columns=columns,
    )
    monkeypatch.setattr(
        "agents.agent6_fundamental.yf.download",
        lambda *args, **kwargs: data,
    )

    future_earnings = date.today() + timedelta(days=30)

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def calendar(self):
            if self.symbol == "NVDA":
                return {
                    "Earnings Date": [future_earnings],
                    "Earnings Average": 2.47,
                    "Revenue Average": 108_000_000_000,
                }
            return {}

    monkeypatch.setattr("agents.agent6_fundamental.yf.Ticker", FakeTicker)

    analysis = ConstituentsFundamentalAgent().analyze()
    report = analysis.to_markdown()
    nvidia = next(company for company in analysis.companies if company.symbol == "NVDA")

    assert nvidia.current_price == 102.0
    assert nvidia.next_earnings_date == future_earnings.isoformat()
    assert nvidia.consensus_eps == 2.47
    assert nvidia.consensus_revenue == 108_000_000_000
    assert "실시간 편입 종목·비중을 뜻하지 않습니다" in report
    assert "$108.00B" in report
    assert "실적 체크 지표" in report


def test_agent6_marks_missing_calendar_data_without_inventing_estimates(monkeypatch):
    symbols = [item["symbol"] for item in ConstituentsFundamentalAgent.CORE_COMPANIES]
    dates = pd.date_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=2), periods=3)
    columns = pd.MultiIndex.from_product([["Close"], symbols])
    data = pd.DataFrame(100.0, index=dates, columns=columns)
    monkeypatch.setattr(
        "agents.agent6_fundamental.yf.download",
        lambda *args, **kwargs: data,
    )

    class FakeTicker:
        @property
        def calendar(self):
            return {}

    monkeypatch.setattr("agents.agent6_fundamental.yf.Ticker", lambda symbol: FakeTicker())

    analysis = ConstituentsFundamentalAgent().analyze()
    nvidia = next(company for company in analysis.companies if company.symbol == "NVDA")

    assert nvidia.next_earnings_date is None
    assert nvidia.consensus_eps is None
    assert nvidia.consensus_revenue is None
    assert "향후 실적 발표일이 없습니다" in nvidia.data_quality_note


def test_daily_report_includes_fundamental_summary_without_an_order_plan(tmp_path):
    record = DailyExecutionRecord(
        date="2026-10-20",
        cycle=1,
        ticker="SOXL",
        prev_close=98.65,
        current_close=98.65,
        T_before=17.0,
        T_after=17.0,
        mode="NORMAL",
        total_shares_before=102,
        total_shares_after=102,
        avg_price_before=122.79,
        avg_price_after=122.79,
        remaining_cash_before=17309.45,
        remaining_cash_after=17309.45,
    )
    journal = ExecutionJournalAgent(
        history_file=tmp_path / "history.json",
        reports_dir=tmp_path / "reports",
    )

    report = journal.generate_daily_markdown_report(
        record=record,
        fundamental_summary="NVDA 실적 일정",
    )

    assert "SOXL 관련 기업 및 실적 캘린더 (Agent 6)" in report
    assert "NVDA 실적 일정" in report
    assert "다음 거래일 주문표" not in report
