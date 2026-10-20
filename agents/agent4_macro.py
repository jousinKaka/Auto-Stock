"""Agent 4: Macro and liquidity indicator analysis."""

from __future__ import annotations

import math
from datetime import datetime
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from pydantic import BaseModel, Field

from services.market_data import extract_daily_close_series


class MacroIndicatorSnapshot(BaseModel):
    symbol: str
    name: str
    current_value: float
    change_1d_pct: float
    change_1m_pct: float
    trend: str
    interpretation: str


class MacroAnalysisResult(BaseModel):
    timestamp: str
    data_as_of: Optional[str] = None
    overall_regime: str
    yield_pressure: Optional[str] = None
    dollar_pressure: Optional[str] = None
    vix_level: Optional[float] = None
    indicators: List[MacroIndicatorSnapshot] = Field(default_factory=list)
    summary_insight: str = ""
    data_quality_note: str = ""

    def to_markdown(self) -> str:
        lines = [
            f"- **조회 시각**: {self.timestamp}",
            f"- **기준 거래일**: {self.data_as_of or '확인 불가'} (미국 동부시간 기준 완료 일봉)",
            f"- **시장 매크로 국면**: `{self.overall_regime}`",
            (
                f"- **VIX**: {self.vix_level:.2f}"
                if self.vix_level is not None
                else "- **VIX**: 확인 불가"
            ),
            (
                f"- **금리 압박**: `{self.yield_pressure}` | "
                f"**달러 압박**: `{self.dollar_pressure}`"
                if self.yield_pressure is not None and self.dollar_pressure is not None
                else "- **금리/달러 압박**: 핵심 지표 미비로 판정하지 않음"
            ),
        ]
        if self.summary_insight:
            lines.append(f"- **요약**: {self.summary_insight}")
        if self.data_quality_note:
            lines.append(f"- **데이터 참고**: {self.data_quality_note}")
        if self.indicators:
            lines.extend([
                "",
                "| 지표 | 현재값 | 1일 | 1개월 | 추세 | 해석 |",
                "|:---|---:|---:|---:|:---|:---|",
            ])
            for indicator in self.indicators:
                lines.append(
                    f"| {indicator.name} (`{indicator.symbol}`) | "
                    f"{indicator.current_value:.2f} | {indicator.change_1d_pct:+.2f}% | "
                    f"{indicator.change_1m_pct:+.2f}% | {indicator.trend} | "
                    f"{indicator.interpretation} |"
                )
        return "\n".join(lines)


