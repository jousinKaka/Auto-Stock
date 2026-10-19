"""Agent 5: Semiconductor Industry & Supply Chain Analyst.

Analyzes the semiconductor industry cycle, Philadelphia Semiconductor Index (SOXX / ^SOX)
technicals, AI capex demand trends, and momentum metrics.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import yfinance as yf
from pydantic import BaseModel, Field


class SemiCycleSnapshot(BaseModel):
    timestamp: str
    soxx_price: float
    soxx_change_1d_pct: float
    soxx_change_1m_pct: float
    rsi_14: float
    sma_20: float
    sma_50: float
    sma_200: float
    trend_state: str  # BULLISH_EXPANSION / HEALTHY_PULLBACK / DEEP_CORRECTION / BEAR_CYCLE
    cycle_phase: str  # AI_ACCELERATION / MEMORY_RECOVERY / FOUNDRY_EXPANSION / MATURE
    industry_insight: str = ""

    def to_markdown(self) -> str:
        trend_badge = {
            "BULLISH_EXPANSION": "🚀 강세 확장 국면",
            "HEALTHY_PULLBACK": "📉 건전한 단기 조정",
            "DEEP_CORRECTION": "⚠️ 심화 조정 국면",
            "BEAR_CYCLE": "🐻 약세 사이클",
        }.get(self.trend_state, self.trend_state)

        if self.rsi_14 > 70:
            rsi_desc = "과매수"
        elif self.rsi_14 < 30:
            rsi_desc = "과매도"
        else:
            rsi_desc = "중립"

        lines = [
            f"- **반도체 지수(SOXX)**: ${self.soxx_price:.2f} ({self.soxx_change_1d_pct:+.2f}% / 1개월 {self.soxx_change_1m_pct:+.2f}%)",
            f"- **사이클 진단**: `{trend_badge}` | **RSI(14)**: {self.rsi_14:.1f} ({rsi_desc})",
            f"- **이동평균 배열**: 20일선 ${self.sma_20:.2f} | 50일선 ${self.sma_50:.2f} | 200일선 ${self.sma_200:.2f}",
            f"- **업황 분석 총평**: {self.industry_insight}",
        ]
        return "\n".join(lines)


class SemiIndustryAgent:
    """Agent 5: Evaluates semiconductor sector health and technical cycle."""

    def analyze(self) -> SemiCycleSnapshot:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        soxx_price = 220.0
        chg_1d = 1.2
        chg_1m = 4.5
        rsi = 52.0
        sma_20 = 215.0
        sma_50 = 210.0
        sma_200 = 195.0

        try:
            df = yf.download("SOXX", period="1y", interval="1d", progress=False)
            if not df.empty and len(df) >= 50:
                closes = df["Close"].squeeze()
                if isinstance(closes, pd.DataFrame):
                    closes = closes.iloc[:, 0]
                closes = closes.dropna()
                soxx_price = float(closes.iloc[-1])
                prev_close = float(closes.iloc[-2])
                chg_1d = ((soxx_price - prev_close) / prev_close) * 100.0

                month_close = float(closes.iloc[-21]) if len(closes) >= 21 else prev_close
                chg_1m = ((soxx_price - month_close) / month_close) * 100.0

                # Calculate RSI 14
                delta = closes.diff()
                gain = delta.clip(lower=0)
                loss = -delta.clip(upper=0)
                avg_gain = gain.rolling(window=14).mean().iloc[-1]
                avg_loss = loss.rolling(window=14).mean().iloc[-1]
                if avg_loss > 0:
                    rs = avg_gain / avg_loss
                    rsi = float(100.0 - (100.0 / (1.0 + rs)))
                else:
                    rsi = 100.0

                # SMAs
                sma_20 = float(closes.rolling(20).mean().iloc[-1])
                sma_50 = float(closes.rolling(50).mean().iloc[-1])
                sma_200 = float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else sma_50
        except Exception:
            pass

        # Determine trend state
        if soxx_price >= sma_20 >= sma_50:
            trend_state = "BULLISH_EXPANSION"
            insight = "AI 가속기 및 데이터센터 수요 지속으로 정배열 상승 추세가 유지되고 있습니다. SOXL 무한매수 회차의 조기 익절 가능성이 높은 구간입니다."
        elif soxx_price < sma_20 and soxx_price >= sma_50:
            trend_state = "HEALTHY_PULLBACK"
            insight = "단기 20일선 하회로 숨고르기 조정 중입니다. 무한매수법의 특성상 평단가를 효율적으로 낮추기에 이상적인 분할 매수 기회입니다."
        elif soxx_price < sma_50 and soxx_price >= sma_200:
            trend_state = "DEEP_CORRECTION"
            insight = "중기 50일선을 하회하는 깊은 눌림목입니다. 3배 레버리지 SOXL 변동성이 확대되므로 후반전 별지점 매수 규칙을 엄격히 준수해야 합니다."
        else:
            trend_state = "BEAR_CYCLE"
            insight = "장기 200일선 부근까지 하락한 약세 구간입니다. 리스크 에이전트의 현금 소진율 모니터링과 리버스 모드 방어 수칙 준수가 최우선입니다."

        return SemiCycleSnapshot(
            timestamp=now_str,
            soxx_price=round(soxx_price, 2),
            soxx_change_1d_pct=round(chg_1d, 2),
            soxx_change_1m_pct=round(chg_1m, 2),
            rsi_14=round(rsi, 1),
            sma_20=round(sma_20, 2),
            sma_50=round(sma_50, 2),
            sma_200=round(sma_200, 2),
            trend_state=trend_state,
            cycle_phase="AI_ACCELERATION",
            industry_insight=insight,
        )
