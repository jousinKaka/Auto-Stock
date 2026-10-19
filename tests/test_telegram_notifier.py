from agents.agent1_journal import DailyExecutionRecord
from agents.agent2_strategy import OrderLine, OrderPlan, OrderSide, OrderType, StrategyState
from services.telegram_notifier import TelegramNotifier


def _sample_record() -> DailyExecutionRecord:
    return DailyExecutionRecord(
        date="2026-10-19",
        cycle=1,
        ticker="SOXL",
        prev_close=100.0,
        current_close=101.0,
        T_before=1.0,
        T_after=1.0,
        mode="NORMAL",
        total_shares_before=10,
        total_shares_after=10,
        avg_price_before=100.0,
        avg_price_after=100.0,
        remaining_cash_before=20000.0,
        remaining_cash_after=20000.0,
    )


def _sample_plan() -> OrderPlan:
    return OrderPlan(
        date="2026-10-19",
        ticker="SOXL",
        state_snapshot=StrategyState(),
        prev_close=100.0,
        one_unit_cash=500.0,
        star_pct=0.1,
        star_price=110.0,
        target_sell_price=120.0,
        orders=[
            OrderLine(
                side=OrderSide.BUY,
                order_type=OrderType.LOC,
                price=99.0,
                quantity=1,
                tag="TEST_BUY",
            )
        ],
    )


def test_briefing_title_and_order_section_match_routine_slot():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    sent_messages = []
    notifier.send_message = lambda text, parse_mode="HTML": sent_messages.append(text) or True
    try:
        for slot in ("morning", "evening"):
            assert notifier.send_morning_briefing(
                record=_sample_record(),
                order_plan=_sample_plan(),
                risk_level="GREEN",
                routine_slot=slot,
                live_order=slot == "evening",
                live_buy_only=slot == "evening",
            )
    finally:
        notifier.http_client.close()

    assert "09:07 KST 아침 루틴 (Dry-Run)" in sent_messages[0]
    assert "밤사이 체결 정산 및 계좌 현황" in sent_messages[0]
    assert "오늘의 To-Do 주문표" in sent_messages[0]
    assert "20:07 KST 저녁 루틴 (실거래 · 매수 전용)" in sent_messages[1]
    assert "현재 계좌 현황" in sent_messages[1]
    assert "오늘 저녁 주문 계획" in sent_messages[1]


def test_briefing_rejects_unknown_routine_slot():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    try:
        try:
            notifier.send_morning_briefing(
                record=_sample_record(),
                order_plan=_sample_plan(),
                routine_slot="night",
            )
        except ValueError as exc:
            assert "Unknown routine slot" in str(exc)
        else:
            raise AssertionError("An invalid routine slot should fail explicitly.")
    finally:
        notifier.http_client.close()
