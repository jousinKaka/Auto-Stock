"""Agent 5: Semiconductor sector technical analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import yfinance as yf
from pydantic import BaseModel

from services.market_data import extract_daily_close_series


class SemiCycleSnapshot(BaseModel):
    timestamp: str
    data_as_of: Optional[str] = None
    data_source: str = "Yahoo Finance"
    soxx_price: Optional[float] = None
    soxx_change_1d_pct: Optional[float] = None
    soxx_change_1m_pct: Optional[float] = None
    rsi_14: Optional[float] = None
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    trend_state: str = "DATA_UNAVAILABLE"
    cycle_phase: str = "NOT_ASSESSED"
    industry_insight: str = ""
    data_quality_note: str = ""

    def to_markdown(self) -> str:
        lines = [
            (
                f"- **데이터 출처/조회 시각**: [{self.data_source} SOXX 이력]"
                "(https://finance.yahoo.com/quote/SOXX/history/) / "
                f"{self.timestamp}"
            ),
        ]
        if self.data_as_of:
            lines.append(f"- **기준 거래일**: {self.data_as_of} (미국 동부시간 기준 완료 일봉)")
        if self.soxx_price is None:
            lines.extend([
                "- **반도체 업황 기술 지표**: 산출 불가",
                f"- **데이터 상태**: {self.data_quality_note or '유효한 가격 데이터가 없습니다.'}",
            ])
            return "\n".join(lines)

        price_change = (
            f"{self.soxx_change_1d_pct:+.2f}% / 1개월 {self.soxx_change_1m_pct:+.2f}%"
            if self.soxx_change_1d_pct is not None and self.soxx_change_1m_pct is not None
            else "등락률 산출 불가"
        )
        rsi_text = f"{self.rsi_14:.1f}" if self.rsi_14 is not None else "N/A"
        moving_averages = " | ".join(
            f"{label} ${value:.2f}" if value is not None else f"{label} N/A"
            for label, value in (
                ("20일선", self.sma_20),
                ("50일선", self.sma_50),
                ("200일선", self.sma_200),
            )
        )
        lines.extend([
            f"- **반도체 지수(SOXX)**: ${self.soxx_price:.2f} ({price_change})",
            f"- **기술적 추세**: `{self.trend_state}` | **RSI(14)**: {rsi_text}",
            f"- **이동평균**: {moving_averages}",
            f"- **해석**: {self.industry_insight}",
            "- **범위**: SOXX 가격 기반 업종 기술 지표이며 기업 실적·수요·SOXL 주문 신호를 의미하지 않습니다.",
        ])
        if self.data_quality_note:
            lines.append(f"- **데이터 참고**: {self.data_quality_note}")
        return "\n".join(lines)


class SemiIndustryAgent:
    """Summarize completed SOXX daily-price indicators without inferring trade signals."""

    def analyze(self) -> SemiCycleSnapshot:
        now = datetime.now(ZoneInfo("America/New_York"))
        timestamp = now.isoformat(timespec="seconds")
        try:
            data = yf.download("SOXX", period="1y", interval="1d", progress=False)
            closes = extract_daily_close_series(data, limit=None, now=now)
        except Exception as exc:
            return SemiCycleSnapshot(
                timestamp=timestamp,
                data_quality_note=f"Yahoo Finance SOXX 가격 조회 실패: {type(exc).__name__}: {exc}",
            )

        if len(closes) < 200:
            return SemiCycleSnapshot(
                timestamp=timestamp,
                data_as_of=closes.index[-1].strftime("%Y-%m-%d"),
                data_quality_note=(
                    f"완료된 SOXX 일봉이 {len(closes)}개라 200일 추세를 계산하지 않았습니다 "
                    "(최소 200개 필요)."
                ),
            )

        current = float(closes.iloc[-1])
        previous = float(closes.iloc[-2])
        month_ago = float(closes.iloc[-21])
        sma_20 = float(closes.rolling(20).mean().iloc[-1])
        sma_50 = float(closes.rolling(50).mean().iloc[-1])
        sma_200 = float(closes.rolling(200).mean().iloc[-1])

        delta = closes.diff()
        avg_gain = float(delta.clip(lower=0).rolling(14).mean().iloc[-1])
        avg_loss = float(-delta.clip(upper=0).rolling(14).mean().iloc[-1])
        if avg_loss == 0:
            rsi = 100.0 if avg_gain > 0 else 50.0
        else:
            rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

        trend_state, insight = self._classify_trend(
            current,
            sma_20,
            sma_50,
            sma_200,
        )
        return SemiCycleSnapshot(
            timestamp=timestamp,
            data_as_of=closes.index[-1].strftime("%Y-%m-%d"),
            soxx_price=round(current, 2),
            soxx_change_1d_pct=round((current - previous) / previous * 100.0, 2),
            soxx_change_1m_pct=round((current - month_ago) / month_ago * 100.0, 2),
            rsi_14=round(rsi, 1),
            sma_20=round(sma_20, 2),
            sma_50=round(sma_50, 2),
            sma_200=round(sma_200, 2),
            trend_state=trend_state,
            cycle_phase="TECHNICAL_TREND_ONLY",
            industry_insight=insight,
        )

    @staticmethod
    def _classify_trend(
        price: float,
        sma_20: float,
        sma_50: float,
        sma_200: float,
    ) -> tuple[str, str]:
        if price >= sma_20 >= sma_50 >= sma_200:
            return "BULLISH_EXPANSION", "가격과 이동평균이 장·중·단기 순으로 정렬된 상태입니다."
        if price < sma_20 and price >= sma_50 >= sma_200:
            return "HEALTHY_PULLBACK", "장기·중기 이동평균 위에서 가격이 20일선 아래에 있습니다."
        if price < sma_50 and price >= sma_200:
            return "DEEP_CORRECTION", "가격이 50일선 아래, 200일선 위에 있습니다."
        if price < sma_20 < sma_50 < sma_200:
            return "BEAR_CYCLE", "가격과 이동평균이 장·중·단기 역순으로 정렬된 상태입니다."
        return "MIXED_TREND", "가격과 이동평균 배열이 단일 추세 구간으로 정렬되지 않았습니다."
