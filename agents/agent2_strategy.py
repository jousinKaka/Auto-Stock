"""Agent 2: Infinite Strategy Engine & Backtester.

Implements Laoer's Infinite Buying Method V4.0 (라오어 무한매수법 V4.0)
specifically tailored for 3x leveraged semiconductor ETF (SOXL).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from config.settings import settings


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LOC = "LOC"       # Limit On Close (장마감 종가 체결)
    LIMIT = "LIMIT"   # Standard Limit Order (지정가)
    MARKET = "MARKET" # Market Order (시장가/MOC)


class StrategyMode(str, Enum):
    NORMAL = "NORMAL"
    REVERSE = "REVERSE"


class OrderLine(BaseModel):
    """Individual order instruction."""
    side: OrderSide
    order_type: OrderType
    price: float = Field(..., description="Order price in USD (2 decimal places)")
    quantity: int = Field(..., description="Number of shares to trade")
    tag: str = Field(..., description="Semantic purpose tag (e.g. STAR_BUY, QUARTER_SELL)")
    description: str = Field(default="", description="Human readable explanation")

    @property
    def estimated_total(self) -> float:
        return round(self.price * self.quantity, 2)


class StrategyState(BaseModel):
    """Persistent state of the infinite buying strategy."""
    ticker: str = "SOXL"
    cycle: int = 1
    divisions: int = 40
    target_profit_rate: float = 0.20  # 20%
    large_number_ratio: float = 1.12  # +12% cap
    
    T: float = 0.0
    avg_price: float = 0.0
    total_shares: int = 0
    remaining_cash: float = 10000.0
    initial_budget: float = 10000.0
    excluded_cash: float = 0.0
    
    mode: StrategyMode = StrategyMode.NORMAL
    reverse_day: int = 0
    updated_at: datetime = Field(default_factory=datetime.now)

    @property
    def total_invested(self) -> float:
        return round(self.avg_price * self.total_shares, 2)

    @property
    def total_equity(self) -> float:
        return round(self.remaining_cash + self.total_invested, 2)

    @property
    def is_first_half(self) -> bool:
        return self.T < (self.divisions / 2.0)


class OrderPlan(BaseModel):
    """Daily execution order sheet generated for broker submission."""
    date: str
    ticker: str
    state_snapshot: StrategyState
    prev_close: float
    one_unit_cash: float
    star_pct: float
    star_price: float
    target_sell_price: float
    orders: List[OrderLine] = Field(default_factory=list)

    def to_markdown_table(self) -> str:
        """Render order sheet as a clean markdown table."""
        lines = [
            f"### [오늘의 무한매수 주문표] {self.ticker} ({self.date})",
            f"- **진행 상태**: 회차 #{self.state_snapshot.cycle} | **T값**: {self.state_snapshot.T:.2f} / {self.state_snapshot.divisions} ({'전반전' if self.state_snapshot.is_first_half else '후반전'}) | **모드**: {self.state_snapshot.mode.value}",
            f"- **보유 현황**: {self.state_snapshot.total_shares}주 @ ${self.state_snapshot.avg_price:.2f} (평가액: ${self.state_snapshot.total_invested:.2f})",
            f"- **회차 시작 원금**: ${self.state_snapshot.initial_budget:.2f} | **남은 현금**: ${self.state_snapshot.remaining_cash:.2f} | **1회 매수금**: ${self.one_unit_cash:.2f}",
            f"- **기준 가격**: 전일종가 ${self.prev_close:.2f} | 별% {self.star_pct*100:+.2f}% | **별지점** ${self.star_price:.2f} | **익절목표** ${self.target_sell_price:.2f}",
            "",
            "| 구분 | 주문유형 | 가격($) | 수량 | 예상금액($) | 설명/목적 |",
            "|:---|:---:|:---:|:---:|:---:|:---|",
        ]
        for o in self.orders:
            side_badge = "🔴 매수" if o.side == OrderSide.BUY else "🔵 매도"
            lines.append(
                f"| {side_badge} | `{o.order_type.value}` | ${o.price:.2f} | {o.quantity}주 | ${o.estimated_total:.2f} | {o.description} |"
            )
        return "\n".join(lines)


class InfiniteStrategyAgent:
    """Agent 2: Infinite Buying Strategy Engine V4.0."""

    def __init__(self, state: Optional[StrategyState] = None):
        if state is None:
            self.state = StrategyState(
                ticker=settings.target_ticker,
                divisions=settings.divisions,
                target_profit_rate=settings.target_profit_rate,
                large_number_ratio=settings.large_number_ratio,
                remaining_cash=settings.total_budget_usd,
                initial_budget=settings.total_budget_usd,
            )
        else:
            self.state = state

    def calculate_one_unit_cash(self) -> float:
        """1회 매수금 = 남은 현금 ÷ (분할수 - T값)."""
        divisor = max(1.0, float(self.state.divisions) - self.state.T)
        return round(self.state.remaining_cash / divisor, 2)

    def calculate_star_pct(self) -> float:
        """별% = 목표% × (1 - (2 × T / 분할수))."""
        ratio = (2.0 * self.state.T) / float(self.state.divisions)
        return self.state.target_profit_rate * (1.0 - ratio)

    def calculate_star_price(self, prev_close: float) -> float:
        """별지점 = 평단가 × (1 + 별%). 평단가가 0이면 전일종가 기준."""
        base_price = self.state.avg_price if self.state.avg_price > 0 else prev_close
        star_pct = self.calculate_star_pct()
        return round(base_price * (1.0 + star_pct), 2)

    def calculate_target_sell_price(self) -> float:
        """목표 지정가 매도 = 평단가 × (1 + 목표수익률)."""
        if self.state.avg_price <= 0:
            return 0.0
        return round(self.state.avg_price * (1.0 + self.state.target_profit_rate), 2)

    def check_and_update_mode(self, prev_close: float) -> StrategyMode:
        """Check for exhaustion transition to Reverse Mode or recovery to Normal Mode."""
        # Check Recovery from Reverse Mode:
        # Close > Avg Price * (1 - target_profit_rate) (e.g. -20% from avg price)
        if self.state.mode == StrategyMode.REVERSE:
            recovery_threshold = self.state.avg_price * (1.0 - self.state.target_profit_rate)
            if prev_close > recovery_threshold:
                self.state.mode = StrategyMode.NORMAL
                self.state.reverse_day = 0
            else:
                self.state.reverse_day += 1
            return self.state.mode

        # Check Exhaustion to Reverse Mode:
        # T >= divisions - 1 AND remaining cash cannot afford 1 share of 1 unit
        if self.state.T >= (self.state.divisions - 1):
            one_unit = self.calculate_one_unit_cash()
            if self.state.remaining_cash < prev_close or one_unit < prev_close:
                self.state.mode = StrategyMode.REVERSE
                self.state.reverse_day = 1
        return self.state.mode

    def generate_daily_orders(
        self,
        prev_close: float,
        recent_5d_closes: Optional[List[float]] = None,
        date_str: Optional[str] = None,
    ) -> OrderPlan:
        """Generate full order sheet for today's market session."""
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")

        # Update mode (Normal vs Reverse)
        self.check_and_update_mode(prev_close)

        one_unit_cash = self.calculate_one_unit_cash()
        star_pct = self.calculate_star_pct()
        star_price = self.calculate_star_price(prev_close)
        target_sell_price = self.calculate_target_sell_price()

        orders: List[OrderLine] = []

        def append_ladder_orders(levels: int, base_quantity: int, ceiling_price: float) -> None:
            base_quantity = max(base_quantity, int(one_unit_cash // ceiling_price), 1)
            for i in range(1, levels + 1):
                ladder_price = math.floor((one_unit_cash / (base_quantity + i)) * 100) / 100.0
                if ladder_price > 0:
                    orders.append(
                        OrderLine(
                            side=OrderSide.BUY,
                            order_type=OrderType.LOC,
                            price=ladder_price,
                            quantity=1,
                            tag=f"LADDER_BUY_{i}",
                            description=f"급락 대비 하방 사다리 매수 {i}차 (1주)",
                        )
                    )

        # ==========================================
        # 1. SELL ORDERS (지정가 및 쿼터매도)
        # ==========================================
        if self.state.total_shares > 0 and self.state.mode == StrategyMode.NORMAL:
            quarter_qty = self.state.total_shares // 4
            target_limit_qty = self.state.total_shares - quarter_qty

            # (1) Quarter Sell (LOC): Sell 1/4 shares if close >= star_price
            if quarter_qty > 0:
                orders.append(
                    OrderLine(
                        side=OrderSide.SELL,
                        order_type=OrderType.LOC,
                        price=star_price,
                        quantity=quarter_qty,
                        tag="QUARTER_SELL_LOC",
                        description=f"보유량 1/4 쿼터매도 (종가가 별지점 ${star_price:.2f} 이상 시 체결, T×0.75)",
                    )
                )

            # (2) Target Profit Sell (LIMIT): Sell 3/4 shares at target profit price
            if target_limit_qty > 0 and target_sell_price > 0:
                orders.append(
                    OrderLine(
                        side=OrderSide.SELL,
                        order_type=OrderType.LIMIT,
                        price=target_sell_price,
                        quantity=target_limit_qty,
                        tag="TARGET_SELL_LIMIT",
                        description=f"보유량 3/4 익절지정가 (평단가 대비 +{self.state.target_profit_rate*100:.0f}%, T×0.25)",
                    )
                )

        # ==========================================
        # 2. BUY ORDERS (NORMAL MODE)
        # ==========================================
        if self.state.mode == StrategyMode.NORMAL:
            large_number_cap = round(prev_close * self.state.large_number_ratio, 2)

            # --- Case A: First Day / Fresh Cycle (T == 0 or total_shares == 0) ---
            if self.state.total_shares == 0 or self.state.T == 0.0:
                first_buy_price = min(large_number_cap, round(prev_close * 1.12, 2))
                qty = int(one_unit_cash // first_buy_price)
                if qty == 0 and one_unit_cash >= prev_close:
                    qty = 1
                if qty > 0:
                    orders.append(
                        OrderLine(
                            side=OrderSide.BUY,
                            order_type=OrderType.LOC,
                            price=first_buy_price,
                            quantity=qty,
                            tag="FIRST_BUY_LARGE_NUMBER",
                            description=f"첫 사이클 1회분 큰수 매수 (전일종가 대비 +12% 상한 LOC)",
                        )
                    )

                append_ladder_orders(settings.ladder_levels_front, qty, first_buy_price)

            # --- Case B: First Half (T < divisions / 2) ---
            elif self.state.is_first_half:
                # 1) Star Buy: 1/2 cash at (star_price - 0.01)
                star_buy_price = round(star_price - 0.01, 2)
                half_cash = one_unit_cash / 2.0
                star_buy_qty = int(half_cash // star_buy_price) if star_buy_price > 0 else 0

                # 2) Avg Price Buy: 1/2 cash at avg_price
                avg_buy_price = round(self.state.avg_price, 2)
                full_unit_qty = int(one_unit_cash // avg_buy_price) if avg_buy_price > 0 else 0
                avg_buy_qty = max(0, full_unit_qty - star_buy_qty)

                ladder_ceiling_price = min(
                    price for price, quantity in (
                        (star_buy_price, star_buy_qty),
                        (avg_buy_price, avg_buy_qty),
                    ) if quantity > 0
                ) if star_buy_qty > 0 or avg_buy_qty > 0 else large_number_cap

                # Check Large Number condition: if star_buy_price exceeds cap, merge into cap
                if star_buy_price > large_number_cap:
                    merged_qty = int(one_unit_cash // large_number_cap)
                    if merged_qty > 0:
                        ladder_ceiling_price = large_number_cap
                        orders.append(
                            OrderLine(
                                side=OrderSide.BUY,
                                order_type=OrderType.LOC,
                                price=large_number_cap,
                                quantity=merged_qty,
                                tag="MERGED_LARGE_NUMBER_BUY",
                                description=f"전반전 큰수 병합 매수 (별지점이 상한 초과, {merged_qty}주 LOC)",
                            )
                        )
                else:
                    if star_buy_qty > 0:
                        orders.append(
                            OrderLine(
                                side=OrderSide.BUY,
                                order_type=OrderType.LOC,
                                price=star_buy_price,
                                quantity=star_buy_qty,
                                tag="STAR_BUY_LOC",
                                description=f"전반전 별지점 매수 (0.5회분, 종가 ${star_buy_price:.2f} 이하 시)",
                            )
                        )
                    if avg_buy_qty > 0:
                        orders.append(
                            OrderLine(
                                side=OrderSide.BUY,
                                order_type=OrderType.LOC,
                                price=avg_buy_price,
                                quantity=avg_buy_qty,
                                tag="AVG_BUY_LOC",
                                description=f"전반전 평단가 매수 (0.5회분, 종가 ${avg_buy_price:.2f} 이하 시)",
                            )
                        )

                append_ladder_orders(
                    settings.ladder_levels_front,
                    full_unit_qty,
                    ladder_ceiling_price,
                )

            # --- Case C: Second Half (T >= divisions / 2) ---
            else:
                # Full 1-unit deployed only at Star Price (star_price <= avg_price)
                star_buy_price = round(star_price - 0.01, 2)
                if star_buy_price > large_number_cap:
                    star_buy_price = large_number_cap
                qty = int(one_unit_cash // star_buy_price) if star_buy_price > 0 else 0

                if qty > 0:
                    orders.append(
                        OrderLine(
                            side=OrderSide.BUY,
                            order_type=OrderType.LOC,
                            price=star_buy_price,
                            quantity=qty,
                            tag="SECOND_HALF_STAR_BUY",
                            description=f"후반전 별지점 전액 매수 (1회분 아껴쓰기, ${star_buy_price:.2f} LOC)",
                        )
                    )

                append_ladder_orders(settings.ladder_levels_back, qty, star_buy_price)

        # ==========================================
        # 3. REVERSE MODE (소진 모드)
        # ==========================================
        elif self.state.mode == StrategyMode.REVERSE:
            sell_divisor = max(1, self.state.divisions // 2)
            reverse_sell_qty = self.state.total_shares // sell_divisor

            # Day 1: Market on Close (MOC) sell
            if self.state.reverse_day == 1:
                if reverse_sell_qty > 0:
                    orders.append(
                        OrderLine(
                            side=OrderSide.SELL,
                            order_type=OrderType.MARKET,
                            price=prev_close,
                            quantity=reverse_sell_qty,
                            tag="REVERSE_DAY1_MOC_SELL",
                            description=f"리버스 1일차 방어 매도 (보유량의 1/{sell_divisor} 시장가 매도)",
                        )
                    )
            # Day 2 onwards: Reverse Star point using 5-day SMA
            else:
                rev_star_price = prev_close
                if recent_5d_closes and len(recent_5d_closes) >= 5:
                    rev_star_price = round(sum(recent_5d_closes[-5:]) / 5.0, 2)

                # 1) LOC Sell at Reverse Star Price
                if reverse_sell_qty > 0:
                    orders.append(
                        OrderLine(
                            side=OrderSide.SELL,
                            order_type=OrderType.LOC,
                            price=rev_star_price,
                            quantity=reverse_sell_qty,
                            tag="REVERSE_LOC_SELL",
                            description=f"리버스 5일선 방어 매도 (종가 ${rev_star_price:.2f} 이상 시)",
                        )
                    )

                # 2) LOC Quarter Buy: 1/4 of remaining cash at (Reverse Star Price - 0.01)
                quarter_cash = self.state.remaining_cash / 4.0
                buy_price = round(rev_star_price - 0.01, 2)
                buy_qty = int(quarter_cash // buy_price) if buy_price > 0 else 0
                if buy_qty > 0:
                    orders.append(
                        OrderLine(
                            side=OrderSide.BUY,
                            order_type=OrderType.LOC,
                            price=buy_price,
                            quantity=buy_qty,
                            tag="REVERSE_QUARTER_BUY",
                            description=f"리버스 쿼터 매수 (잔여금 1/4, 종가 ${buy_price:.2f} 이하 시)",
                        )
                    )

        return OrderPlan(
            date=date_str,
            ticker=self.state.ticker,
            state_snapshot=self.state.model_copy(),
            prev_close=prev_close,
            one_unit_cash=one_unit_cash,
            star_pct=star_pct,
            star_price=star_price,
            target_sell_price=target_sell_price,
            orders=orders,
        )

    def apply_execution(
        self,
        filled_buys: List[Tuple[float, int, str]],  # List of (fill_price, fill_qty, tag)
        filled_sells: List[Tuple[float, int, str]], # List of (fill_price, fill_qty, tag)
    ) -> Dict[str, Any]:
        """Apply confirmed trade executions and transition strategy state."""
        total_bought_qty = sum(qty for _, qty, _ in filled_buys)
        total_bought_cost = sum(price * qty for price, qty, _ in filled_buys)

        total_sold_qty = sum(qty for _, qty, _ in filled_sells)
        total_sold_revenue = sum(price * qty for price, qty, _ in filled_sells)
        one_unit_cash = self.calculate_one_unit_cash()

        realized_pnl = 0.0
        cycle_completed = False

        # 1. Process Sells
        if total_sold_qty > 0:
            cost_basis_sold = self.state.avg_price * total_sold_qty
            realized_pnl = total_sold_revenue - cost_basis_sold
            self.state.total_shares -= total_sold_qty
            self.state.remaining_cash += total_sold_revenue

            # Update T for sells
            for _, qty, tag in filled_sells:
                if "QUARTER_SELL" in tag:
                    self.state.T *= 0.75
                elif "TARGET_SELL" in tag:
                    self.state.T *= 0.25
                elif "REVERSE" in tag:
                    # Reverse sell decreases T slightly (× 0.95 for 40 divisions)
                    self.state.T *= (1.0 - 2.0 / float(self.state.divisions))

        # Check if cycle ended (all shares sold)
        if self.state.total_shares <= 0 and total_sold_qty > 0:
            cycle_completed = True
            self.state.cycle += 1
            self.state.T = 0.0
            self.state.avg_price = 0.0
            self.state.total_shares = 0
            self.state.mode = StrategyMode.NORMAL
            self.state.reverse_day = 0
            # 매도 대금에 손익이 이미 포함되어 있으므로 종료 현금을 새 회차 원금으로 사용합니다.
            self.state.initial_budget = round(self.state.remaining_cash, 2)
            one_unit_cash = self.calculate_one_unit_cash()

        # 2. Process Buys
        if total_bought_qty > 0:
            new_total_cost = (self.state.avg_price * self.state.total_shares) + total_bought_cost
            self.state.total_shares += total_bought_qty
            self.state.avg_price = round(new_total_cost / self.state.total_shares, 4)
            self.state.remaining_cash -= total_bought_cost

            # Update T for buys
            if one_unit_cash > 0:
                self.state.T += total_bought_cost / one_unit_cash
            else:
                self.state.T += 1.0

        self.state.updated_at = datetime.now()

        return {
            "cycle_completed": cycle_completed,
            "realized_pnl": round(realized_pnl, 2),
            "bought_shares": total_bought_qty,
            "sold_shares": total_sold_qty,
            "new_T": round(self.state.T, 2),
            "new_avg_price": round(self.state.avg_price, 2),
            "new_total_shares": self.state.total_shares,
            "new_remaining_cash": round(self.state.remaining_cash, 2),
        }


# ==========================================
# Comprehensive Infinite Backtester Engine
# ==========================================

@dataclass
class BacktestCycleResult:
    cycle_num: int
    start_date: str
    end_date: str
    duration_days: int
    start_capital: float
    end_capital: float
    profit_usd: float
    profit_pct: float
    max_T_reached: float
    max_shares_held: int


class InfiniteBacktester:
    """Historical Simulation Engine for Infinite Buying Method V4.0."""

    def __init__(
        self,
        ticker: str = "SOXL",
        divisions: int = 40,
        target_profit_rate: float = 0.20,
        initial_capital: float = 10000.0,
    ):
        self.ticker = ticker
        self.divisions = divisions
        self.target_profit_rate = target_profit_rate
        self.initial_capital = initial_capital

    def run(self, df_ohlcv: pd.DataFrame) -> Dict[str, Any]:
        """Run daily bar-by-bar simulation.
        
        df_ohlcv must have columns: Open, High, Low, Close, Date or Datetime index.
        """
        df = df_ohlcv.sort_index().copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        
        agent = InfiniteStrategyAgent(
            StrategyState(
                ticker=self.ticker,
                divisions=self.divisions,
                target_profit_rate=self.target_profit_rate,
                remaining_cash=self.initial_capital,
                initial_budget=self.initial_capital,
            )
        )

        completed_cycles: List[BacktestCycleResult] = []
        equity_curve: List[Tuple[str, float]] = []
        cycle_start_date = str(df.index[0])[:10]
        cycle_start_capital = self.initial_capital
        max_T_in_cycle = 0.0
        max_shares_in_cycle = 0

        for i in range(1, len(df)):
            current_date_str = str(df.index[i])[:10]
            val_prev_close = df["Close"].iloc[i - 1]
            prev_close = float(val_prev_close.item() if hasattr(val_prev_close, "item") else val_prev_close)

            val_high = df["High"].iloc[i]
            curr_high = float(val_high.item() if hasattr(val_high, "item") else val_high)

            val_low = df["Low"].iloc[i]
            curr_low = float(val_low.item() if hasattr(val_low, "item") else val_low)

            val_close = df["Close"].iloc[i]
            curr_close = float(val_close.item() if hasattr(val_close, "item") else val_close)

            raw_5d = df["Close"].iloc[max(0, i - 5):i].values
            recent_5d = [float(x.item() if hasattr(x, "item") else x) for x in raw_5d]

            # Generate orders for today
            order_plan = agent.generate_daily_orders(
                prev_close=prev_close,
                recent_5d_closes=recent_5d,
                date_str=current_date_str,
            )

            max_T_in_cycle = max(max_T_in_cycle, agent.state.T)
            max_shares_in_cycle = max(max_shares_in_cycle, agent.state.total_shares)

            # Simulate execution against curr_high, curr_low, curr_close
            filled_buys: List[Tuple[float, int, str]] = []
            filled_sells: List[Tuple[float, int, str]] = []

            for o in order_plan.orders:
                if o.side == OrderSide.SELL:
                    if o.order_type == OrderType.LIMIT:
                        # Intraday limit sell fills if high reaches limit price
                        if curr_high >= o.price:
                            filled_sells.append((o.price, o.quantity, o.tag))
                    elif o.order_type == OrderType.LOC:
                        # LOC sell fills at closing price if close >= order price
                        if curr_close >= o.price:
                            filled_sells.append((curr_close, o.quantity, o.tag))
                    elif o.order_type == OrderType.MARKET:
                        filled_sells.append((curr_close, o.quantity, o.tag))

                elif o.side == OrderSide.BUY:
                    if o.order_type == OrderType.LOC:
                        # LOC buy fills at closing price if close <= order price
                        if curr_close <= o.price:
                            # Check available cash
                            cost = curr_close * o.quantity
                            if agent.state.remaining_cash >= cost:
                                filled_buys.append((curr_close, o.quantity, o.tag))

            # Apply executions
            exec_res = agent.apply_execution(filled_buys, filled_sells)

            # Track equity
            eval_equity = agent.state.remaining_cash + (agent.state.total_shares * curr_close)
            equity_curve.append((current_date_str, round(eval_equity, 2)))

            # If cycle finished
            if exec_res["cycle_completed"]:
                cycle_end_capital = agent.state.remaining_cash
                profit_usd = cycle_end_capital - cycle_start_capital
                profit_pct = (profit_usd / cycle_start_capital) * 100.0

                completed_cycles.append(
                    BacktestCycleResult(
                        cycle_num=agent.state.cycle - 1,
                        start_date=cycle_start_date,
                        end_date=current_date_str,
                        duration_days=0, # Computed later
                        start_capital=round(cycle_start_capital, 2),
                        end_capital=round(cycle_end_capital, 2),
                        profit_usd=round(profit_usd, 2),
                        profit_pct=round(profit_pct, 2),
                        max_T_reached=round(max_T_in_cycle, 2),
                        max_shares_held=max_shares_in_cycle,
                    )
                )

                # Reset for next cycle
                cycle_start_date = current_date_str
                cycle_start_capital = cycle_end_capital
                max_T_in_cycle = 0.0
                max_shares_in_cycle = 0

        # Compute summary metrics
        final_equity = equity_curve[-1][1] if equity_curve else self.initial_capital
        total_return_pct = ((final_equity - self.initial_capital) / self.initial_capital) * 100.0
        
        # Max Drawdown
        equities = np.array([eq for _, eq in equity_curve])
        peak = np.maximum.accumulate(equities)
        drawdowns = (equities - peak) / peak
        max_drawdown_pct = float(np.min(drawdowns) * 100.0) if len(drawdowns) > 0 else 0.0

        return {
            "initial_capital": self.initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return_pct, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "total_cycles_completed": len(completed_cycles),
            "cycles": completed_cycles,
            "active_cycle_state": agent.state.model_dump(),
        }
