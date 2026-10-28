import pytest

from agents.agent1_journal import DailyExecutionRecord
from agents.agent2_strategy import InfiniteStrategyAgent, StrategyState


@pytest.mark.parametrize("sale_price, capital", [(120, 32000), (80, 28000), (100, 30000)])
def test_completed_cycle_reinvests_profit_and_loss(sale_price, capital):
    # 원금 30,000 = 미사용 현금 20,000 + 100달러에 산 100주.
    agent = InfiniteStrategyAgent(StrategyState(
        initial_budget=30000, remaining_cash=20000,
        total_shares=100, avg_price=100, T=20,
    ))
    result = agent.apply_execution([], [(sale_price, 100, "TARGET_SELL_LIMIT")])
    assert result["cycle_completed"]
    assert agent.state.cycle == 2
    assert agent.state.T == 0
    assert agent.state.initial_budget == capital
    assert agent.state.remaining_cash == capital
    assert agent.calculate_one_unit_cash() == capital / 40


def test_quarter_sale_profit_is_included_at_final_close():
    agent = InfiniteStrategyAgent(StrategyState(
        initial_budget=30000, remaining_cash=20000,
        total_shares=100, avg_price=100, T=20,
    ))
    agent.apply_execution([], [(100, 25, "QUARTER_SELL_LOC")])
    assert agent.state.initial_budget == 30000
    assert agent.state.cycle == 1
    agent.apply_execution([], [(120, 75, "TARGET_SELL_LIMIT")])
    # 20,000 + 2,500 + 9,000 = 31,500. 손익을 다시 더하지 않습니다.
    assert agent.state.initial_budget == 31500
    assert agent.state.remaining_cash == 31500
    agent.apply_execution([], [])
    assert agent.state.initial_budget == 31500
    assert agent.state.cycle == 2


def test_new_cycle_buy_uses_reinvested_capital():
    agent = InfiniteStrategyAgent(StrategyState(
        initial_budget=30000, remaining_cash=20000,
        total_shares=100, avg_price=100, T=20,
    ))
    agent.apply_execution([(80, 10, "INITIAL_BUY")], [(120, 100, "TARGET_SELL_LIMIT")])
    assert agent.state.initial_budget == 32000
    assert agent.state.remaining_cash == 31200
    assert agent.state.T == 1


def test_journal_retains_cycle_capital():
    record = DailyExecutionRecord(
        date="2026-10-28", cycle=2, ticker="SOXL", prev_close=100,
        current_close=100, T_before=0, T_after=0, mode="NORMAL",
        total_shares_before=0, total_shares_after=0,
        avg_price_before=0, avg_price_after=0,
        remaining_cash_before=32000, remaining_cash_after=32000,
        initial_budget=32000,
    )
    assert DailyExecutionRecord.model_validate_json(record.model_dump_json()).initial_budget == 32000
