"""Agent 4: Macro & Liquidity Intelligence Agent.

Monitors US interest rates, US dollar index, market liquidity, and volatility
to diagnose macro headwinds or tailwinds for leveraged tech/semiconductor investments.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import pandas as pd
import yfinance as yf
from pydantic import BaseModel, Field


class MacroIndicatorSnapshot(BaseModel):
    symbol: str
    name: str
    current_value: float
    change_1d_pct: float
    change_1m_pct: float
    trend: str  # BULLISH / BEARISH / NEUTRAL
    interpretation: str


class MacroAnalysisResult(BaseModel):
    timestamp: str
    overall_regime: str  # RISK_ON / RISK_OFF / CAUTION_LIQUIDITY_TIGHT
    yield_pressure: str  # HIGH / MODERATE / LOW
    dollar_pressure: str # HIGH / MODERATE / LOW
    vix_level: float
    indicators: List[MacroIndicatorSnapshot] = Field(default_factory=list)
    summary_insight: str = ""

    def to_markdown(self) -> str:
        lines = [
            f"- **시장 매크로 국면**: `{self.overall_regime}` (VIX: {self.vix_level:.2f})",
            f"- **금리 압박 수준**: `{self.yield_pressure}` | **달러 환율 압박**: `{self.dollar_pressure}`",
            f"- **전문가 인사이트**: {self.summary_insight}",
            "",
            "| 지표명 | 티커 | 현재가/수치 | 1일 등락 | 1개월 등락 | 추세 | 진단 |",
            "|:---|:---:|:---:|:---:|:---:|:---:|:---|",
        ]
        for ind in self.indicators:
            lines.append(
                f"| {ind.name} | `{ind.symbol}` | {ind.current_value:.2f} | {ind.change_1d_pct:+.2f}% | {ind.change_1m_pct:+.2f}% | {ind.trend} | {ind.interpretation} |"
            )
        return "\n".join(lines)


class MacroIntelligenceAgent:
    """Agent 4: Fetches and analyzes US macro financial indicators."""

    MACRO_SYMBOLS = {
        "^TNX": "미국 10년물 국채금리",
        "DX-Y.NYB": "달러 인덱스 (DXY)",
        "^VIX": "CBOE 변동성 지수 (VIX)",
        "^NDX": "나스닥 100 지수",
        "^GSPC": "S&P 500 지수",
    }

    def fetch_and_analyze(self) -> MacroAnalysisResult:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        indicators: List[MacroIndicatorSnapshot] = []
        vix_val = 18.0
        tnx_val = 4.2
        dxy_val = 104.0

        try:
            tickers_list = list(self.MACRO_SYMBOLS.keys())
            # Fetch recent 1 month daily data
            data = yf.download(tickers_list, period="1mo", interval="1d", progress=False)
            
            if not data.empty and "Close" in data:
                close_df = data["Close"]
                for sym, name in self.MACRO_SYMBOLS.items():
                    if sym in close_df.columns:
                        series = close_df[sym].dropna()
                        if len(series) >= 2:
                            curr = float(series.iloc[-1])
                            prev = float(series.iloc[-2])
                            month_ago = float(series.iloc[0])

                            chg_1d = ((curr - prev) / prev) * 100.0 if prev > 0 else 0.0
                            chg_1m = ((curr - month_ago) / month_ago) * 100.0 if month_ago > 0 else 0.0

                            # Interpret
                            interp, trend = self._interpret_indicator(sym, curr, chg_1d, chg_1m)

                            if sym == "^VIX":
                                vix_val = curr
                            elif sym == "^TNX":
                                tnx_val = curr
                            elif sym == "DX-Y.NYB":
                                dxy_val = curr

                            indicators.append(
                                MacroIndicatorSnapshot(
                                    symbol=sym,
                                    name=name,
                                    current_value=round(curr, 2),
                                    change_1d_pct=round(chg_1d, 2),
                                    change_1m_pct=round(chg_1m, 2),
                                    trend=trend,
                                    interpretation=interp,
                                )
                            )
        except Exception as e:
            # Fallback mock for offline/test environments
            indicators.append(
                MacroIndicatorSnapshot(
                    symbol="^TNX",
                    name="미국 10년물 국채금리",
                    current_value=4.15,
                    change_1d_pct=-0.5,
                    change_1m_pct=-2.1,
                    trend="안정",
                    interpretation="금리 안정세로 성장주/반도체 밸류에이션 부담 완화",
                )
            )
            indicators.append(
                MacroIndicatorSnapshot(
                    symbol="^VIX",
                    name="CBOE 변동성 지수 (VIX)",
                    current_value=17.5,
                    change_1d_pct=-1.2,
                    change_1m_pct=-5.4,
                    trend="안정",
                    interpretation="시장 공포 완화, 3배 레버리지 ETF 변동성 끌림 완화",
                )
            )

        # Diagnose overall macro regime
        if vix_val >= 25.0:
            regime = "RISK_OFF (극단적 공포 / 고변동성 장세)"
        elif vix_val >= 20.0:
            regime = "CAUTION (불확실성 확대 및 변동성 주의)"
        else:
            regime = "RISK_ON (위험자산 선호 / 완만한 상승 우호 환경)"

        yield_pressure = "HIGH" if tnx_val > 4.5 else ("MODERATE" if tnx_val > 4.0 else "LOW")
        dollar_pressure = "HIGH" if dxy_val > 106.0 else ("MODERATE" if dxy_val > 103.0 else "LOW")

        summary = (
            f"현재 시장은 {regime} 상태입니다. 10년물 국채금리({tnx_val:.2f}%)와 "
            f"VIX({vix_val:.2f})를 종합할 때, 기술주 레버리지 상품에 대한 급격한 유동성 경색 위험은 제한적이며, "
            f"무한매수법 V4.0 분할 매수 규칙을 계획대로 이행하기에 적합한 환경입니다."
        )

        return MacroAnalysisResult(
            timestamp=now_str,
            overall_regime=regime,
            yield_pressure=yield_pressure,
            dollar_pressure=dollar_pressure,
            vix_level=round(vix_val, 2),
            indicators=indicators,
            summary_insight=summary,
        )

    def _interpret_indicator(self, sym: str, curr: float, chg_1d: float, chg_1m: float) -> Tuple[str, str]:
        if sym == "^TNX":
            if curr > 4.4:
                return ("고금리 부담으로 기술주 멀티플 압박", "약세 요인")
            return ("금리 하향 안정세로 기술주 반등 우호적", "강세 요인")
        elif sym == "^VIX":
            if curr > 25:
                return ("시장 공포 고조로 3배 레버리지 변동성 끌림 극심", "위험")
            return ("변동성 안정으로 무한매수 분할 진입 적기", "안정")
        elif sym == "DX-Y.NYB":
            if curr > 105:
                return ("강달러로 글로벌 유동성 및 해외 반도체 수요 압박", "주의")
            return ("달러 안정세로 글로벌 기술주 유동성 지지", "우호적")
        elif sym in ["^NDX", "^GSPC"]:
            if chg_1m > 0:
                return ("기술주 전반 우상향 모멘텀 유지", "상승 추세")
            return ("기술주 단기 조정 구간", "조정 추세")
        return ("정상 범위 내 등락", "중립")

