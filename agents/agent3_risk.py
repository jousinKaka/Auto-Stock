"""Agent 3: Risk Watchdog & Leverage Volatility Monitor.

Monitors 3x leverage decay, cash exhaustion trajectory, drawdown limits,
and issues early warnings for SOXL infinite buying execution.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from agents.agent2_strategy import StrategyState, StrategyMode


class RiskLevel(str, Enum):
    GREEN = "GREEN"     # Safe, initial/mid progression
    YELLOW = "YELLOW"   # Caution, 2nd half / elevated volatility
    ORANGE = "ORANGE"   # High Risk, near exhaustion
    RED = "RED"         # Critical, Reverse Mode active / extreme decay


class RiskAssessment(BaseModel):
    """Structured risk diagnostics for the current portfolio."""
    timestamp: str
    ticker: str
    risk_level: RiskLevel
    
    # Progress & Runway
    T_value: float
    max_divisions: int
    cash_burn_pct: float
    days_cash_runway: int
    one_unit_cash: float
    
    # Drawdown & Exposure
    unrealized_pnl_pct: float
    current_mode: StrategyMode
    
    # Volatility & Leverage Metrics
    annualized_volatility_pct: float
    volatility_drag_annual_est_pct: float
    
    # Actionable Warning / Recommendation
    headline: str
    warnings: List[str] = Field(default_factory=list)
    action_items: List[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        """Format assessment into readable markdown alert."""
        icon = {
            RiskLevel.GREEN: "🟢 [안전/정상]",
            RiskLevel.YELLOW: "🟡 [주의/후반전]",
            RiskLevel.ORANGE: "🟠 [경고/소진임박]",
            RiskLevel.RED: "🔴 [위험/리버스모드]",
        }[self.risk_level]

        lines = [
            f"**위험 등급**: {icon} - {self.headline}",
            f"- **현금 소진율**: {self.cash_burn_pct:.1f}% | **잔여 버퍼**: 약 {self.days_cash_runway}회분 매수 가능",
            f"- **평단 대비 손익**: {self.unrealized_pnl_pct:+.2f}% | **SOXL 20일 변동성**: {self.annualized_volatility_pct:.1f}%",
            f"- **추정 변동성 끌림(Decay)**: 연 약 -{self.volatility_drag_annual_est_pct:.1f}% (횡보장 손실 요인)",
        ]
        if self.warnings:
            lines.append("- **주의 사항**:")
            for w in self.warnings:
                lines.append(f"  - ⚠️ {w}")
        if self.action_items:
            lines.append("- **대응 지침**:")
            for a in self.action_items:
                lines.append(f"  - 🎯 {a}")
        return "\n".join(lines)


class RiskWatchdogAgent:
    """Agent 3: Evaluates risk metrics and guards against catastrophic leverage risk."""

    def __init__(self, target_divisions: int = 40):
        self.target_divisions = target_divisions

    def assess_risk(
        self,
        state: StrategyState,
        curr_price: float,
        recent_daily_closes: Optional[List[float]] = None,
    ) -> RiskAssessment:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Runway & Cash Burn
        initial_cap = max(1.0, state.initial_budget)
        cash_burn_pct = max(0.0, min(100.0, (1.0 - (state.remaining_cash / initial_cap)) * 100.0))
        divisor = max(1.0, float(state.divisions) - state.T)
        one_unit_cash = round(state.remaining_cash / divisor, 2) if divisor > 0 else 0.0
        days_cash_runway = int(state.remaining_cash // one_unit_cash) if one_unit_cash > 0 else 0

        # 2. Drawdown
        if state.avg_price > 0:
            unrealized_pnl_pct = ((curr_price - state.avg_price) / state.avg_price) * 100.0
        else:
            unrealized_pnl_pct = 0.0

        # 3. Volatility & Decay Estimation
        ann_vol = 75.0 # Default ~75% for SOXL 3x
        if recent_daily_closes and len(recent_daily_closes) >= 15:
            arr = np.array(recent_daily_closes)
            returns = np.diff(arr) / arr[:-1]
            ann_vol = float(np.std(returns) * np.sqrt(252) * 100.0)

        # 3x ETF Volatility Drag approx: 0.5 * beta * (beta - 1) * sigma_1x^2
        # where sigma_1x approx ann_vol / 3
        sigma_1x = (ann_vol / 100.0) / 3.0
        vol_drag_pct = round(3.0 * (sigma_1x ** 2) * 100.0, 1)

        # 4. Determine Risk Level & Alerts
        warnings: List[str] = []
        action_items: List[str] = []

        if state.mode == StrategyMode.REVERSE:
            risk_level = RiskLevel.RED
            headline = "원금 소진으로 리버스 모드(방어 매매)가 가동 중입니다."
            warnings.append("현금이 거의 소진되어 5일 이평선 기준 쿼터 매도 및 매수가 실행됩니다.")
            warnings.append(f"복귀 기준 가격: 평단가 ${state.avg_price:.2f} 대비 -20%인 ${(state.avg_price * 0.8):.2f} 상회 필요.")
            action_items.append("임의로 추가 매수하지 마시고 리버스 모드 규칙을 기계적으로 유지하세요.")
            action_items.append("원금 추가 투입이 필요하다면 반드시 세션을 재설정(원금 증액 기록)해야 합니다.")

        elif state.T >= 35 or days_cash_runway <= 5:
            risk_level = RiskLevel.ORANGE
            headline = f"현금 소진 임박 (T값 {state.T:.1f}/{state.divisions}) 경고 단계입니다."
            warnings.append(f"잔여 매수 가능 횟수가 {days_cash_runway}회에 불과합니다.")
            warnings.append("하락이 지속될 경우 수일 내 리버스 모드로 자동 전환될 수 있습니다.")
            action_items.append("하방 사다리 매수가 잦아질 수 있으므로 달러 예수금 부족 여부를 매일 점검하세요.")
            action_items.append("지정가 익절이 오지 않더라도 별지점 쿼터 매도로 현금이 회수되는지 모니터링하세요.")

        elif state.T >= (state.divisions / 2.0) or unrealized_pnl_pct <= -20.0:
            risk_level = RiskLevel.YELLOW
            headline = f"후반전(T값 {state.T:.1f}/{state.divisions}) 진입 - 현금 보존 및 탈출 전략 집중 구간입니다."
            warnings.append("별%가 마이너스로 전환되어 별지점이 평단가보다 낮아졌습니다.")
            warnings.append("쿼터 매도가 원금 손실 구간에서 발생할 수 있으나, 이는 현금 생존을 위한 필수 규칙입니다.")
            action_items.append("후반전에는 별지점 1곳에만 1회분을 집중하여 평단가를 최대한 낮추세요.")

        else:
            risk_level = RiskLevel.GREEN
            headline = f"전반전(T값 {state.T:.1f}/{state.divisions}) 정상 분할 매수 진행 중입니다."
            action_items.append("전일 종가 대비 LOC 매수 및 익절 지정가를 규칙대로 입력해 두세요.")

        return RiskAssessment(
            timestamp=now_str,
            ticker=state.ticker,
            risk_level=risk_level,
            T_value=round(state.T, 2),
            max_divisions=state.divisions,
            cash_burn_pct=round(cash_burn_pct, 1),
            days_cash_runway=days_cash_runway,
            one_unit_cash=one_unit_cash,
            unrealized_pnl_pct=round(unrealized_pnl_pct, 2),
            current_mode=state.mode,
            annualized_volatility_pct=round(ann_vol, 1),
            volatility_drag_annual_est_pct=vol_drag_pct,
            headline=headline,
            warnings=warnings,
            action_items=action_items,
        )