class MacroIntelligenceAgent:
    """Analyze fetched macro indicators; never substitute illustrative values."""

    MACRO_SYMBOLS = {
        "^TNX": "미국 10년물 국채금리",
        "DX-Y.NYB": "달러 인덱스 (DXY)",
        "^VIX": "CBOE 변동성 지수 (VIX)",
        "^NDX": "나스닥 100 지수",
        "^GSPC": "S&P 500 지수",
    }
    REQUIRED_SYMBOLS = {"^TNX", "DX-Y.NYB", "^VIX"}

    def fetch_and_analyze(self) -> MacroAnalysisResult:
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        now_str = now.isoformat(timespec="seconds")
        market_now = now.astimezone(ZoneInfo("America/New_York"))
        indicators: List[MacroIndicatorSnapshot] = []
        missing_symbols = set(self.MACRO_SYMBOLS)
        data_as_of: Optional[str] = None
        fetch_error = ""

        try:
            data = yf.download(
                list(self.MACRO_SYMBOLS),
                period="1mo",
                interval="1d",
                progress=False,
            )
            close_frame = self._close_frame(data)
            observed_dates = []

            for symbol, name in self.MACRO_SYMBOLS.items():
                if symbol not in close_frame.columns:
                    continue
                try:
                    closes = extract_daily_close_series(
                        pd.DataFrame({"Close": close_frame[symbol]}),
                        limit=None,
                        now=market_now,
                    )
                except (TypeError, ValueError):
                    continue

                if len(closes) < 2:
                    continue
                current = float(closes.iloc[-1])
                previous = float(closes.iloc[-2])
                month_ago = float(closes.iloc[0])
                if min(current, previous, month_ago) <= 0:
                    continue

                change_1d = (current - previous) / previous * 100.0
                change_1m = (current - month_ago) / month_ago * 100.0
                if not all(math.isfinite(value) for value in (change_1d, change_1m)):
                    continue

                interpretation, trend = self._interpret_indicator(
                    symbol,
                    current,
                    change_1m,
                )
                indicators.append(
                    MacroIndicatorSnapshot(
                        symbol=symbol,
                        name=name,
                        current_value=round(current, 2),
                        change_1d_pct=round(change_1d, 2),
                        change_1m_pct=round(change_1m, 2),
                        trend=trend,
                        interpretation=interpretation,
                    )
                )
                missing_symbols.discard(symbol)
                observed_dates.append(closes.index[-1])

            if observed_dates:
                data_as_of = min(observed_dates).strftime("%Y-%m-%d")
        except Exception as exc:
            fetch_error = f"Yahoo Finance 매크로 데이터 조회 실패: {type(exc).__name__}: {exc}"

        by_symbol = {indicator.symbol: indicator for indicator in indicators}
        missing_required = sorted(self.REQUIRED_SYMBOLS - by_symbol.keys())
        data_notes = []
        if fetch_error:
            data_notes.append(fetch_error)
        if missing_required:
            data_notes.append(
                "필수 지표 데이터 부족: "
                + ", ".join(self.MACRO_SYMBOLS[symbol] for symbol in missing_required)
            )
        optional_missing = sorted(missing_symbols - self.REQUIRED_SYMBOLS)
        if optional_missing:
            data_notes.append(
                "보조 지표 데이터 없음: "
                + ", ".join(self.MACRO_SYMBOLS[symbol] for symbol in optional_missing)
            )

        if missing_required:
            return MacroAnalysisResult(
                timestamp=now_str,
                data_as_of=data_as_of,
                overall_regime="DATA_UNAVAILABLE",
                vix_level=(
                    by_symbol["^VIX"].current_value
                    if "^VIX" in by_symbol
                    else None
                ),
                indicators=indicators,
                summary_insight=(
                    "핵심 매크로 지표가 모두 확보되지 않아 국면과 압박 수준을 판정하지 않았습니다."
                ),
                data_quality_note="; ".join(data_notes),
            )

        tnx_value = by_symbol["^TNX"].current_value
        dxy_value = by_symbol["DX-Y.NYB"].current_value
        vix_value = by_symbol["^VIX"].current_value
        if vix_value >= 25.0:
            regime = "RISK_OFF"
            summary = "VIX가 높은 구간입니다. 변동성 확대 여부와 실제 시장 데이터를 함께 확인하세요."
        elif vix_value >= 20.0:
            regime = "CAUTION"
            summary = "VIX가 주의 구간에 있습니다. 이 요약은 시장 지표 관찰이며 매매 지시가 아닙니다."
        else:
            regime = "RISK_ON"
            summary = "VIX는 상대적으로 낮은 구간입니다. 이는 매매 적합성이나 수익을 보장하지 않습니다."

        yield_pressure = "HIGH" if tnx_value > 4.5 else ("MODERATE" if tnx_value > 4.0 else "LOW")
        dollar_pressure = "HIGH" if dxy_value > 106.0 else ("MODERATE" if dxy_value > 103.0 else "LOW")
        return MacroAnalysisResult(
            timestamp=now_str,
            data_as_of=data_as_of,
            overall_regime=regime,
            yield_pressure=yield_pressure,
            dollar_pressure=dollar_pressure,
            vix_level=round(vix_value, 2),
            indicators=indicators,
            summary_insight=summary,
            data_quality_note="; ".join(data_notes),
        )

    @staticmethod
    def _close_frame(data: pd.DataFrame) -> pd.DataFrame:
        if data.empty or "Close" not in data:
            raise ValueError("Yahoo Finance response has no daily Close data.")
        close_data = data["Close"]
        if isinstance(close_data, pd.Series):
            return close_data.to_frame()
        if close_data.empty:
            raise ValueError("Yahoo Finance response has no closing prices.")
        return close_data

    def _interpret_indicator(
        self,
        symbol: str,
        current: float,
        change_1m: float,
    ) -> Tuple[str, str]:
        if symbol == "^TNX":
            if current > 4.4:
                return "금리 상승은 성장주 밸류에이션 부담 요인이 될 수 있음", "금리 상승"
            return "금리 수준을 다른 시장 지표와 함께 관찰", "금리 관찰"
        if symbol == "^VIX":
            if current > 25:
                return "시장 변동성이 높은 구간", "변동성 높음"
            return "변동성 지표의 최근 흐름 관찰", "변동성 관찰"
        if symbol == "DX-Y.NYB":
            if current > 105:
                return "달러 강세는 글로벌 유동성에 부담이 될 수 있음", "달러 강세"
            return "달러 지표의 최근 흐름 관찰", "달러 관찰"
        if symbol in {"^NDX", "^GSPC"}:
            direction = "상승" if change_1m > 0 else "하락" if change_1m < 0 else "보합"
            return f"1개월 지수 흐름은 {direction}", f"월간 {direction}"
        return "정상 범위 내 등락", "중립"
