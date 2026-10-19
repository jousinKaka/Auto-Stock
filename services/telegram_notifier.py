"""Telegram Notification Service for Auto Stock.

Sends daily morning execution briefings, order sheets, and risk warnings
directly to the user's Telegram chat.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
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
        risk_headline: str = "",
        risk_level: str = "",
        soxx_price: float = 0.0,
        soxx_rsi: float = 0.0,
        macro_regime: str = "",
    ) -> bool:
        """Format and send the daily morning briefing."""
        if not self.is_configured:
            return False

        pnl_icon = "🟢" if record.unrealized_pnl >= 0 else "🔴"
        risk_icon = {"GREEN": "🟢", "YELLOW": "🟡", "ORANGE": "🟠", "RED": "🔴"}.get(risk_level, "⚪")

        # 1. Header & Overnight Execution
        msg_lines = [
            f"🚀 <b>[SOXL 무한매수 V4.0] 09:00 AM 일일 리포트</b>",
            f"📅 <b>기준일자</b>: {record.date} | <b>회차</b>: #{record.cycle} | <b>모드</b>: {record.mode}",
            "",
            f"<b>💼 어제까지의 결제 & 계좌 현황</b>",
            f"• <b>진행도 (T값)</b>: <code>{record.T_after:.2f} / 40</code> ({'전반전' if record.T_after < 20 else '후반전'})",
            f"• <b>보유 주식</b>: <b>{record.total_shares_after}주</b> (평단가: <b>${record.avg_price_after:.2f}</b>)",
            f"• <b>현재가</b>: <b>${record.current_close:.2f}</b> ({((record.current_close - record.prev_close)/record.prev_close)*100:+.2f}%)",
            f"• <b>평가 손익</b>: {pnl_icon} <b>${record.unrealized_pnl:+.2f} ({record.unrealized_return_pct:+.2f}%)</b>",
            f"• <b>남은 현금</b>: <b>${record.remaining_cash_after:,.2f}</b>",
            f"• <b>총 자산 가치</b>: <b>${record.total_equity:,.2f}</b>",
        ]

        if record.bought_shares > 0 or record.sold_shares > 0:
            msg_lines.extend([
                "",
                f"<b>⚡ 어제 야간 체결 결과</b>",
                f"• 매수 체결: +{record.bought_shares}주 (${record.bought_amount:.2f})" if record.bought_shares > 0 else "",
                f"• 매도 체결: -{record.sold_shares}주 (실현손익: ${record.realized_pnl:+.2f})" if record.sold_shares > 0 else "",
            ])
            msg_lines = [line for line in msg_lines if line]

        # 2. Today's Order Sheet
        msg_lines.extend([
            "",
            f"<b>🎯 오늘의 To-Do 주문표 (1회 매수금: ${order_plan.one_unit_cash:.2f})</b>",
            f"• <b>별지점</b>: ${order_plan.star_price:.2f} | <b>익절목표가</b>: ${order_plan.target_sell_price:.2f}",
            "",
        ])

        for o in order_plan.orders:
            side_badge = "🔴 매수" if o.side == OrderSide.BUY else "🔵 매도"
            msg_lines.append(
                f"{side_badge} <code>{o.order_type.value:5s}</code> | <b>${o.price:6.2f}</b> | <b>{o.quantity:2d}주</b> ({o.tag})"
            )

        # 3. Risk & Market Pulse
        msg_lines.extend([
            "",
            f"<b>🛡️ 시장 환경 & 리스크 진단</b>",
            f"• <b>위험 등급</b>: {risk_icon} <b>[{risk_level}]</b> {risk_headline}",
            f"• <b>반도체 지수(SOXX)</b>: ${soxx_price:.2f} (RSI: {soxx_rsi:.1f})",
            f"• <b>거시 매크로</b>: {macro_regime}",
            "",
            f"<i>*Auto Stock Multi-Agent System 자동 발송*</i>",
        ])

        return self.send_message("\n".join(msg_lines), parse_mode="HTML")

