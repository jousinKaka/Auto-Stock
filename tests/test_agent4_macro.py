import pandas as pd

from agents.agent4_macro import MacroIntelligenceAgent


def _macro_prices(symbols):
    dates = pd.bdate_range(
        end=pd.Timestamp.now().normalize() - pd.Timedelta(days=2),
        periods=4,
    )
    columns = pd.MultiIndex.from_product([["Close"], symbols])
    data = pd.DataFrame(index=dates, columns=columns, dtype=float)
    for index, symbol in enumerate(symbols):
        start = 4.0 + index
        data[("Close", symbol)] = [start, start + 0.1, start + 0.2, start + 0.3]
    return data


def test_agent4_uses_fetched_macro_values_without_trade_recommendations(monkeypatch):
    symbols = list(MacroIntelligenceAgent.MACRO_SYMBOLS)
    data = _macro_prices(symbols)
    data[("Close", "^VIX")] = [16.0, 17.0, 18.0, 19.0]
    data[("Close", "^TNX")] = [4.1, 4.2, 4.3, 4.4]
    data[("Close", "DX-Y.NYB")] = [104.0, 104.1, 104.2, 104.3]
    monkeypatch.setattr(
        "agents.agent4_macro.yf.download",
        lambda *args, **kwargs: data,
    )

    result = MacroIntelligenceAgent().fetch_and_analyze()
    report = result.to_markdown()

    assert result.overall_regime == "RISK_ON"
    assert result.vix_level == 19.0
    assert result.yield_pressure == "MODERATE"
    assert result.dollar_pressure == "MODERATE"
    assert result.data_as_of == data.index[-1].strftime("%Y-%m-%d")
    assert len(result.indicators) == len(symbols)
    assert "분할 매수 규칙을 계획대로 이행하기에 적합" not in report
    assert "매매 적합성이나 수익을 보장하지 않습니다" in report


def test_agent4_does_not_classify_regime_when_required_data_is_missing(monkeypatch):
    data = _macro_prices(["^VIX"])
    data[("Close", "^VIX")] = [16.0, 17.0, 18.0, 19.0]
    monkeypatch.setattr(
        "agents.agent4_macro.yf.download",
        lambda *args, **kwargs: data,
    )

    result = MacroIntelligenceAgent().fetch_and_analyze()
    report = result.to_markdown()

    assert result.overall_regime == "DATA_UNAVAILABLE"
    assert result.vix_level == 19.0
    assert result.yield_pressure is None
    assert result.dollar_pressure is None
    assert len(result.indicators) == 1
    assert "핵심 지표 미비로 판정하지 않음" in report
    assert "미국 10년물 국채금리" in result.data_quality_note
    assert "달러 인덱스 (DXY)" in result.data_quality_note
    assert "**VIX**: 19.00" in report


def test_agent4_reports_download_errors_without_fabricated_indicators(monkeypatch):
    def fail_download(*args, **kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr("agents.agent4_macro.yf.download", fail_download)

    result = MacroIntelligenceAgent().fetch_and_analyze()
    report = result.to_markdown()

    assert result.overall_regime == "DATA_UNAVAILABLE"
    assert result.indicators == []
    assert result.vix_level is None
    assert "ConnectionError: offline" in result.data_quality_note
    assert "확인 불가" in report
    assert "4.15" not in report
    assert "17.50" not in report
