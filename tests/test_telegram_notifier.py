from agents.agent1_journal import DailyExecutionRecord
from agents.agent2_strategy import OrderLine, OrderPlan, OrderSide, OrderType, StrategyState
from services.telegram_notifier import TelegramNotifier
from services.telegram_notifier import _analysis_messages
from services.telegram_notifier import previous_account_record


def test_account_changes_keep_price_on_previous_close_basis():
    previous = {
        "T_after": 21.4176838, "total_shares_after": 140,
        "avg_price_after": 114.1407, "current_close": 94.15,
        "unrealized_pnl": -2798.70, "unrealized_return_pct": -17.51,
        "remaining_cash_after": 13608.42, "broker_soxl_shares": 140,
        "broker_cash_usd": 13608.42, "total_equity": 26789.42,
    }
    record = _sample_record().model_copy(update={
        "T_after": 22.3838603, "total_shares_after": 147,
        "avg_price_after": 113.5188, "prev_close": 90.50, "current_close": 100.65,
        "unrealized_pnl": -1891.71, "unrealized_return_pct": -11.34,
        "remaining_cash_after": 12900.16, "broker_soxl_shares": 147,
        "broker_cash_usd": 12900.16, "total_equity": 27695.71,
    })
    notifier = TelegramNotifier(bot_token="test", chat_id="test")
    messages = []
    notifier.send_message = lambda text, parse_mode="HTML": messages.append(text) or True
    try:
        assert notifier.send_morning_briefing(record, _sample_plan(), previous_record=previous)
    finally:
        notifier.http_client.close()
    text = messages[0]
    for expected in ["(+0.97)", "147주 (+7주)", "$113.52 (−$0.62)",
                     "$100.65 (+$10.15 · +11.22%)", "+$906.99 · 수익률 +6.17%p",
                     "$12,900.16 (−$708.26)", "$27,695.71 (+$906.29 · +3.38%)"]:
        assert expected in text
    assert "전일 대비" not in text
    assert "<i>" not in text
    assert "미리보기" not in text


def test_previous_record_skips_same_day_other_ticker_and_handles_weekend():
    friday = {"date": "2026-10-23", "ticker": "SOXL"}
    rows = [friday, {"date": "2026-10-26", "ticker": "SOXL"},
            {"date": "2026-10-24", "ticker": "TQQQ"}]
    assert previous_account_record(rows, "2026-10-26", "SOXL") == friday
    assert previous_account_record([], "2026-10-26", "SOXL") is None


def test_missing_history_and_zero_previous_close_do_not_invent_deltas():
    notifier = TelegramNotifier(bot_token="test", chat_id="test")
    messages = []
    notifier.send_message = lambda text, parse_mode="HTML": messages.append(text) or True
    try:
        assert notifier.send_morning_briefing(
            _sample_record().model_copy(update={"prev_close": 0}), _sample_plan(),
            previous_record={"total_equity": 0},
        )
    finally:
        notifier.http_client.close()
    assert "nan" not in messages[0]
    assert "(+1.00%" not in messages[0]
    assert "수익률" not in messages[0]


def test_actual_company_report_is_mobile_card_without_markdown():
    from agents.agent6_fundamental import SOXLConstituentsAnalysis, ConstituentStockInfo

    report = SOXLConstituentsAnalysis(
        timestamp="2026-10-28", companies=[ConstituentStockInfo(
            symbol="NVDA", company_name="엔비디아", current_price=216,
            change_1d_pct=2.39, next_earnings_date="2026-11-18",
            consensus_eps=2.47, consensus_revenue=108980000000,
            key_metrics_to_watch="데이터센터 매출 성장, 가속기 수요",
        )],
    )
    text = "\n".join(_analysis_messages("2026-10-28", [("기업·실적", report.to_markdown())]))
    assert "<b>NVDA · 엔비디아</b>\n💵 $216.00  (+2.39%)" in text
    assert "📅 실적 발표: 2026-11-18" in text
    assert "예상 EPS $2.47 · 예상 매출 $108.98B" in text
    assert '<a href="https://finance.yahoo.com/quote/NVDA/">Yahoo Finance</a>' in text
    assert all(marker not in text for marker in ["|", "`", "**", "[Yahoo Finance]("])


def test_analysis_formats_tables_as_cards_and_escapes_html():
    summary = "\n".join([
        '- **기준 거래일**: 2026-10-21',
        '| 종목 | 기업 | 주가($) | 출처 |',
        '|:---|:---|---:|:---|',
        '| `NVDA` | 엔비디아 | $216.47 | [Yahoo Finance](https://finance.yahoo.com/quote/NVDA/) |',
        '주의: <위험> & 확인',
    ])
    message = _analysis_messages('2026-10-22', [('기업 분석', summary)])[0]
    assert '• <b>기준 거래일</b>: 2026-10-21' in message
    assert '<b>NVDA · 엔비디아</b>\n• <b>주가($)</b>: $216.47' in message
    assert '<a href="https://finance.yahoo.com/quote/NVDA/">Yahoo Finance</a>' in message
    assert '&lt;위험&gt; &amp; 확인' in message
    assert '|' not in message
    assert '**' not in message
    assert '`' not in message


def test_long_analysis_keeps_cards_and_html_intact_across_messages():
    summary = '\n'.join([
        '| 지표 | 현재값 | 해석 |',
        '|:---|---:|:---|',
        *[f'| 지표 {i} | {i}.00 | 관찰 & 확인 |' for i in range(100)],
    ])
    messages = _analysis_messages('2026-10-22', [('거시 매크로', summary)])
    assert len(messages) > 1
    assert all(len(message) <= 3800 for message in messages)
    for i in range(100):
        card = f'<b>지표 {i}</b>\n• <b>현재값</b>: {i}.00\n• <b>해석</b>: 관찰 &amp; 확인'
        assert sum(card in message for message in messages) == 1


def test_agent_sections_use_consistent_compact_spacing():
    summary = '- **기준일**: 2026-10-21\n\n- **요약**: 관찰'
    headings = ['거시 매크로', '반도체 업황', '주요 기업']
    messages = _analysis_messages('2026-10-22', [(heading, summary) for heading in headings])
    assert len(messages) == 1
    for heading in headings:
        assert (
            f'\n\n<b>{heading}</b>\n\n'
            '• <b>기준일</b>: 2026-10-21\n• <b>요약</b>: 관찰'
        ) in messages[0]
    assert '\n\n\n' not in messages[0]


def test_analysis_splits_dense_formatting_before_later_cards_lose_bold():
    import re

    symbols = ['NVDA', 'AVGO', 'AMD', 'QCOM', 'TSM', 'MU', 'AMAT', 'INTC']
    headers = '| 종목 | 기업 | ' + ' | '.join(f'항목{i}' for i in range(12)) + ' |'
    separator = '|' + '|'.join(['---'] * 14) + '|'
    rows = [f'| {symbol} | 기업 | ' + ' | '.join(['값'] * 12) + ' |' for symbol in symbols]
    messages = _analysis_messages('2026-10-22', [('기업', '\n'.join([headers, separator, *rows]))])
    assert len(messages) > 1
    assert all(len(re.findall(r'<(?:b|a)\b', message)) <= 80 for message in messages)
    for symbol in symbols:
        assert sum(f'<b>{symbol} · 기업</b>' in message for message in messages) == 1


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

    assert "매수 체결: <b>7주 · $690.55</b>" in sent_messages[0]
    assert "토스 실제 SOXL 보유량" in sent_messages[0]
    assert "<i>" not in sent_messages[0]


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
