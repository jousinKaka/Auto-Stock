"""Telegram Notification Service for Auto Stock.

Sends daily morning execution briefings, order sheets, and risk warnings
directly to the user's Telegram chat.
"""

from __future__ import annotations

import logging
import math
import re
from html import escape
from typing import Optional
import httpx

from agents.agent1_journal import DailyExecutionRecord
from agents.agent2_strategy import OrderPlan, OrderSide
from config.settings import settings


def previous_account_record(history: list[dict], date: str, ticker: str) -> Optional[dict]:
    """같은 날 기록을 제외하고 같은 종목의 직전 저장 날짜를 선택합니다."""
    candidates = [row for row in history if row.get("ticker") == ticker and row.get("date", "") < date]
    return max(candidates, key=lambda row: row["date"], default=None)


def _money(value: float, signed: bool = False) -> str:
    prefix = "−" if value < 0 else "+" if signed else ""
    return f"{prefix}${abs(value):,.2f}"


def _account_change(previous: Optional[dict], key: str, value: float, kind="money", percent=False) -> str:
    before = previous.get(key) if previous else None
    if before is None or not math.isfinite(float(before)) or not math.isfinite(value):
        return ""
    change = value - float(before)
    label = f"{change:+.0f}주" if kind == "shares" else f"{change:+.2f}" if kind == "number" else _money(change, signed=True)
    if percent and float(before) > 0:
        label += f" · {change / float(before) * 100:+.2f}%"
    return f" ({label})"


def _telegram_inline(text: str) -> str:
    """분석 Markdown의 인라인 표현을 안전한 Telegram HTML로 바꿉니다."""
    pattern = r"\[([^\]]+)\]\((https?://[^\s)]+)\)|\*\*(.+?)\*\*|`([^`]+)`"
    parts = []
    cursor = 0
    for match in re.finditer(pattern, text):
        parts.append(escape(text[cursor:match.start()]))
        label, url, bold, code = match.groups()
        if url:
            parts.append(f'<a href="{escape(url, quote=True)}">{escape(label)}</a>')
        elif bold is not None:
            parts.append(f"<b>{escape(bold)}</b>")
        else:
            parts.append(escape(code))
        cursor = match.end()
    parts.append(escape(text[cursor:]))
    return "".join(parts)


def _analysis_blocks(summary: str) -> list[str]:
    """표를 항목별 카드로 펼쳐 모바일에서 읽을 수 있게 만듭니다."""
    blocks = []
    headers = []
    summary_lines = []

    def flush_summary() -> None:
        if summary_lines:
            blocks.append("\n".join(summary_lines))
            summary_lines.clear()

    for line in (summary or "분석 요약 없음").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("|") and line.endswith("|"):
            flush_summary()
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                continue
            if not headers:
                headers = cells
                continue
            if len(cells) == len(headers):
                title = cells[0].replace("`", "")
                first_value = 1
                if headers[:2] == ["종목", "기업"]:
                    title += f" · {cells[1]}"
                    first_value = 2
                if headers[:2] == ["종목", "기업"] and "다음 실적일" in headers:
                    values = dict(zip(headers, cells))
                    rows = [f"<b>{escape(title)}</b>"]
                    rows.append(
                        f"💵 {_telegram_inline(values.get('주가($)', 'N/A'))}  "
                        f"({_telegram_inline(values.get('1일 등락', 'N/A'))})"
                    )
                    rows.append(f"📅 실적 발표: {_telegram_inline(values['다음 실적일'])}")
                    rows.append(
                        f"예상 EPS {_telegram_inline(values.get('EPS 컨센서스', 'N/A'))} · "
                        f"예상 매출 {_telegram_inline(values.get('매출 컨센서스', 'N/A'))}"
                    )
                    for label, prefix in [("실적 체크 지표", "체크"), ("데이터 참고", "참고")]:
                        if values.get(label) not in (None, "", "-"):
                            rows.append(f"{prefix}: {_telegram_inline(values[label])}")
                    if values.get("출처"):
                        rows.append(_telegram_inline(values["출처"]))
                    blocks.append("\n".join(rows))
                    continue
                rows = [f"<b>{escape(title)}</b>"]
                rows.extend(
                    f"• <b>{escape(label)}</b>: {_telegram_inline(value)}"
                    for label, value in zip(headers[first_value:], cells[first_value:])
                    if value and value != "-"
                )
                blocks.append("\n".join(rows))
                continue
        else:
            headers = []
        if line.startswith("- "):
            line = "• " + line[2:]
        else:
            flush_summary()
        # 긴 원문도 HTML 태그나 엔티티 중간에서 자르지 않습니다.
        for i in range(0, len(line), 900):
            rendered = _telegram_inline(line[i:i + 900])
            if line.startswith("• "):
                if sum(len(item) + 1 for item in summary_lines) + len(rendered) > 2400:
                    flush_summary()
                summary_lines.append(rendered)
            else:
                blocks.append(rendered)
    flush_summary()
    return blocks


