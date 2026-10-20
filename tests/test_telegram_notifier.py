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
        assert notifier.send_morning_briefing(
            record=_sample_record(),
            order_plan=_sample_plan(),
            routine_slot="morning",
            macro_summary="금리·달러 요약",
            semi_summary="SOXX 업황 요약",
            fundamental_summary="NVDA 실적 요약",
        )
        assert notifier.send_morning_briefing(
            record=_sample_record(),
            order_plan=_sample_plan(),
            routine_slot="evening",
            live_order=True,
            execution_summary="실거래 총 1건 접수 완료, 0건 실패.",
        )
    finally:
        notifier.http_client.close()

    assert len(sent_messages) == 3
    assert "09:00 KST 아침 루틴 (체결 정산·분석 (주문 없음))" in sent_messages[0]
    assert "밤사이 체결 정산 및 계좌 현황" in sent_messages[0]
    assert "체결된 SOXL 주문이 없습니다" in sent_messages[0]
    assert "주문표" not in sent_messages[0]
    assert "금리·달러 요약" in sent_messages[1]
    assert "SOXX 업황 요약" in sent_messages[1]
    assert "NVDA 실적 요약" in sent_messages[1]
    assert "주문 가격" not in sent_messages[1]
    assert "20:00 KST 저녁 루틴 (실거래)" in sent_messages[2]
    assert "현재 계좌 현황" in sent_messages[2]
    assert "오늘 저녁 주문 계획" in sent_messages[2]
    assert "실거래 총 1건 접수 완료, 0건 실패." in sent_messages[2]
    assert "실제 체결은 다음 날 아침에 정산합니다" in sent_messages[2]


def test_morning_briefing_includes_fills_and_separates_real_holdings():
    record = _sample_record().model_copy(update={
        "bought_shares": 7,
        "bought_amount": 690.55,
        "broker_soxl_shares": 7,
    })
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    sent_messages = []
    notifier.send_message = lambda text, parse_mode="HTML": sent_messages.append(text) or True
    try:
        assert notifier.send_morning_briefing(
            record=record,
            order_plan=_sample_plan(),
            routine_slot="morning",
        )
    finally:
        notifier.http_client.close()

    assert "매수 체결: +7주 ($690.55)" in sent_messages[0]
    assert "토스 실제 SOXL 보유량" in sent_messages[0]
    assert "아침 정산·분석 메시지이며, 주문은 제출하지 않았습니다." in sent_messages[0]


def test_evening_briefing_rejects_oversized_messages_without_sending():
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    sent_messages = []
    notifier.send_message = lambda text, parse_mode="HTML": sent_messages.append(text) or True
    try:
        success = notifier.send_morning_briefing(
            record=_sample_record(),
            order_plan=_sample_plan(),
            routine_slot="evening",
            execution_summary="x" * 4000,
        )
    finally:
        notifier.http_client.close()

    assert success is False
    assert sent_messages == []


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
