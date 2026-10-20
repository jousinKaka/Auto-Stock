"""Telegram Notification Service for Auto Stock.

Sends daily morning execution briefings, order sheets, and risk warnings
directly to the user's Telegram chat.
"""

from __future__ import annotations

import logging
from html import escape
from typing import Optional
import httpx

from agents.agent1_journal import DailyExecutionRecord
from agents.agent2_strategy import OrderPlan, OrderSide
from config.settings import settings


class TelegramNotifier:
    """Sends rich notifications to Telegram via Bot API."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id
        self.http_client = httpx.Client(timeout=10.0)
        self.logger = logging.getLogger("TelegramNotifier")

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to Telegram. Splits long messages automatically."""
        if not self.is_configured:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        
        # Telegram character limit is 4096. Use 3800 for safety.
        max_chunk = 3800
        chunks = [text[i:i + max_chunk] for i in range(0, len(text), max_chunk)]

        success = True
        for chunk in chunks:
            try:
                resp = self.http_client.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                )
                if resp.status_code != 200:
                    self.logger.error(f"Telegram send failed [{resp.status_code}]: {resp.text}")
                    success = False
            except Exception as e:
                self.logger.error(f"Telegram exception: {e}")
                success = False

        return success

    def send_morning_briefing(
        self,
        record: DailyExecutionRecord,
        order_plan: OrderPlan,
        routine_slot: str = "morning",
        live_order: bool = False,
        live_buy_only: bool = False,
        execution_summary: str = "",
        macro_summary: str = "",
        semi_summary: str = "",
        fundamental_summary: str = "",
    ) -> bool:
        """Send an evening order update or a morning settlement and analysis."""
        if not self.is_configured:
            return False

        if routine_slot not in {"morning", "evening"}:
            raise ValueError(f"Unknown routine slot: {routine_slot!r}")

        is_evening = routine_slot == "evening"
        time_label = "20:00 KST 저녁" if is_evening else "09:00 KST 아침"
        run_mode = (
            "실거래 · 매수 전용" if live_order and live_buy_only
            else "실거래" if live_order
            else "Dry-Run" if is_evening
            else "체결 정산·분석 (주문 없음)"
        )
        account_section = "현재 계좌 현황" if is_evening else "밤사이 체결 정산 및 계좌 현황"
        account_lines = [
            f"🚀 <b>[SOXL 무한매수 V4.0] {time_label} 루틴 ({run_mode})</b>",
            f"📅 <b>기준일자</b>: {record.date} | <b>회차</b>: #{record.cycle} | <b>모드</b>: {record.mode}",
            "",
            f"<b>💼 {account_section}</b>",
            f"• <b>진행도 (T값)</b>: <code>{record.T_after:.2f} / 40</code> ({'전반전' if record.T_after < 20 else '후반전'})",
            f"• <b>보유 주식</b>: <b>{record.total_shares_after}주</b> (평단가: <b>${record.avg_price_after:.2f}</b>)",
            f"• <b>현재가</b>: <b>${record.current_close:.2f}</b> ({((record.current_close - record.prev_close)/record.prev_close)*100:+.2f}%)",
            f"• <b>평가 손익</b>: <b>${record.unrealized_pnl:+.2f} ({record.unrealized_return_pct:+.2f}%)</b>",
            f"• <b>전략 계산용 현금</b>: <b>${record.remaining_cash_after:,.2f}</b>",
            f"• <b>총 자산 가치</b>: <b>${record.total_equity:,.2f}</b>",
        ]
        if record.broker_soxl_shares is not None:
            account_lines.insert(
                -1,
                f"• <b>토스 실제 SOXL 보유량</b>: <b>{record.broker_soxl_shares}주</b>",
            )
        if record.broker_cash_usd is not None:
            account_lines.insert(
                -1,
                f"• <b>토스 USD 매수 가능 금액</b>: <b>${record.broker_cash_usd:,.2f}</b>",
            )

        if not is_evening:
            account_lines.extend([
                "",
                "<b>⚡ 전날 미국장 체결 정산</b>",
            ])
            if record.bought_shares or record.sold_shares:
                if record.bought_shares:
                    account_lines.append(
                        f"• 매수 체결: +{record.bought_shares}주 (${record.bought_amount:.2f})"
                    )
                if record.sold_shares:
                    account_lines.append(
                        f"• 매도 체결: -{record.sold_shares}주 (${record.sold_amount:.2f})"
                    )
                account_lines.append(f"• 실현 손익: ${record.realized_pnl:+.2f}")
            else:
                account_lines.append("• 체결된 SOXL 주문이 없습니다.")
            account_lines.extend([
                "",
                "<i>*아침 정산·분석 메시지이며, 주문은 제출하지 않았습니다.*</i>",
            ])
            messages = [
                "\n".join(account_lines),
                "\n".join([
                    f"📊 <b>[SOXL 에이전트 분석] {record.date}</b>",
                    "",
                    f"<b>Agent 4 · 거시 매크로</b>\n{escape(macro_summary or '분석 요약 없음')}",
                    "",
                    f"<b>Agent 5 · 반도체 업황</b>\n{escape(semi_summary or '분석 요약 없음')}",
                    "",
                    f"<b>Agent 6 · 주요 기업·실적 일정</b>\n"
                    f"{escape(fundamental_summary or '분석 요약 없음')}",
                ]),
            ]
        else:
            order_section = "오늘 저녁 주문 계획"
            order_lines = [
                f"🎯 <b>{order_section}</b>",
                f"• <b>전략 주문 현금</b>: ${record.remaining_cash_after:,.2f}",
                f"• <b>1회 매수금</b>: ${order_plan.one_unit_cash:.2f}",
                f"• <b>별지점</b>: ${order_plan.star_price:.2f} | "
                f"<b>익절목표가</b>: ${order_plan.target_sell_price:.2f}",
                "",
            ]
            for order in order_plan.orders:
                side_badge = "🔴 매수" if order.side == OrderSide.BUY else "🔵 매도"
                order_lines.append(
                    f"{side_badge} <code>{order.order_type.value:5s}</code> | "
                    f"<b>${order.price:6.2f}</b> | <b>{order.quantity:2d}주</b> "
                    f"({escape(order.tag)})"
                )
            order_lines.extend([
                "",
                f"📨 <b>주문 처리 결과</b>: {escape(execution_summary or '결과 요약 없음')}",
                "<i>접수는 체결 확정을 뜻하지 않습니다. 실제 체결은 다음 날 아침에 정산합니다.</i>",
            ])
            messages = ["\n".join(account_lines + [""] + order_lines)]

        if any(len(message) > 3800 for message in messages):
            self.logger.error("Telegram routine briefing exceeds the safe message length; no briefing was sent.")
            return False

        success = True
        for message in messages:
            if not self.send_message(message, parse_mode="HTML"):
                success = False
        return success
