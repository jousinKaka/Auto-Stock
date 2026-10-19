import pytest
import pandas as pd
from agents.agent2_strategy import (
    InfiniteStrategyAgent,
    StrategyState,
    StrategyMode,
    OrderSide,
    OrderType,
    InfiniteBacktester,
)

def test_initial_first_day_orders():
    """Verify first day order generation for a fresh cycle."""
    state = StrategyState(
        ticker="SOXL",
        divisions=40,
        target_profit_rate=0.20,
        large_number_ratio=1.12,
        remaining_cash=10000.0,
        initial_budget=10000.0,
        T=0.0,
        avg_price=0.0,
        total_shares=0,
    )
    agent = InfiniteStrategyAgent(state)
    prev_close = 30.0

    plan = agent.generate_daily_orders(prev_close=prev_close, date_str="2026-10-19")
    
    # 1회 매수금: 10000 / 40 = 250.0
    assert plan.one_unit_cash == 250.0
    assert plan.star_pct == pytest.approx(0.20, rel=1e-3)
    
    # Verify orders
    buys = [o for o in plan.orders if o.side == OrderSide.BUY]
    sells = [o for o in plan.orders if o.side == OrderSide.SELL]
    assert len(sells) == 0  # No shares to sell
    
    # First buy order should be large number capped (30.0 * 1.12 = 33.6)
    first_buy = buys[0]
    assert first_buy.tag == "FIRST_BUY_LARGE_NUMBER"
    assert first_buy.price == 33.60
    assert first_buy.quantity == int(250.0 // 33.60)  # 7 shares
    
    # 7 ladder orders should follow
    ladder_orders = buys[1:]
    assert len(ladder_orders) == 7
    for o in ladder_orders:
        assert "LADDER_BUY" in o.tag
        assert o.quantity == 1
        assert o.price < 33.60


def test_first_half_orders_and_quarter_sell():
    """Verify first half split buy (star buy + avg buy) and quarter sell."""
    state = StrategyState(
        ticker="SOXL",
        divisions=40,
        target_profit_rate=0.20,
        T=5.0,  # First half
        avg_price=30.0,
        total_shares=40,
        remaining_cash=8750.0,
    )
    agent = InfiniteStrategyAgent(state)
    prev_close = 31.0

    plan = agent.generate_daily_orders(prev_close=prev_close, date_str="2026-10-19")
    
    # 별% = 0.20 * (1 - (2 * 5 / 40)) = 0.20 * 0.75 = 0.15 (+15%)
    # 별지점 = 30.0 * 1.15 = 34.50
    assert plan.star_pct == pytest.approx(0.15, rel=1e-3)
    assert plan.star_price == 34.50
    # 익절목표가 = 30.0 * 1.20 = 36.00
    assert plan.target_sell_price == 36.00

    # Sell orders:
    # 쿼터매도: 40 // 4 = 10주 @ 34.50 LOC
    # 지정가매도: 40 - 10 = 30주 @ 36.00 LIMIT
    quarter_sells = [o for o in plan.orders if o.tag == "QUARTER_SELL_LOC"]
    target_sells = [o for o in plan.orders if o.tag == "TARGET_SELL_LIMIT"]
    assert len(quarter_sells) == 1
    assert quarter_sells[0].quantity == 10
    assert quarter_sells[0].price == 34.50
    
    assert len(target_sells) == 1
    assert target_sells[0].quantity == 30
    assert target_sells[0].price == 36.00


def test_second_half_orders():
    """Verify second half (T >= 20) single star buy order."""
    state = StrategyState(
        ticker="SOXL",
        divisions=40,
        target_profit_rate=0.20,
        T=22.0,  # Second half
        avg_price=30.0,
        total_shares=150,
        remaining_cash=4500.0,
    )
    agent = InfiniteStrategyAgent(state)
    prev_close = 25.0

    plan = agent.generate_daily_orders(prev_close=prev_close, date_str="2026-10-19")
    
    # T=22 -> 별% = 0.20 * (1 - (44/40)) = 0.20 * (-0.1) = -0.02 (-2%)
    assert plan.star_pct == pytest.approx(-0.02, rel=1e-3)
    assert plan.star_price == 29.40
    
    # With prev_close = 25.0, large_number_cap is 25.0 * 1.12 = 28.0.
    # Since star_buy_price (29.39) > 28.0, it should be capped at 28.0!
    star_buys = [o for o in plan.orders if o.tag == "SECOND_HALF_STAR_BUY"]
    assert len(star_buys) == 1
    assert star_buys[0].price == 28.00  # Capped at large_number_cap

    # Now test with prev_close = 28.0, where 28.0 * 1.12 = 31.36 > 29.39 (un-capped)
    plan_uncapped = agent.generate_daily_orders(prev_close=28.0, date_str="2026-10-19")
    star_buys_uncapped = [o for o in plan_uncapped.orders if o.tag == "SECOND_HALF_STAR_BUY"]
    assert len(star_buys_uncapped) == 1
    assert star_buys_uncapped[0].price == 29.39  # star_price - 0.01 un-capped


def test_execution_application_and_cycle_completion():
    """Verify state transitions and cycle reset upon full liquidation."""
    state = StrategyState(
        ticker="SOXL",
        divisions=40,
        target_profit_rate=0.20,
        T=4.0,
        avg_price=30.0,
        total_shares=30,
        remaining_cash=9000.0,
    )
    agent = InfiniteStrategyAgent(state)

    # 1. Simulate a buy fill
    res_buy = agent.apply_execution(
        filled_buys=[(28.0, 10, "STAR_BUY_LOC")],
        filled_sells=[]
    )
    assert res_buy["new_total_shares"] == 40
    # New avg price: (30 * 30 + 28 * 10) / 40 = 1180 / 40 = 29.50
    assert res_buy["new_avg_price"] == 29.50
    assert agent.state.T > 4.0

    # 2. Simulate complete liquidation (both target sell and quarter sell fill)
    res_sell = agent.apply_execution(
        filled_buys=[],
        filled_sells=[(35.40, 30, "TARGET_SELL_LIMIT"), (32.00, 10, "QUARTER_SELL_LOC")]
    )
    assert res_sell["cycle_completed"] is True
    assert agent.state.cycle == 2
    assert agent.state.T == 0.0
    assert agent.state.total_shares == 0
    assert agent.state.avg_price == 0.0
    assert agent.state.remaining_cash > 9000.0


def test_infinite_backtester_synthetic():
    """Verify backtester executes correctly over simulated price series."""
    dates = pd.date_range("2026-01-01", periods=60, freq="B")
    
    # Create price curve: drops first, then rallies 30% to trigger target sell
    closes = []
    price = 30.0
    for i in range(60):
        if i < 20:
            price *= 0.99  # Gentle dip
        else:
            price *= 1.015 # Rally
        closes.append(price)

    df = pd.DataFrame(
        {
            "Open": [c * 0.99 for c in closes],
            "High": [c * 1.02 for c in closes],
            "Low": [c * 0.98 for c in closes],
            "Close": closes,
            "Volume": [1000000] * 60,
        },
        index=dates,
    )

    backtester = InfiniteBacktester(ticker="SOXL", divisions=40, target_profit_rate=0.20, initial_capital=10000.0)
    result = backtester.run(df)

    assert result["initial_capital"] == 10000.0
    assert "total_return_pct" in result
    assert "max_drawdown_pct" in result
    assert result["total_cycles_completed"] >= 1