def _analysis_messages(date: str, summaries: list[tuple[str, str]]) -> list[str]:
    title = f"📊 <b>[SOXL 에이전트 분석] {escape(date)}</b>"
    messages = []
    current = title
    for heading, summary in summaries:
        section = f"<b>{heading}</b>"
        for index, block in enumerate(_analysis_blocks(summary)):
            addition = f"{section}\n\n{block}" if index == 0 else block
            # 글자 수와 별도로 서식 개수에도 여유를 두어 후반부 강조 누락을 막습니다.
            entity_count = len(re.findall(r"<(?:b|a)\b", current + addition))
            if len(current) + len(addition) + 2 > 3800 or entity_count > 80:
                messages.append(current)
                current = f"{title}\n\n{section} (계속)\n\n{block}"
            else:
                current += "\n\n" + addition
    messages.append(current)
    return messages


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
        previous_record: Optional[dict] = None,
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
        def change(key, value, kind="money", percent=False):
            return _account_change(previous_record, key, value, kind, percent)

        pnl_change = change("unrealized_pnl", record.unrealized_pnl)
        if pnl_change and previous_record.get("unrealized_return_pct") is not None:
            points = record.unrealized_return_pct - previous_record["unrealized_return_pct"]
            pnl_change = pnl_change[:-1] + f" · 수익률 {points:+.2f}%p)"
        price_change = ""
        if record.prev_close > 0:
            difference = record.current_close - record.prev_close
            price_change = f" ({_money(difference, signed=True)} · {difference / record.prev_close * 100:+.2f}%)"
        divisions = order_plan.state_snapshot.divisions
        account_lines = [
            f"🚀 <b>[SOXL 무한매수 V4.0] {time_label} 루틴 ({run_mode})</b>",
            f"📅 <b>기준일자</b>: {record.date} | <b>회차</b>: #{record.cycle} | <b>모드</b>: {record.mode}",
            "",
            f"<b>💼 {account_section}</b>",
            f"• <b>진행도 (T값)</b>: <code>{record.T_after:.2f} / {divisions}</code> · {'전반전' if record.T_after < divisions / 2 else '후반전'}<b>{change('T_after', record.T_after, 'number')}</b>",
            f"• <b>보유 주식</b>: <b>{record.total_shares_after}주{change('total_shares_after', record.total_shares_after, 'shares')}</b>",
            f"• <b>평단가</b>: <b>{_money(record.avg_price_after)}{change('avg_price_after', record.avg_price_after)}</b>",
            f"• <b>현재가</b>: <b>{_money(record.current_close)}{price_change}</b>",
            f"• <b>평가 손익</b>: <b>{_money(record.unrealized_pnl)} · {record.unrealized_return_pct:+.2f}%{pnl_change}</b>",
            f"• <b>전략 계산용 현금</b>: <b>{_money(record.remaining_cash_after)}{change('remaining_cash_after', record.remaining_cash_after)}</b>",
            f"• <b>총 자산 가치</b>: <b>{_money(record.total_equity)}{change('total_equity', record.total_equity, percent=True)}</b>",
        ]
        if record.broker_soxl_shares is not None:
            account_lines.insert(
                -1,
                f"• <b>토스 실제 SOXL 보유량</b>: <b>{record.broker_soxl_shares}주{change('broker_soxl_shares', record.broker_soxl_shares, 'shares')}</b>",
            )
        if record.broker_cash_usd is not None:
            account_lines.insert(
                -1,
                f"• <b>토스 USD 매수 가능 금액</b>: <b>{_money(record.broker_cash_usd)}{change('broker_cash_usd', record.broker_cash_usd)}</b>",
            )

        if not is_evening:
            account_lines.extend([
                "",
                "<b>⚡ 전날 미국장 체결 정산</b>",
            ])
            if record.bought_shares or record.sold_shares:
                if record.bought_shares:
                    account_lines.append(
                        f"• 매수 체결: <b>{record.bought_shares}주 · {_money(record.bought_amount)}</b>"
                    )
                if record.sold_shares:
                    account_lines.append(
                        f"• 매도 체결: <b>{record.sold_shares}주 · {_money(record.sold_amount)}</b>"
                    )
                account_lines.append(f"• 실현 손익: <b>{_money(record.realized_pnl)}</b>")
            else:
                account_lines.append("• 체결된 SOXL 주문이 없습니다.")
            messages = [
                "\n".join(account_lines),
                *_analysis_messages(record.date, [
                    ("🌐 거시 매크로 · Agent 4", macro_summary),
                    ("💾 반도체 업황 · Agent 5", semi_summary),
                    ("🏢 주요 기업·실적 일정 · Agent 6", fundamental_summary),
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
